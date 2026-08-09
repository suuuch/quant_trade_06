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
from typing import Literal
from zoneinfo import ZoneInfo

import botpy
import psycopg
from botpy.message import C2CMessage, GroupMessage

from quant_trade.qq_bot import QQBotClient, QQBotError, QQTargetType
from quant_trade.rsi50 import Direction, Rsi50Config
from quant_trade.scanner import (
    DatabaseSettings,
    Market,
    MarketDataStatus,
    SignalMatch,
    read_market_data_status,
    render_signal_chart,
    render_signal_sheet,
    scan_database_latest,
)
from quant_trade.wm_scanner import (
    WmSignalMatch,
    render_wm_signal_chart,
    scan_wm_database_latest,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DeliveryImage:
    """One combined image and the symbols displayed in it."""

    path: Path
    symbols: tuple[str, ...]
    direction: Direction | None = None


@dataclass(frozen=True)
class PreparedDelivery:
    """A completed daily scan ready for immediate QQ delivery."""

    scan_date: date
    summary: str
    images: tuple[DeliveryImage, ...]


@dataclass(frozen=True)
class WmCommand:
    """A parsed W/M delivery command."""

    market: Market
    pattern: Literal["w", "m", "wm"]
    history: bool = False


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
                    "direction": (
                        None if image.direction is None else image.direction.value
                    ),
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
                direction=(
                    None
                    if item.get("direction") is None
                    else Direction(str(item["direction"]))
                ),
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
        f"共同：最新一天 RSI({config.rsi_period}) 位于 "
        f"{config.rsi_zone_low:g}–{config.rsi_zone_high:g}。"
    )
    lines = ["RSI 顺势交易筛选条件（日线）：", common]
    if direction in {"long", "both"}:
        rsi_filter = config.rsi_filter_for(Direction.LONG)
        lines.append(
            f"多头：最近 {config.recent_rsi_days} 天 RSI 全部位于 "
            f"{rsi_filter.trigger_low:g}–{rsi_filter.trigger_high:g}；MA20 最近 "
            f"{config.ma_fast_angle_bars} Bar 拟合角度 > "
            f"{config.ma_fast_min_angle_degrees:g}°。"
        )
    if direction in {"short", "both"}:
        rsi_filter = config.rsi_filter_for(Direction.SHORT)
        lines.append(
            f"空头：最近 {config.recent_rsi_days} 天 RSI 全部位于 "
            f"{rsi_filter.trigger_low:g}–{rsi_filter.trigger_high:g}；MA20 最近 "
            f"{config.ma_fast_angle_bars} Bar 拟合角度 < "
            f"-{config.ma_fast_min_angle_degrees:g}°。"
        )
    return "\n".join(lines)


def parse_wm_command(content: str) -> WmCommand | None:
    """Parse a W/M command with an optional A-share or US-share keyword."""
    compact = "".join(content.upper().split())
    if "发送" not in compact:
        return None
    pattern: Literal["w", "m", "wm"]
    if "W底" in compact and "M顶" in compact:
        pattern = "wm"
    elif "WM" in compact or "形态" in compact:
        pattern = "wm"
    elif "W底" in compact:
        pattern = "w"
    elif "M顶" in compact:
        pattern = "m"
    else:
        return None
    market: Market = "us" if "美股" in compact else "a"
    return WmCommand(market, pattern, history="历史" in compact)


def format_wm_conditions(pattern: Literal["w", "m", "wm"]) -> str:
    """Describe the independent W/M entry conditions."""
    pattern_label = {"w": "W 底", "m": "M 顶", "wm": "W 底及 M 顶"}[pattern]
    return (
        f"筛选条件（日线，{pattern_label}）：\n"
        "摆动点采用 3/3；两个同类摆动点间距 5–30 Bar；"
        "两端价差 ≤ 1 ATR；中间反弹/回撤 ≥ 1 ATR；"
        "W 底要求收盘 > 颈线 + 0.1 ATR，"
        "M 顶要求收盘 < 颈线 - 0.1 ATR。\n"
        "该策略独立运行，不叠加 RSI 顺势交易或均线条件。"
    )


def prepare_wm_deliveries(
    settings: DatabaseSettings,
    output_root: Path,
    *,
    market: Market,
    lookback_bars: int,
    charts_per_message: int,
    enforce_freshness: bool = True,
) -> dict[str, PreparedDelivery]:
    """Scan and cache W-bottom, M-top, and combined delivery batches."""
    if market not in {"a", "us"}:
        raise ValueError("market must be 'a' or 'us'")
    batch = scan_wm_database_latest(
        settings,
        market=market,
        lookback_bars=lookback_bars,
        enforce_freshness=enforce_freshness,
    )
    market_label = "A股" if market == "a" else "美股"
    output_dir = output_root / f"wm_{market}" / batch.scan_date.isoformat()
    rendered = [
        (
            match,
            render_wm_signal_chart(
                match,
                output_dir
                / (
                    f"{match.symbol.replace('.', '_')}_"
                    f"{match.signal.direction.value}.png"
                ),
            ),
        )
        for match in batch.matches
    ]
    selections: dict[
        Literal["w", "m", "wm"],
        list[tuple[WmSignalMatch, Path]],
    ] = {
        "w": [item for item in rendered if item[0].signal.direction is Direction.LONG],
        "m": [item for item in rendered if item[0].signal.direction is Direction.SHORT],
        "wm": rendered,
    }
    deliveries: dict[str, PreparedDelivery] = {}
    for pattern, selected in selections.items():
        images = _wm_delivery_images(
            selected,
            output_dir / "batches" / pattern,
            charts_per_message,
        )
        summary = (
            f"{market_label} W/M 日线入场扫描 {batch.scan_date:%Y-%m-%d}\n"
            f"扫描 {batch.scanned_symbols} 只，停牌/陈旧 {batch.stale_symbols} 只，"
            f"命中 {len(selected)} 只，图片消息 {len(images)} 条。\n\n"
            f"{format_wm_conditions(pattern)}"
        )
        delivery = PreparedDelivery(batch.scan_date, summary, images)
        save_prepared_delivery(
            delivery,
            output_root / _wm_manifest_name(market, pattern),
        )
        deliveries[pattern] = delivery
    return deliveries


def _wm_delivery_images(
    rendered: list[tuple[WmSignalMatch, Path]],
    output_dir: Path,
    charts_per_message: int,
) -> tuple[DeliveryImage, ...]:
    groups = [
        rendered[start : start + charts_per_message]
        for start in range(0, len(rendered), charts_per_message)
    ]
    return tuple(
        DeliveryImage(
            path=render_signal_sheet(
                [path for _, path in group],
                output_dir / f"batch_{index:03d}.png",
            ),
            symbols=tuple(match.symbol for match, _ in group),
        )
        for index, group in enumerate(groups, start=1)
    )


def _wm_manifest_name(market: str, pattern: str) -> str:
    return f"latest_wm_{market}_{pattern}.json"


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
    images = _rsi_delivery_images(rendered, output_dir, charts_per_message)
    long_count = sum(
        match.signal.direction is Direction.LONG for match in batch.matches
    )
    short_count = len(batch.matches) - long_count
    summary = (
        f"RSI 顺势交易 A股日线扫描 {batch.scan_date:%Y-%m-%d}\n"
        f"扫描 {batch.scanned_symbols} 只，停牌/陈旧 {batch.stale_symbols} 只，"
        f"命中 {len(batch.matches)} 只（多 {long_count} / 空 {short_count}），"
        f"图片消息 {len(images)} 条。\n\n"
        f"{format_filter_conditions()}"
    )
    delivery = PreparedDelivery(batch.scan_date, summary, images)
    save_prepared_delivery(delivery, output_root / "latest_delivery.json")
    return delivery


def _rsi_delivery_images(
    rendered: list[tuple[SignalMatch, Path]],
    output_dir: Path,
    charts_per_message: int,
) -> tuple[DeliveryImage, ...]:
    """Combine RSI charts into long batches followed by short batches."""
    images: list[DeliveryImage] = []
    for direction in (Direction.LONG, Direction.SHORT):
        direction_rendered = [
            item for item in rendered if item[0].signal.direction is direction
        ]
        groups = [
            direction_rendered[start : start + charts_per_message]
            for start in range(0, len(direction_rendered), charts_per_message)
        ]
        images.extend(
            DeliveryImage(
                path=render_signal_sheet(
                    [path for _, path in group],
                    output_dir
                    / "batches"
                    / direction.value
                    / f"batch_{index:03d}.png",
                ),
                symbols=tuple(match.symbol for match, _ in group),
                direction=direction,
            )
            for index, group in enumerate(groups, start=1)
        )
    return tuple(images)


class QQSignalService(botpy.Client):
    """Prepare strategy caches and handle authorized group/C2C commands."""

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
        self.c2c_openid = os.getenv("QQBOT_OPENID")
        self.wm_prepared = {
            (market, pattern): delivery
            for market in ("a", "us")
            for pattern in ("w", "m", "wm")
            if (
                delivery := load_prepared_delivery(
                    output_root / _wm_manifest_name(market, pattern)
                )
            )
            is not None
        }
        self.preparing = False
        self.wm_preparing: set[str] = set()
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
        for (market, pattern), delivery in sorted(self.wm_prepared.items()):
            market_label = "A股" if market == "a" else "美股"
            pattern_label = {"w": "W底", "m": "M顶", "wm": "WM"}[pattern]
            print(
                f"已加载 {delivery.scan_date} {market_label}{pattern_label}缓存。",
                flush=True,
            )
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        """Handle supported commands from an mentioned QQ group."""
        group_openid = message.group_openid
        msg_id = message.id
        if not isinstance(group_openid, str) or not isinstance(msg_id, str):
            print("忽略缺少 group_openid 或 msg_id 的群消息。", flush=True)
            return
        await self._handle_command("group", group_openid, msg_id, message.content or "")

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        """Handle commands from the configured QQBOT_OPENID conversation."""
        user_openid = message.author.user_openid
        msg_id = message.id
        if not isinstance(user_openid, str) or not isinstance(msg_id, str):
            print("忽略缺少 user_openid 或 msg_id 的单聊消息。", flush=True)
            return
        if not self.c2c_openid or user_openid != self.c2c_openid:
            print(f"忽略未授权 QQ 单聊：{user_openid}。", flush=True)
            return
        await self._handle_command("c2c", user_openid, msg_id, message.content or "")

    async def _handle_command(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        content: str,
    ) -> None:
        wm_command = parse_wm_command(content)
        if wm_command is not None:
            await self._handle_wm_command(
                target_type,
                target_id,
                msg_id,
                wm_command,
            )
            return
        send_history = "发送历史" in content
        if not send_history and "发送" not in content:
            return
        command = "发送历史" if send_history else "发送"
        cache_date = self.prepared.scan_date if self.prepared is not None else "无"
        print(f"收到命令：{command}；当前缓存日期：{cache_date}。", flush=True)
        prepared = self.prepared
        if self.preparing and not (send_history and prepared is not None):
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                "今日数据已齐，扫描和绘图正在进行，请稍后再次发送。",
            )
            return
        if send_history and prepared is None:
            if self._delivery_lock.locked():
                await asyncio.to_thread(
                    self._reply_status,
                    target_type,
                    target_id,
                    msg_id,
                    "历史扫描正在准备，请稍后再试。",
                )
                return
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                "未找到历史缓存，开始按数据库最后交易日扫描，完成后发送。",
            )
            asyncio.create_task(
                self._prepare_history_and_deliver(
                    target_type,
                    target_id,
                    msg_id,
                )
            )
            return
        if prepared is None:
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                "今日扫描尚未完成；交易日 18:00 后将在数据到齐时自动扫描。",
            )
            return
        if self._delivery_lock.locked():
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                "已有一批信号正在发送，请稍后再试。",
            )
            return
        asyncio.create_task(self._deliver(target_type, target_id, msg_id, prepared))

    async def _handle_wm_command(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        command: WmCommand,
    ) -> None:
        key = (command.market, command.pattern)
        prepared = self.wm_prepared.get(key)
        market_label = "A股" if command.market == "a" else "美股"
        pattern_label = {"w": "W底", "m": "M顶", "wm": "WM"}[command.pattern]
        history_label = "历史" if command.history else ""
        print(
            f"收到命令：发送{history_label}{market_label}{pattern_label}。",
            flush=True,
        )
        if prepared is not None:
            asyncio.create_task(self._deliver(target_type, target_id, msg_id, prepared))
            return
        if command.market in self.wm_preparing:
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                f"{market_label} W/M 正在扫描和绘图，请稍后再次发送。",
            )
            return
        asyncio.create_task(
            self._prepare_wm_and_deliver(
                target_type,
                target_id,
                msg_id,
                command,
            )
        )

    async def _prepare_history_and_deliver(
        self,
        target_type: QQTargetType,
        target_id: str,
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
                    target_type,
                    target_id,
                    msg_id,
                    prepared,
                )
            except (QQBotError, OSError, TypeError, ValueError) as error:
                print(f"历史扫描或发送失败：{error}", flush=True)
                await asyncio.to_thread(
                    self._reply_status,
                    target_type,
                    target_id,
                    msg_id,
                    f"历史扫描或发送失败：{error}",
                )

    async def _prepare_wm_and_deliver(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        command: WmCommand,
    ) -> None:
        async with self._delivery_lock:
            await self._prepare_wm_and_deliver_locked(
                target_type,
                target_id,
                msg_id,
                command,
            )

    async def _prepare_wm_and_deliver_locked(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        command: WmCommand,
    ) -> None:
        market = command.market
        self.wm_preparing.add(market)
        try:
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                f"{'A股' if market == 'a' else '美股'} W/M 开始扫描，完成后发送。",
            )
            deliveries = await asyncio.to_thread(
                prepare_wm_deliveries,
                self.settings,
                self.output_root,
                market=market,
                lookback_bars=self.lookback_bars,
                charts_per_message=self.charts_per_message,
                enforce_freshness=market == "a" and not command.history,
            )
            self.wm_prepared.update(
                {
                    (market, pattern): delivery
                    for pattern, delivery in deliveries.items()
                }
            )
            await asyncio.to_thread(
                self._deliver_sync,
                target_type,
                target_id,
                msg_id,
                deliveries[command.pattern],
                2,
            )
        except (QQBotError, OSError, TypeError, ValueError) as error:
            print(f"W/M 扫描或发送失败：{error}", flush=True)
        finally:
            self.wm_preparing.discard(market)

    async def _scheduler_loop(self) -> None:
        while True:
            now = datetime.now(SHANGHAI)
            if now.time() < self.check_time:
                await asyncio.sleep(seconds_until_check(now, self.check_time))
                continue
            rsi_current = (
                self.prepared is not None and self.prepared.scan_date == now.date()
            )
            wm_current = all(
                (delivery := self.wm_prepared.get(("a", pattern))) is not None
                and delivery.scan_date == now.date()
                for pattern in ("w", "m", "wm")
            )
            if rsi_current and wm_current:
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
                if not rsi_current:
                    print(
                        f"{now:%Y-%m-%d %H:%M:%S} 数据已齐，开始 RSI 顺势交易扫描。",
                        flush=True,
                    )
                    self.prepared = await asyncio.to_thread(
                        prepare_delivery,
                        self.settings,
                        self.output_root,
                        lookback_bars=self.lookback_bars,
                        charts_per_message=self.charts_per_message,
                    )
                    print(
                        f"{self.prepared.scan_date} RSI 顺势交易扫描完成。",
                        flush=True,
                    )
                if not wm_current:
                    print(
                        f"{now:%Y-%m-%d %H:%M:%S} 开始 A股 W/M 扫描。",
                        flush=True,
                    )
                    wm_deliveries = await asyncio.to_thread(
                        prepare_wm_deliveries,
                        self.settings,
                        self.output_root,
                        market="a",
                        lookback_bars=self.lookback_bars,
                        charts_per_message=self.charts_per_message,
                    )
                    self.wm_prepared.update(
                        {
                            ("a", pattern): delivery
                            for pattern, delivery in wm_deliveries.items()
                        }
                    )
                    print(
                        f"{now:%Y-%m-%d} A股 W/M 扫描完成。",
                        flush=True,
                    )
                print("全部扫描完成，等待发送指令。", flush=True)
            except Exception as error:
                print(f"定时扫描失败，将重试：{error}", flush=True)
                await asyncio.sleep(self.poll_seconds)
            finally:
                self.preparing = False

    async def _deliver(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        prepared: PreparedDelivery,
    ) -> None:
        async with self._delivery_lock:
            try:
                await asyncio.to_thread(
                    self._deliver_sync,
                    target_type,
                    target_id,
                    msg_id,
                    prepared,
                    2,
                )
            except (QQBotError, OSError, TypeError, ValueError) as error:
                print(f"QQ 批量发送失败：{error}", flush=True)

    def _deliver_sync(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        prepared: PreparedDelivery,
        first_msg_seq: int = 1,
    ) -> None:
        print(
            f"开始发送 {prepared.scan_date} 扫描结果："
            f"{len(prepared.images)} 条图片消息。",
            flush=True,
        )
        client = QQBotClient()
        client.send_text(
            target_type,
            target_id,
            prepared.summary,
            msg_id=msg_id,
            msg_seq=first_msg_seq,
        )
        print("QQ 筛选条件和扫描汇总已发送。", flush=True)
        total = len(prepared.images)
        for index, image in enumerate(prepared.images, start=1):
            if self.send_delay:
                time.sleep(self.send_delay)
            direction_label = _delivery_direction_label(image.direction)
            client.send_image(
                target_type,
                target_id,
                image.path,
                content=(
                    f"{direction_label}信号 {index}/{total}\n"
                    f"{'、'.join(image.symbols)}"
                ),
                msg_id=msg_id,
                msg_seq=first_msg_seq + index,
            )
            print(
                f"QQ 已发送 {index}/{total}: {'、'.join(image.symbols)}",
                flush=True,
            )
        print(f"{prepared.scan_date} QQ 批量发送完成。", flush=True)

    @staticmethod
    def _reply_status(
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        content: str,
    ) -> None:
        QQBotClient().send_text(
            target_type,
            target_id,
            content,
            msg_id=msg_id,
            msg_seq=1,
        )


def _delivery_direction_label(direction: Direction | None) -> str:
    """Return a QQ message prefix for one delivery image direction."""
    if direction is Direction.LONG:
        return "RSI 顺势交易多头"
    if direction is Direction.SHORT:
        return "RSI 顺势交易空头"
    return "RSI 顺势交易"


def required_qq_credentials() -> tuple[str, str]:
    """Return configured QQ credentials or fail before starting the service."""
    app_id = os.getenv("QQBOT_APPID")
    secret = os.getenv("QQBOT_SECRET")
    if not app_id or not secret:
        raise ValueError("QQBOT_APPID and QQBOT_SECRET are required")
    return app_id, secret
