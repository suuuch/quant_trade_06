"""Tests for latest-bar database scanning and PNG rendering."""

from pathlib import Path

import pandas as pd

from quant_trade.rsi50 import Direction
from quant_trade.scanner import render_signal_chart, scan_symbol_frame


def _latest_long_frame() -> pd.DataFrame:
    closes = [100.0 + index * 0.5 for index in range(40)] + [
        115.0,
        117.0,
        119.5,
        119.0,
        118.0,
        117.5,
        117.0,
        116.0,
        117.0,
        118.0,
        119.0,
        121.5,
    ]
    index = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    close = pd.Series(closes, index=index)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 100_000.0,
        },
        index=index,
    )


def test_scan_symbol_returns_only_latest_signal() -> None:
    match = scan_symbol_frame("000001.SZ", "Test", "Bank", _latest_long_frame())

    assert match is not None
    assert match.signal.direction is Direction.LONG
    assert match.signal.timestamp == _latest_long_frame().index[-1]


def test_render_signal_chart_writes_png(tmp_path: Path) -> None:
    match = scan_symbol_frame("000001.SZ", "Test", "Bank", _latest_long_frame())
    assert match is not None

    output = render_signal_chart(match, tmp_path / "signal.png")

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
