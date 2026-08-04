"""Scan all A shares once, render latest signals, and optionally send to QQ."""

from __future__ import annotations

import argparse
import os
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from quant_trade.qq_bot import QQBotClient, QQBotError, QQTargetType
from quant_trade.rsi50 import Direction
from quant_trade.scanner import (
    DatabaseSettings,
    SignalMatch,
    render_signal_chart,
    scan_database_latest,
)


def parse_args() -> argparse.Namespace:
    """Parse scan and delivery options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="send results to QQ")
    parser.add_argument(
        "--target-type",
        choices=("c2c", "group"),
        default="c2c",
    )
    parser.add_argument("--target-id", help="QQ openid; defaults to environment")
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
        default=20,
        help="refuse larger sends; use 0 to explicitly allow all",
    )
    parser.add_argument("--allow-stale-data", action="store_true")
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
    load_dotenv()
    batch = scan_database_latest(
        DatabaseSettings.from_env(),
        lookback_bars=args.lookback_bars,
    )
    matches = [
        match
        for match in batch.matches
        if args.direction == "both" or match.signal.direction.value == args.direction
    ]
    output_dir = args.output_dir / batch.scan_date.isoformat()
    rendered = [
        (
            match,
            render_signal_chart(
                match,
                output_dir / f"{match.symbol.replace('.', '_')}.png",
            ),
        )
        for match in matches
    ]
    long_count = sum(
        match.signal.direction is Direction.LONG for match in batch.matches
    )
    short_count = len(batch.matches) - long_count
    data_age_days = (date.today() - batch.scan_date).days
    summary = (
        f"RSI50 日线扫描 {batch.scan_date:%Y-%m-%d}\n"
        f"扫描 {batch.scanned_symbols} 只，停牌/陈旧 {batch.stale_symbols} 只，"
        f"命中 {len(batch.matches)} 只（多 {long_count} / 空 {short_count}），"
        f"本次选择 {len(rendered)} 只，数据滞后 {data_age_days} 天"
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
    if args.max_send and len(rendered) > args.max_send:
        raise SystemExit(
            f"拒绝发送 {len(rendered)} 条图片消息：超过 --max-send "
            f"{args.max_send}；确认后使用 --max-send 0"
        )

    target_type: QQTargetType = args.target_type
    target_id = args.target_id or _default_target_id(target_type)
    client = QQBotClient()
    client.send_text(target_type, target_id, summary)
    failures: list[str] = []
    for match, image_path in rendered:
        if args.delay:
            time.sleep(args.delay)
        try:
            client.send_image(
                target_type,
                target_id,
                image_path,
                content=_signal_text(match),
            )
            print(f"QQ 已发送: {match.symbol}")
        except (QQBotError, OSError, ValueError) as error:
            failures.append(match.symbol)
            print(f"QQ 发送失败: {match.symbol}: {error}")
    if failures:
        names = ", ".join(failures)
        raise SystemExit(f"部分信号发送失败: {names}")


def _signal_text(match: SignalMatch) -> str:
    signal = match.signal
    direction = "多头 W 底" if signal.direction is Direction.LONG else "空头 M 顶"
    return (
        f"[{direction}] {match.symbol} {match.name} ({match.industry})\n"
        f"总市值 {_format_market_cap(match.market_cap_cny)} | "
        f"日期 {signal.timestamp:%Y-%m-%d} | 收盘 {signal.close:.3f} | "
        f"RSI {signal.rsi:.2f} | 颈线 {signal.neckline:.3f} | "
        f"ATR {signal.atr:.3f}"
    )


def _format_market_cap(value: float | None) -> str:
    if value is None:
        return "未知"
    return f"{value / 100_000_000:.2f} 亿元"


def _default_target_id(target_type: QQTargetType) -> str:
    variable = "QQBOT_OPENID" if target_type == "c2c" else "QQBOT_GROUP_OPENID"
    target_id = os.getenv(variable)
    if not target_id:
        raise ValueError(f"{variable} is required when --target-id is omitted")
    return target_id


if __name__ == "__main__":
    main()
