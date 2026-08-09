"""Scheduled A-share scanning and on-demand QQ group delivery."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from zoneinfo import ZoneInfo

import botpy
import psycopg
from botpy.message import GroupMessage

from quant_trade.qq_bot import QQBotClient, QQBotError
from quant_trade.rsi50 import Direction, Rsi50Config
from quant_trade.scanner import (
    DatabaseSettings,
    MarketDataStatus,
    read_market_data_status,
    render_signal_chart,
    render_signal_sheet,
    scan_database_latest,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DeliveryImage:
    """One combined image and the symbols displayed in it."""

    path: Path
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class PreparedDelivery:
    """A completed daily scan ready for immediate QQ delivery."""

    scan_date: date
    summary: str
    images: tuple[DeliveryImage, ...]


def save_prepared_delivery(delivery: PreparedDelivery, manifest: Path) -> None:
    """Persist a prepared scan so Supervisor restarts can send it."""
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scan_date": delivery.scan_date.isoformat(),
        "summary": delivery.summary,
        "images": [
            {
                "path": str(image.path.resolve()),
                "symbols": list(image.symbols),
            }
            for image in delivery.images
        ],
    }
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(manifest)


def load_prepared_delivery(manifest: Path) -> PreparedDelivery | None:
    """Load the most recent prepared scan, returning None when unavailable."""
    if not manifest.exists():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        images = tuple(
            DeliveryImage(
                path=Path(item["path"]),
                symbols=tuple(str(symbol) for symbol in item["symbols"]),
            )
            for item in payload["images"]
        )
        if any(not image.path.exists() for image in images):
            return None
        return PreparedDelivery(
            scan_date=date.fromisoformat(payload["scan_date"]),
            summary=str(payload["summary"]),
            images=images,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def market_data_ready(status: MarketDataStatus) -> bool:
    """Return whether a trading day has all required A-share daily data."""
    return bool(
        status.is_trading_day
        and status.daily_latest == status.today
        and status.adjustment_latest == status.today
    )


def seconds_until_check(
    now: datetime,
    check_time: datetime_time = datetime_time(18, 0),
) -> float:
    """Return seconds until today's or the next day's configured check time."""
    target = datetime.combine(now.date(), check_time, tzinfo=now.tzinfo)
    if now >= target:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def format_filter_conditions(direction: str = "both") -> str:
    """Describe the active A-share strategy conditions for a QQ summary."""
    config = Rsi50Config()
    common = (
        f"共同：RSI({config.rsi_period}) 曾进入 "
        f"{config.rsi_zone_low:g}–{config.rsi_zone_high:g}，当前 RSI 位于 "
        f"{config.trigger_rsi_low:g}–{config.trigger_rsi_high:g}；"
        f"T-{config.recent_rsi_lookback} 至 T 共 "
        f"{config.recent_rsi_lookback + 1} 个 RSI 全部位于 "
        f"{config.recent_rsi_low:g}–{config.recent_rsi_high:g}。"
    )
    lines = ["筛选条件（日线）：", common]
    if direction in {"long", "both"}:
        lines.append(
            f"多头：MA20 最近 {config.ma_fast_angle_bars} Bar 拟合角度 > "
            f"{config.ma_fast_min_angle_degrees:g}°、MA30 向上。"
        )
    if direction in {"short", "both"}:
        lines.append(
            f"空头：MA20 最近 {config.ma_fast_angle_bars} Bar 拟合角度 < "
            f"-{config.ma_fast_min_angle_degrees:g}°、MA30 向下。"
        )
    return "\n".join(lines)


def read_a_share_status(settings: DatabaseSettings, today: date) -> MarketDataStatus:
    """Read today's A-share trading-calendar and table freshness state."""
    with psycopg.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.user,
        password=settings.password,
        connect_timeout=10,
    ) as connection:
        return read_market_data_status(connection, market="a", today=today)


def prepare_delivery(
    settings: DatabaseSettings,
    output_root: Path,
    *,
    lookback_bars: int,
    charts_per_message: int,
    enforce_freshness: bool = True,
) -> PreparedDelivery:
    """Run one fresh A-share scan and render all delivery images."""
    batch = scan_database_latest(
        settings,
        market="a",
        lookback_bars=lookback_bars,
        enforce_freshness=enforce_freshness,
    )
    output_dir = output_root / batch.scan_date.isoformat()
    rendered = [
        (
            match,
            render_signal_chart(
                match,
                output_dir / f"{match.symbol.replace('.', '_')}.png",
            ),
        )
        for match in batch.matches
    ]
    groups = [
        rendered[start : start + charts_per_message]
        for start in range(0, len(rendered), charts_per_message)
    ]
    images = tuple(
        DeliveryImage(
            path=render_signal_sheet(
                [path for _, path in group],
                output_dir / "batches" / f"batch_{index:03d}.png",
            ),
            symbols=tuple(match.symbol for match, _ in group),
        )
        for index, group in enumerate(groups, start=1)
    )
    long_count = sum(
        match.signal.direction is Direction.LONG for match in batch.matches
    )
    short_count = len(batch.matches) - long_count
    summary = (
        f"RSI50 A股日线扫描 {batch.scan_date:%Y-%m-%d}\n"
        f"扫描 {batch.scanned_symbols} 只，停牌/陈旧 {batch.stale_symbols} 只，"
        f"命中 {len(batch.matches)} 只（多 {long_count} / 空 {short_count}），"
        f"图片消息 {len(images)} 条。\n\n"
        f"{format_filter_conditions()}"
    )
    delivery = PreparedDelivery(batch.scan_date, summary, images)
    save_prepared_delivery(delivery, output_root / "latest_delivery.json")
    return delivery


class AShareQQService(botpy.Client):
    """Keep QQ online, prepare daily scans, and reply to group send commands."""

    def __init__(
        self,
        *,
        settings: DatabaseSettings,
        output_root: Path,
        check_time: datetime_time,
        poll_seconds: float,
        send_delay: float,
        lookback_bars: int,
        charts_per_message: int,
    ) -> None:
        super().__init__(intents=botpy.Intents(public_messages=True))
        self.settings = settings
        self.output_root = output_root
        self.check_time = check_time
        self.poll_seconds = poll_seconds
        self.send_delay = send_delay
        self.lookback_bars = lookback_bars
        self.charts_per_message = charts_per_message
        self.manifest = output_root / "latest_delivery.json"
        self.prepared = load_prepared_delivery(self.manifest)
        self.preparing = False
        self._scheduler_task: asyncio.Task[None] | None = None
        self._delivery_lock = asyncio.Lock()

    async def on_ready(self) -> None:
        """Start the daily data-check scheduler after QQ connects."""
        print("QQ WebSocket 已连接；定时扫描服务已启动。", flush=True)
        if self.prepared is not None:
            print(
                f"已加载 {self.prepared.scan_date} 历史扫描缓存。",
                flush=True,
            )
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        """Send the prepared scan when a group mention contains '发送'."""
        content = message.content or ""
        send_history = "发送历史" in content
        if not send_history and "发送" not in content:
            return
        group_openid = message.group_openid
        msg_id = message.id
        if not isinstance(group_openid, str) or not isinstance(msg_id, str):
            print("忽略缺少 group_openid 或 msg_id 的群消息。", flush=True)
            return
        command = "发送历史" if send_history else "发送"
        cache_date = self.prepared.scan_date if self.prepared is not None else "无"
        print(f"收到群命令：{command}；当前缓存日期：{cache_date}。", flush=True)
        prepared = self.prepared
        if self.preparing and not (send_history and prepared is not None):
            await asyncio.to_thread(
                self._reply_status,
                group_openid,
                msg_id,
                "今日数据已齐，扫描和绘图正在进行，请稍后再次发送。",
            )
            return
        if send_history and prepared is None:
            if self._delivery_lock.locked():
                await asyncio.to_thread(
                    self._reply_status,
                    group_openid,
                    msg_id,
                    "历史扫描正在准备，请稍后再试。",
                )
                return
            asyncio.create_task(self._prepare_history_and_deliver(group_openid, msg_id))
            return
        if prepared is None:
            await asyncio.to_thread(
                self._reply_status,
                group_openid,
                msg_id,
                "今日扫描尚未完成；交易日 18:00 后将在数据到齐时自动扫描。",
            )
            return
        if self._delivery_lock.locked():
            await asyncio.to_thread(
                self._reply_status,
                group_openid,
                msg_id,
                "已有一批信号正在发送，请稍后再试。",
            )
            return
        asyncio.create_task(self._deliver(group_openid, msg_id, prepared))

    async def _prepare_history_and_deliver(
        self,
        group_openid: str,
        msg_id: str,
    ) -> None:
        async with self._delivery_lock:
            try:
                print("未找到历史缓存，按数据库最后交易日开始扫描。", flush=True)
                prepared = await asyncio.to_thread(
                    prepare_delivery,
                    self.settings,
                    self.output_root,
                    lookback_bars=self.lookback_bars,
                    charts_per_message=self.charts_per_message,
                    enforce_freshness=False,
                )
                self.prepared = prepared
                await asyncio.to_thread(
                    self._deliver_sync,
                    group_openid,
                    msg_id,
                    prepared,
                )
            except (QQBotError, OSError, TypeError, ValueError) as error:
                print(f"历史扫描或发送失败：{error}", flush=True)

    async def _scheduler_loop(self) -> None:
        while True:
            now = datetime.now(SHANGHAI)
            if now.time() < self.check_time:
                await asyncio.sleep(seconds_until_check(now, self.check_time))
                continue
            if self.prepared is not None and self.prepared.scan_date == now.date():
                await asyncio.sleep(seconds_until_check(now, self.check_time))
                continue
            try:
                status = await asyncio.to_thread(
                    read_a_share_status,
                    self.settings,
                    now.date(),
                )
                if not status.is_trading_day:
                    print(f"{now:%Y-%m-%d} 非交易日，等待下次检查。", flush=True)
                    await asyncio.sleep(seconds_until_check(now, self.check_time))
                    continue
                if not market_data_ready(status):
                    print(
                        f"{now:%Y-%m-%d %H:%M:%S} 数据未齐："
                        f"daily={status.daily_latest}, "
                        f"adj_factor={status.adjustment_latest}",
                        flush=True,
                    )
                    await asyncio.sleep(self.poll_seconds)
                    continue
                self.preparing = True
                print(f"{now:%Y-%m-%d %H:%M:%S} 数据已齐，开始扫描。", flush=True)
                self.prepared = await asyncio.to_thread(
                    prepare_delivery,
                    self.settings,
                    self.output_root,
                    lookback_bars=self.lookback_bars,
                    charts_per_message=self.charts_per_message,
                )
                print(
                    f"{self.prepared.scan_date} 扫描完成，等待群内发送指令。",
                    flush=True,
                )
            except Exception as error:
                print(f"定时扫描失败，将重试：{error}", flush=True)
                await asyncio.sleep(self.poll_seconds)
            finally:
                self.preparing = False

    async def _deliver(
        self,
        group_openid: str,
        msg_id: str,
        prepared: PreparedDelivery,
    ) -> None:
        async with self._delivery_lock:
            try:
                await asyncio.to_thread(
                    self._deliver_sync,
                    group_openid,
                    msg_id,
                    prepared,
                )
            except (QQBotError, OSError, TypeError, ValueError) as error:
                print(f"QQ 批量发送失败：{error}", flush=True)

    def _deliver_sync(
        self,
        group_openid: str,
        msg_id: str,
        prepared: PreparedDelivery,
    ) -> None:
        print(
            f"开始发送 {prepared.scan_date} 扫描结果："
            f"{len(prepared.images)} 条图片消息。",
            flush=True,
        )
        client = QQBotClient()
        client.send_text(
            "group",
            group_openid,
            prepared.summary,
            msg_id=msg_id,
            msg_seq=1,
        )
        print("QQ 筛选条件和扫描汇总已发送。", flush=True)
        total = len(prepared.images)
        for index, image in enumerate(prepared.images, start=1):
            if self.send_delay:
                time.sleep(self.send_delay)
            client.send_image(
                "group",
                group_openid,
                image.path,
                content=(f"RSI50 信号 {index}/{total}\n{'、'.join(image.symbols)}"),
                msg_id=msg_id,
                msg_seq=index + 1,
            )
            print(
                f"QQ 已发送 {index}/{total}: {'、'.join(image.symbols)}",
                flush=True,
            )
        print(f"{prepared.scan_date} QQ 批量发送完成。", flush=True)

    @staticmethod
    def _reply_status(group_openid: str, msg_id: str, content: str) -> None:
        QQBotClient().send_text(
            "group",
            group_openid,
            content,
            msg_id=msg_id,
            msg_seq=1,
        )


def required_qq_credentials() -> tuple[str, str]:
    """Return configured QQ credentials or fail before starting the service."""
    app_id = os.getenv("QQBOT_APPID")
    secret = os.getenv("QQBOT_SECRET")
    if not app_id or not secret:
        raise ValueError("QQBOT_APPID and QQBOT_SECRET are required")
    return app_id, secret
