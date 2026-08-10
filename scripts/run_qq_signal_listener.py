"""Run the QQ command listener for on-demand signal delivery."""

from __future__ import annotations

import argparse
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

from quant_trade.qq_service import QQSignalService, required_qq_credentials
from quant_trade.scanner import DatabaseSettings


def parse_args() -> argparse.Namespace:
    """Parse listener scheduling and delivery options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-hour", type=int, default=18)
    parser.add_argument("--poll-seconds", type=float, default=300.0)
    parser.add_argument("--send-delay", type=float, default=3.0)
    parser.add_argument("--lookback-bars", type=int, default=240)
    parser.add_argument("--charts-per-message", type=int, default=4)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/qq_signals"),
    )
    return parser.parse_args()


def main() -> None:
    """Load configuration and keep the QQ listener running."""
    args = parse_args()
    if not 0 <= args.check_hour <= 23:
        raise ValueError("check_hour must be between 0 and 23")
    if args.poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    if args.send_delay < 0:
        raise ValueError("send_delay must not be negative")
    if args.charts_per_message < 1:
        raise ValueError("charts_per_message must be positive")
    load_dotenv()
    app_id, secret = required_qq_credentials()
    client = QQSignalService(
        settings=DatabaseSettings.from_env(),
        output_root=args.output_dir,
        check_time=time(args.check_hour),
        poll_seconds=args.poll_seconds,
        send_delay=args.send_delay,
        lookback_bars=args.lookback_bars,
        charts_per_message=args.charts_per_message,
    )
    client.run(appid=app_id, secret=secret)


if __name__ == "__main__":
    main()
