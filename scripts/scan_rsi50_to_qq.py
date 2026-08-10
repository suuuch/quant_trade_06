"""Scan all A shares once, render latest signals, and optionally send to QQ."""

from __future__ import annotations

import argparse
import os
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from quant_trade.qq_bot import QQBotClient, QQBotError, QQTargetType
from quant_trade.rsi50 import Direction, Rsi50Config
from quant_trade.scanner import (
    DatabaseSettings,
    DataFreshnessError,
    SignalMatch,
    render_signal_chart,
    render_signal_sheet,
    scan_database_latest,
    select_matches_for_delivery,
)


def parse_args() -> argparse.Namespace:
    """Parse scan and delivery options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--market",
        choices=("a", "us"),
        default="a",
        help="market to scan: a for A shares (default), us for US shares",
    )
    parser.add_argument("--send", action="store_true", help="send results to QQ")
    parser.add_argument(
        "--target-type",
        choices=("c2c", "group"),
        default="group",
        help="QQ target type; defaults to the configured group",
    )
    parser.add_argument("--target-id", help="QQ openid; defaults to environment")
    parser.add_argument(
        "--msg-id",
        help="recent group message ID used for a passive reply",
    )
    parser.add_argument("--lookback-bars", type=int, default=240)
    parser.add_argument("--delay", type=float, default=10.0)
    parser.add_argument(
        "--direction",
        choices=("long", "short", "both"),
        default="both",
    )
    parser.add_argument(
        "--max-send",
        type=int,
        default=0,
        help="optional stock limit; defaults to 0, which sends all matches",
    )
    parser.add_argument(
        "--charts-per-message",
        type=int,
        default=4,
        help="combine this many stock charts into each QQ image message",
    )
    parser.add_argument("--allow-stale-data", action="store_true")
    parser.add_argument(
        "--skip-freshness-check",
        action="store_true",
        help="explicitly bypass the trading-day missing-data fuse",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/qq_signals"))
    return parser.parse_args()


def main() -> None:
    """Run one full scan, render every match, and optionally send the batch."""
    args = parse_args()
    if args.delay < 0:
        raise ValueError("delay must not be negative")
    if args.max_send < 0:
        raise ValueError("max_send must not be negative")
    if args.charts_per_message < 1:
        raise ValueError("charts_per_message must be positive")
    load_dotenv()
    try:
        batch = scan_database_latest(
            DatabaseSettings.from_env(),
            market=args.market,
            lookback_bars=args.lookback_bars,
            enforce_freshness=not args.skip_freshness_check,
        )
    except DataFreshnessError as error:
        raise SystemExit(f"执行已熔断：{error}") from error
    matches = [
        match
        for match in batch.matches
        if args.direction == "both" or match.signal.direction.value == args.direction
    ]
    delivery_matches = (
        select_matches_for_delivery(matches, args.max_send) if args.send else matches
    )
    output_dir = args.output_dir / batch.scan_date.isoformat()
    rendered = [
        (
            match,
            render_signal_chart(
                match,
                output_dir / f"{match.symbol.replace('.', '_')}.png",
            ),
        )
        for match in delivery_matches
    ]
    delivery_images = (
        _direction_delivery_images(rendered, output_dir, args.charts_per_message)
        if args.send
        else []
    )
    long_count = sum(
        match.signal.direction is Direction.LONG for match in batch.matches
    )
    short_count = len(batch.matches) - long_count
    data_age_days = (date.today() - batch.scan_date).days
    summary = (
        f"RSI 顺势交易 {_market_label(args.market)}日线扫描 {batch.scan_date:%Y-%m-%d}\n"
        f"扫描 {batch.scanned_symbols} 只，停牌/陈旧 {batch.stale_symbols} 只，"
        f"命中 {len(batch.matches)} 只（多 {long_count} / 空 {short_count}），"
        f"本次{'发送' if args.send else '选择'} {len(rendered)} 只，"
        f"图片消息 {len(delivery_images)} 条，"
        f"数据滞后 {data_age_days} 天\n\n"
        f"{_filter_conditions(args.market, args.direction)}"
    )
    print(summary)
    print(f"图片目录: {output_dir}")
    if args.verbose:
        for match, image_path in rendered:
            print(f"{_signal_text(match)}\n图片: {image_path}")

    if not args.send:
        print("dry-run 完成；添加 --send 才会发送 QQ 消息")
        return

    if data_age_days > 3 and not args.allow_stale_data:
        raise SystemExit("拒绝发送：行情数据超过 3 天；确认后添加 --allow-stale-data")
    target_type: QQTargetType = args.target_type
    target_id = args.target_id or _default_target_id(target_type)
    client = QQBotClient()
    client.send_text(
        target_type,
        target_id,
        summary,
        msg_id=args.msg_id,
        msg_seq=1 if args.msg_id else None,
    )
    failures: list[str] = []
    total_groups = len(delivery_images)
    for index, (direction, group, image_path) in enumerate(delivery_images, start=1):
        if args.delay:
            time.sleep(args.delay)
        symbols = [match.symbol for match, _ in group]
        try:
            client.send_image(
                target_type,
                target_id,
                image_path,
                content=(
                    f"{_direction_label(direction)}信号 {index}/{total_groups}\n"
                    f"{'、'.join(symbols)}"
                ),
                msg_id=args.msg_id,
                msg_seq=index + 1 if args.msg_id else None,
            )
            print(f"QQ 已发送 {index}/{total_groups}: {'、'.join(symbols)}")
        except (QQBotError, OSError, ValueError) as error:
            failures.extend(symbols)
            print(f"QQ 发送失败: {'、'.join(symbols)}: {error}")
    if failures:
        names = ", ".join(failures)
        raise SystemExit(f"部分信号发送失败: {names}")


def _chunk_rendered(
    rendered: list[tuple[SignalMatch, Path]],
    size: int,
) -> list[list[tuple[SignalMatch, Path]]]:
    """Split rendered charts into ordered QQ delivery groups."""
    return [rendered[start : start + size] for start in range(0, len(rendered), size)]


def _direction_delivery_images(
    rendered: list[tuple[SignalMatch, Path]],
    output_dir: Path,
    charts_per_message: int,
) -> list[tuple[Direction, list[tuple[SignalMatch, Path]], Path]]:
    """Render long batches followed by short batches for QQ delivery."""
    images: list[tuple[Direction, list[tuple[SignalMatch, Path]], Path]] = []
    for direction in (Direction.LONG, Direction.SHORT):
        direction_rendered = [
            item for item in rendered if item[0].signal.direction is direction
        ]
        groups = _chunk_rendered(direction_rendered, charts_per_message)
        images.extend(
            (
                direction,
                group,
                render_signal_sheet(
                    [image_path for _, image_path in group],
                    output_dir / "batches" / direction.value / f"batch_{index:03d}.png",
                ),
            )
            for index, group in enumerate(groups, start=1)
        )
    return images


def _direction_label(direction: Direction) -> str:
    """Return a QQ message prefix for a trade direction."""
    return "RSI 顺势交易多头" if direction is Direction.LONG else "RSI 顺势交易空头"


def _signal_text(match: SignalMatch) -> str:
    signal = match.signal
    direction = "多头" if signal.direction is Direction.LONG else "空头"
    return (
        f"[{direction}] {match.symbol} {match.name} ({match.industry})\n"
        f"总市值 {_format_market_cap(match.market_cap_cny, match.market)} | "
        f"日期 {signal.timestamp:%Y-%m-%d} | 收盘 {signal.close:.3f} | "
        f"RSI {signal.rsi:.2f} | ATR {signal.atr:.3f}"
    )


def _format_market_cap(value: float | None, market: str = "a") -> str:
    if value is None:
        return "未知"
    unit = "亿美元" if market == "us" else "亿元"
    return f"{value / 100_000_000:.2f} {unit}"


def _market_label(market: str) -> str:
    return "美股" if market == "us" else "A股"


def _filter_conditions(market: str, direction: str) -> str:
    """Describe the active strategy conditions for the QQ summary."""
    config = (
        Rsi50Config(ma_fast_min_angle_degrees=None) if market == "us" else Rsi50Config()
    )
    common = (
        f"共同：最新一天 RSI({config.rsi_period}) 位于 "
        f"{config.rsi_zone_low:g}–{config.rsi_zone_high:g}。"
    )
    if config.ma_fast_min_angle_degrees is None:
        long_ma = "MA20 向上"
        short_ma = "MA20 向下"
    else:
        long_ma = (
            f"MA20 最近 {config.ma_fast_angle_bars} Bar 拟合角度 > "
            f"{config.ma_fast_min_angle_degrees:g}°"
        )
        short_ma = (
            f"MA20 最近 {config.ma_fast_angle_bars} Bar 拟合角度 < "
            f"-{config.ma_fast_min_angle_degrees:g}°"
        )
    lines = ["RSI 顺势交易筛选条件（日线）：", common]
    if direction in {"long", "both"}:
        rsi_filter = config.rsi_filter_for(Direction.LONG)
        lines.append(
            f"多头：最近 {config.recent_rsi_days} 天 RSI 全部位于 "
            f"{rsi_filter.trigger_low:g}–{rsi_filter.trigger_high:g}；{long_ma}。"
        )
    if direction in {"short", "both"}:
        rsi_filter = config.rsi_filter_for(Direction.SHORT)
        lines.append(
            f"空头：最近 {config.recent_rsi_days} 天 RSI 全部位于 "
            f"{rsi_filter.trigger_low:g}–{rsi_filter.trigger_high:g}；{short_ma}。"
        )
    return "\n".join(lines)


def _default_target_id(target_type: QQTargetType) -> str:
    variable = "QQBOT_OPENID" if target_type == "c2c" else "QQBOT_GROUP_OPENID"
    target_id = os.getenv(variable)
    if not target_id:
        raise ValueError(f"{variable} is required when --target-id is omitted")
    return target_id


if __name__ == "__main__":
    main()
