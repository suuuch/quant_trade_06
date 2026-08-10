"""Scheduled A-share scanning and on-demand QQ group delivery."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from threading import Event
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

import botpy
import duckdb
import psycopg
from botpy.message import C2CMessage, GroupMessage

from quant_trade.delivery_store import (
    DeliveryImage,
    DeliveryMetadata,
    PreparedDelivery,
    delivery_duckdb_path,
    delivery_images_exist,
    load_prepared_delivery,
    record_delivery_send_event,
    save_delivery_to_duckdb,
    save_prepared_delivery,
)
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
    scan_database_on_date,
)
from quant_trade.wm_scanner import (
    WmSignalMatch,
    render_wm_signal_chart,
    scan_wm_database_latest,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
RenderedMatch = TypeVar("RenderedMatch")


@dataclass(frozen=True)
class WmCommand:
    """A parsed W/M delivery command."""

    market: Market
    pattern: Literal["w", "m", "wm"]
    history: bool = False


@dataclass(frozen=True)
class RsiCommand:
    """A parsed RSI delivery command with an optional target date."""

    scan_date: date | None = None


@dataclass(frozen=True)
class StopCommand:
    """A command that stops active delivery tasks."""


@dataclass(frozen=True)
class ClearCacheCommand:
    """A command that clears prepared delivery caches."""


QQCommand = StopCommand | ClearCacheCommand | WmCommand | RsiCommand


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
        delivery = PreparedDelivery(
            batch.scan_date,
            summary,
            images,
            DeliveryMetadata("W/M", market, pattern),
        )
        _persist_delivery(
            delivery,
            output_root,
            output_root / _wm_manifest_name(market, pattern),
            "W/M",
        )
        deliveries[pattern] = delivery
    return deliveries


def _wm_delivery_images(
    rendered: list[tuple[WmSignalMatch, Path]],
    output_dir: Path,
    charts_per_message: int,
) -> tuple[DeliveryImage, ...]:
    return tuple(
        DeliveryImage(
            path=render_signal_sheet(
                [path for _, path in group],
                output_dir / f"batch_{index:03d}.png",
            ),
            symbols=tuple(match.symbol for match, _ in group),
        )
        for index, group in enumerate(
            _chunk_rendered(rendered, charts_per_message), start=1
        )
    )


def _wm_manifest_name(market: str, pattern: str) -> str:
    return f"latest_wm_{market}_{pattern}.json"


def _rsi_manifest_name(scan_date: date) -> str:
    return f"rsi_{scan_date.isoformat()}.json"


def parse_rsi_command(content: str) -> RsiCommand | None:
    """Parse an RSI send command with an optional YYYY-MM-DD date."""
    compact = "".join(content.split())
    if "发送" not in compact:
        return None
    match = re.search(r"发送(\d{4}-\d{2}-\d{2})", compact)
    if match is None:
        return RsiCommand()
    try:
        return RsiCommand(date.fromisoformat(match.group(1)))
    except ValueError:
        return None


def parse_qq_command(content: str) -> QQCommand | None:
    """Parse one incoming QQ message into an atomic command."""
    compact = "".join(content.split())
    if "停止发送" in compact:
        return StopCommand()
    if "清理缓存" in compact:
        return ClearCacheCommand()
    wm_command = parse_wm_command(content)
    if wm_command is not None:
        return wm_command
    return parse_rsi_command(content)


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


def read_a_share_latest_data_date(settings: DatabaseSettings) -> date | None:
    """Read the latest A-share date with both daily and adjustment data."""
    with psycopg.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.user,
        password=settings.password,
        connect_timeout=10,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT least(
                    (SELECT max(trade_date) FROM tushare.daily),
                    (SELECT max(trade_date) FROM tushare.adj_factor)
                )
                """
            )
            row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    if isinstance(row[0], date):
        return row[0]
    value = str(row[0])
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    return datetime.fromisoformat(value).date()


def a_share_data_exists(settings: DatabaseSettings, scan_date: date) -> bool:
    """Return whether both A-share source tables contain the requested date."""
    trade_date = scan_date.strftime("%Y%m%d")
    with psycopg.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.user,
        password=settings.password,
        connect_timeout=10,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    EXISTS (SELECT 1 FROM tushare.daily WHERE trade_date = %s),
                    EXISTS (SELECT 1 FROM tushare.adj_factor WHERE trade_date = %s)
                """,
                (trade_date, trade_date),
            )
            row = cursor.fetchone()
    return row is not None and bool(row[0]) and bool(row[1])


def prepare_delivery(
    settings: DatabaseSettings,
    output_root: Path,
    *,
    lookback_bars: int,
    charts_per_message: int,
    enforce_freshness: bool = True,
    scan_date: date | None = None,
) -> PreparedDelivery:
    """Run one fresh A-share scan and render all delivery images."""
    if scan_date is None:
        batch = scan_database_latest(
            settings,
            market="a",
            lookback_bars=lookback_bars,
            enforce_freshness=enforce_freshness,
        )
    else:
        batch = scan_database_on_date(
            settings,
            scan_date,
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
    delivery = PreparedDelivery(
        batch.scan_date,
        summary,
        images,
        DeliveryMetadata("RSI 顺势交易", "a"),
    )
    _persist_delivery(
        delivery,
        output_root,
        output_root / _rsi_manifest_name(batch.scan_date),
        "RSI",
    )
    if scan_date is None:
        save_prepared_delivery(delivery, output_root / "latest_delivery.json")
    return delivery


def _persist_delivery(
    delivery: PreparedDelivery,
    output_root: Path,
    manifest_path: Path,
    log_label: str,
) -> None:
    save_prepared_delivery(delivery, manifest_path)
    try:
        save_delivery_to_duckdb(
            delivery,
            delivery_duckdb_path(output_root),
            manifest_path=manifest_path,
        )
    except (duckdb.Error, OSError, ValueError) as error:
        print(f"DuckDB 写入 {log_label} 发送缓存失败：{error}", flush=True)


def _chunk_rendered(
    rendered: Sequence[tuple[RenderedMatch, Path]],
    size: int,
) -> list[Sequence[tuple[RenderedMatch, Path]]]:
    return [rendered[start : start + size] for start in range(0, len(rendered), size)]


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
        images.extend(
            DeliveryImage(
                path=render_signal_sheet(
                    [path for _, path in group],
                    output_dir / "batches" / direction.value / f"batch_{index:03d}.png",
                ),
                symbols=tuple(match.symbol for match, _ in group),
                direction=direction,
            )
            for index, group in enumerate(
                _chunk_rendered(direction_rendered, charts_per_message),
                start=1,
            )
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
        self.prepared_by_date: dict[date, PreparedDelivery] = {}
        if self.prepared is not None:
            self.prepared_by_date[self.prepared.scan_date] = self.prepared
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
        self._delivery_tasks: dict[asyncio.Task[None], Event] = {}

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
        command = parse_qq_command(content)
        if command is None:
            return
        if isinstance(command, StopCommand):
            await self._handle_stop_command(target_type, target_id, msg_id)
        elif isinstance(command, ClearCacheCommand):
            await self._handle_clear_cache_command(target_type, target_id, msg_id)
        elif isinstance(command, WmCommand):
            await self._handle_wm_command(target_type, target_id, msg_id, command)
        else:
            await self._handle_rsi_command(target_type, target_id, msg_id, command)

    async def _handle_stop_command(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
    ) -> None:
        self._stop_delivery_tasks()
        await asyncio.to_thread(
            self._reply_status,
            target_type,
            target_id,
            msg_id,
            "已收到停止发送命令，正在终止当前发送任务。",
        )

    async def _handle_clear_cache_command(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
    ) -> None:
        self._stop_delivery_tasks()
        self._clear_delivery_cache()
        await asyncio.to_thread(
            self._reply_status,
            target_type,
            target_id,
            msg_id,
            "缓存已清理；下次发送将重新扫描并生成图片。",
        )

    async def _handle_rsi_command(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        command: RsiCommand,
    ) -> None:
        target_date = await asyncio.to_thread(
            self._resolve_rsi_target_date,
            command.scan_date,
        )
        if target_date is None:
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                "数据库中没有可发送的 A股 RSI 数据。",
            )
            return
        if not await asyncio.to_thread(a_share_data_exists, self.settings, target_date):
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                f"数据库中没有 {target_date:%Y-%m-%d} 的完整 A股日线和复权数据。",
            )
            return
        command_label = f"发送{target_date:%Y-%m-%d}"
        prepared = self._load_rsi_delivery(target_date)
        cache_date = prepared.scan_date if prepared is not None else "无"
        print(f"收到命令：{command_label}；当前缓存日期：{cache_date}。", flush=True)
        if prepared is not None and not delivery_images_exist(prepared):
            print(
                f"{prepared.scan_date} RSI 缓存图片缺失，清空缓存并重新准备。",
                flush=True,
            )
            self.prepared_by_date.pop(prepared.scan_date, None)
            if self.prepared is prepared:
                self.prepared = None
            prepared = None
        if self.preparing and prepared is None:
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                "今日数据已齐，扫描和绘图正在进行，请稍后再次发送。",
            )
            return
        if prepared is None:
            if self._delivery_lock.locked():
                await asyncio.to_thread(
                    self._reply_status,
                    target_type,
                    target_id,
                    msg_id,
                    "已有一批信号正在扫描或发送，请稍后再试。",
                )
                return
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                f"{target_date:%Y-%m-%d} 未找到缓存，开始扫描并生成图片。",
            )
            self._start_delivery_task(
                lambda stop_event: self._prepare_rsi_and_deliver(
                    target_type,
                    target_id,
                    msg_id,
                    target_date,
                    stop_event,
                )
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
        self._start_delivery_task(
            lambda stop_event: self._deliver(
                target_type,
                target_id,
                msg_id,
                prepared,
                stop_event,
            )
        )

    def _resolve_rsi_target_date(self, requested_date: date | None) -> date | None:
        if requested_date is not None:
            return requested_date
        return read_a_share_latest_data_date(self.settings)

    def _load_rsi_delivery(self, scan_date: date) -> PreparedDelivery | None:
        prepared = self.prepared_by_date.get(scan_date)
        if prepared is not None:
            return prepared
        prepared = load_prepared_delivery(
            self.output_root / _rsi_manifest_name(scan_date)
        )
        if prepared is None:
            return None
        self.prepared_by_date[prepared.scan_date] = prepared
        if self.prepared is None or self.prepared.scan_date <= prepared.scan_date:
            self.prepared = prepared
        return prepared

    def _clear_delivery_cache(self) -> None:
        manifests = [
            self.manifest,
            *self.output_root.glob("rsi_*.json"),
            *self.output_root.glob("latest_wm_*.json"),
        ]
        failed: list[Path] = []
        for manifest in manifests:
            try:
                manifest.unlink(missing_ok=True)
            except OSError as error:
                failed.append(manifest)
                print(f"清理缓存文件失败 {manifest}: {error}", flush=True)
        if failed:
            print("部分缓存文件未删除，重启后可能仍会加载残留缓存。", flush=True)
        self.prepared = None
        self.prepared_by_date.clear()
        self.wm_prepared.clear()

    def _stop_delivery_tasks(self) -> None:
        for task, stop_event in tuple(self._delivery_tasks.items()):
            stop_event.set()
            if not task.done():
                task.cancel()

    def _start_delivery_task(
        self,
        task_factory: Callable[[Event], Coroutine[object, object, None]],
    ) -> None:
        stop_event = Event()
        task = asyncio.create_task(task_factory(stop_event))
        self._delivery_tasks[task] = stop_event

        def discard(done: asyncio.Task[None]) -> None:
            self._delivery_tasks.pop(done, None)
            if done.cancelled():
                return
            try:
                done.exception()
            except asyncio.CancelledError:
                return

        task.add_done_callback(discard)

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
            self._start_delivery_task(
                lambda stop_event: self._deliver(
                    target_type,
                    target_id,
                    msg_id,
                    prepared,
                    stop_event,
                )
            )
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
        self._start_delivery_task(
            lambda stop_event: self._prepare_wm_and_deliver(
                target_type,
                target_id,
                msg_id,
                command,
                stop_event,
            )
        )

    async def _prepare_rsi_and_deliver(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        scan_date: date,
        stop_event: Event,
    ) -> None:
        async with self._delivery_lock:
            try:
                print(f"未找到 {scan_date:%Y-%m-%d} 缓存，开始 RSI 扫描。", flush=True)
                prepared = await asyncio.to_thread(
                    prepare_delivery,
                    self.settings,
                    self.output_root,
                    lookback_bars=self.lookback_bars,
                    charts_per_message=self.charts_per_message,
                    enforce_freshness=False,
                    scan_date=scan_date,
                )
                if stop_event.is_set():
                    print("RSI 扫描完成，但发送已停止。", flush=True)
                    return
                self.prepared = prepared
                self.prepared_by_date[prepared.scan_date] = prepared
                await asyncio.to_thread(
                    self._deliver_sync,
                    target_type,
                    target_id,
                    msg_id,
                    prepared,
                    stop_event,
                    2,
                )
            except (QQBotError, OSError, TypeError, ValueError) as error:
                print(f"RSI 扫描或发送失败：{error}", flush=True)
                await asyncio.to_thread(
                    self._reply_status,
                    target_type,
                    target_id,
                    msg_id,
                    f"RSI 扫描或发送失败：{error}",
                )

    async def _prepare_wm_and_deliver(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        command: WmCommand,
        stop_event: Event,
    ) -> None:
        async with self._delivery_lock:
            await self._prepare_wm_and_deliver_locked(
                target_type,
                target_id,
                msg_id,
                command,
                stop_event,
            )

    async def _prepare_wm_and_deliver_locked(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        command: WmCommand,
        stop_event: Event,
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
            if stop_event.is_set():
                print("W/M 扫描完成，但发送已停止。", flush=True)
                return
            await asyncio.to_thread(
                self._deliver_sync,
                target_type,
                target_id,
                msg_id,
                deliveries[command.pattern],
                stop_event,
                2,
            )
        except (QQBotError, OSError, TypeError, ValueError) as error:
            print(f"W/M 扫描或发送失败：{error}", flush=True)
            await asyncio.to_thread(
                self._reply_status,
                target_type,
                target_id,
                msg_id,
                f"W/M 扫描或发送失败：{error}",
            )
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
                    self.prepared_by_date[self.prepared.scan_date] = self.prepared
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
        stop_event: Event,
    ) -> None:
        async with self._delivery_lock:
            try:
                await asyncio.to_thread(
                    self._deliver_sync,
                    target_type,
                    target_id,
                    msg_id,
                    prepared,
                    stop_event,
                    1,
                )
            except (QQBotError, OSError, TypeError, ValueError) as error:
                print(f"QQ 批量发送失败：{error}", flush=True)
                self._record_send_event(
                    prepared,
                    target_type,
                    target_id,
                    "failed",
                    str(error),
                )

    def _deliver_sync(
        self,
        target_type: QQTargetType,
        target_id: str,
        msg_id: str,
        prepared: PreparedDelivery,
        stop_event: Event,
        first_msg_seq: int = 1,
    ) -> None:
        print(
            f"开始发送 {prepared.scan_date} 扫描结果："
            f"{len(prepared.images)} 条图片消息。",
            flush=True,
        )
        if stop_event.is_set():
            self._record_send_event(
                prepared,
                target_type,
                target_id,
                "stopped",
                "stopped before summary",
            )
            return
        self._record_send_event(
            prepared,
            target_type,
            target_id,
            "started",
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
            if stop_event.is_set():
                print("发送已停止，剩余图片不再发送。", flush=True)
                self._record_send_event(
                    prepared,
                    target_type,
                    target_id,
                    "stopped",
                    f"stopped before image {index}/{total}",
                )
                return
            if self.send_delay:
                time.sleep(self.send_delay)
            if stop_event.is_set():
                print("发送已停止，剩余图片不再发送。", flush=True)
                self._record_send_event(
                    prepared,
                    target_type,
                    target_id,
                    "stopped",
                    f"stopped before image {index}/{total}",
                )
                return
            direction_label = _delivery_direction_label(
                image.direction,
                prepared.metadata.strategy,
            )
            client.send_image(
                target_type,
                target_id,
                image.path,
                content=(
                    f"{direction_label}信号 {index}/{total}\n{'、'.join(image.symbols)}"
                ),
                msg_id=msg_id,
                msg_seq=first_msg_seq + index,
            )
            print(
                f"QQ 已发送 {index}/{total}: {'、'.join(image.symbols)}",
                flush=True,
            )
        print(f"{prepared.scan_date} QQ 批量发送完成。", flush=True)
        self._record_send_event(
            prepared,
            target_type,
            target_id,
            "completed",
        )

    def _record_send_event(
        self,
        prepared: PreparedDelivery,
        target_type: QQTargetType,
        target_id: str,
        status: str,
        detail: str = "",
    ) -> None:
        try:
            record_delivery_send_event(
                delivery_duckdb_path(self.output_root),
                metadata=prepared.metadata,
                scan_date=prepared.scan_date,
                target_type=target_type,
                target_id=target_id,
                status=status,
                detail=detail,
            )
        except (duckdb.Error, OSError, ValueError) as error:
            print(f"DuckDB 写入 QQ 发送事件失败：{error}", flush=True)

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


def _delivery_direction_label(direction: Direction | None, strategy: str) -> str:
    """Return a QQ message prefix for one delivery image direction."""
    if strategy == "W/M":
        return "W/M"
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
