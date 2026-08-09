"""Generate the interactive RSI trend-following signal-review report."""

from pathlib import Path

from quant_trade.report import generate_signal_review

DATABASE = Path("data/a_share_backtest.duckdb")
OUTPUT = Path("reports/rsi50_signal_review.html")
SYMBOLS = [
    "000001.SZ",
    "000858.SZ",
    "600036.SH",
    "600519.SH",
    "601318.SH",
]


def main() -> None:
    """Build the report for the exported A-share sample."""
    destination = generate_signal_review(DATABASE, OUTPUT, SYMBOLS)
    print(f"wrote signal review to {destination}")


if __name__ == "__main__":
    main()
