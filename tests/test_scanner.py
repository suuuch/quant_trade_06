"""Tests for latest-bar database scanning and PNG rendering."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from quant_trade.rsi50 import Direction
from quant_trade.scanner import (
    DataFreshnessError,
    MarketDataStatus,
    render_signal_chart,
    render_signal_sheet,
    scan_symbol_frame,
    select_matches_for_delivery,
    sort_matches_by_market_cap,
    validate_market_data_freshness,
)


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


def test_render_signal_sheet_combines_charts(tmp_path: Path) -> None:
    match = scan_symbol_frame("000001.SZ", "Test", "Bank", _latest_long_frame())
    assert match is not None
    chart = render_signal_chart(match, tmp_path / "signal.png")

    output = render_signal_sheet([chart, chart], tmp_path / "batch.png")

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_matches_are_sorted_by_market_cap_descending() -> None:
    frame = _latest_long_frame()
    small = scan_symbol_frame(
        "000001.SZ",
        "Small",
        "Test",
        frame,
        market_cap_cny=10_000_000_000.0,
    )
    large = scan_symbol_frame(
        "600000.SH",
        "Large",
        "Test",
        frame,
        market_cap_cny=100_000_000_000.0,
    )
    missing = scan_symbol_frame("300001.SZ", "Missing", "Test", frame)
    assert small is not None and large is not None and missing is not None

    ordered = sort_matches_by_market_cap([small, missing, large])

    assert [match.symbol for match in ordered] == [
        "600000.SH",
        "000001.SZ",
        "300001.SZ",
    ]


def test_delivery_limit_keeps_highest_ranked_matches() -> None:
    frame = _latest_long_frame()
    matches = [
        scan_symbol_frame(f"00000{index}.SZ", str(index), "Test", frame)
        for index in range(1, 4)
    ]
    assert all(match is not None for match in matches)
    ranked = [match for match in matches if match is not None]

    selected = select_matches_for_delivery(ranked, max_send=2)

    assert [match.symbol for match in selected] == ["000001.SZ", "000002.SZ"]


def test_zero_delivery_limit_explicitly_allows_all_matches() -> None:
    frame = _latest_long_frame()
    match = scan_symbol_frame("000001.SZ", "Test", "Test", frame)
    assert match is not None

    assert select_matches_for_delivery([match], max_send=0) == [match]


def test_trading_day_missing_today_data_triggers_fuse() -> None:
    status = MarketDataStatus(
        today=date(2026, 8, 6),
        is_trading_day=True,
        daily_latest=date(2026, 8, 5),
        adjustment_latest=date(2026, 8, 5),
    )

    with pytest.raises(DataFreshnessError, match="2026-08-06 is a trading day"):
        validate_market_data_freshness(status)


def test_trading_day_complete_data_passes_fuse() -> None:
    status = MarketDataStatus(
        today=date(2026, 8, 6),
        is_trading_day=True,
        daily_latest=date(2026, 8, 6),
        adjustment_latest=date(2026, 8, 6),
    )

    validate_market_data_freshness(status)


def test_non_trading_day_allows_previous_session_data() -> None:
    status = MarketDataStatus(
        today=date(2026, 8, 8),
        is_trading_day=False,
        daily_latest=date(2026, 8, 7),
        adjustment_latest=date(2026, 8, 7),
    )

    validate_market_data_freshness(status)
