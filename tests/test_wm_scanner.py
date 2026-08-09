"""Tests for W/M latest-bar scanning and charts."""

from pathlib import Path

import pandas as pd

from quant_trade.rsi50 import Direction
from quant_trade.wm_scanner import render_wm_signal_chart, scan_wm_symbol_frame


def _w_bottom_frame() -> pd.DataFrame:
    closes = [80.0] * 35 + [
        118.0,
        119.0,
        120.0,
        121.0,
        122.0,
        85.0,
        87.0,
        89.0,
        87.0,
        86.0,
        87.0,
        86.0,
        85.0,
        85.5,
        85.5,
        100.0,
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


def test_scan_wm_symbol_returns_latest_w_entry() -> None:
    matches = scan_wm_symbol_frame("000001.SZ", "Test", "Bank", _w_bottom_frame())

    assert len(matches) == 1
    assert matches[0].signal.direction is Direction.LONG
    assert matches[0].signal.timestamp == _w_bottom_frame().index[-1]


def test_scan_wm_symbol_keeps_requested_market() -> None:
    matches = scan_wm_symbol_frame(
        "US.TEST",
        "Test",
        "Test",
        _w_bottom_frame(),
        market="us",
    )

    assert matches[0].market == "us"


def test_render_wm_chart_writes_png(tmp_path: Path) -> None:
    match = scan_wm_symbol_frame(
        "000001.SZ",
        "Test",
        "Bank",
        _w_bottom_frame(),
    )[0]

    output = render_wm_signal_chart(match, tmp_path / "wm.png")

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
