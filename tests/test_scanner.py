"""Tests for latest-bar database scanning and PNG rendering."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pandas as pd
import pytest
from matplotlib import pyplot as plt
from matplotlib.figure import Figure

from quant_trade.rsi50 import Direction
from quant_trade.scanner import (
    _A_SHARE_SCAN_ON_DATE_QUERY,
    _US_SHARE_SCAN_ON_DATE_QUERY,
    _US_SHARE_SCAN_QUERY,
    DataFreshnessError,
    MarketDataStatus,
    _evaluate_rows,
    render_signal_chart,
    render_signal_sheet,
    scan_symbol_frame,
    select_matches_for_delivery,
    sort_matches_by_market_cap,
    validate_market_data_freshness,
)


def test_chinese_font_candidates_include_linux_fonts() -> None:
    candidates = plt.rcParams["font.sans-serif"]

    assert "Noto Sans CJK SC" in candidates
    assert "WenQuanYi Zen Hei" in candidates


def _latest_long_frame() -> pd.DataFrame:
    closes = (
        [45.04] * 35
        + [118.70, 128.32, 128.33, 132.96, 138.33]
        + [
            73.02,
            74.40,
            79.16,
            76.24,
            76.09,
            74.44,
            74.14,
            73.02,
            74.83,
            74.75,
            75.99,
            80.63,
        ]
    )
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


def test_scan_symbol_defaults_to_a_share_market() -> None:
    match = scan_symbol_frame("000001.SZ", "Test", "Bank", _latest_long_frame())

    assert match is not None
    assert match.market == "a"


def test_scan_symbol_accepts_us_market() -> None:
    match = scan_symbol_frame(
        "US.AAPL",
        "Apple",
        "Consumer Electronics",
        _latest_long_frame(),
        market="us",
    )

    assert match is not None
    assert match.market == "us"


def test_render_signal_chart_marks_last_candle_with_translucent_dashed_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    match = scan_symbol_frame("000001.SZ", "Test", "Bank", _latest_long_frame())
    assert match is not None
    closed: list[Figure] = []
    monkeypatch.setattr(plt, "close", closed.append)

    render_signal_chart(match, tmp_path / "signal.png")

    price_axis = closed[0].axes[0]
    last_candle_x = len(match.frame) - 1
    signal_line = price_axis.lines[-1]
    assert signal_line.get_xdata()[0] == last_candle_x
    assert signal_line.get_xdata()[1] == last_candle_x
    assert signal_line.get_linestyle() == "--"
    assert signal_line.get_alpha() == pytest.approx(0.6)
    signal_label = price_axis.texts[0]
    assert signal_label.get_text() == "B"
    assert signal_label.get_position()[0] == last_candle_x


def test_us_market_uses_ma20_direction_without_angle_threshold() -> None:
    closes = [91.64] * 35 + [
        99.13,
        102.79,
        104.92,
        122.63,
        134.94,
        100.01,
        100.69,
        103.53,
        102.50,
        100.25,
        101.13,
        101.62,
        100.01,
        101.89,
        102.34,
        101.01,
        104.46,
    ]
    base = 91.64
    closes = [base + (close - base) * 0.6 for close in closes]
    index = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    close = pd.Series(closes, index=index)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 100_000.0,
        },
        index=index,
    )

    a_share_match = scan_symbol_frame("000001.SZ", "Test", "Test", frame)
    us_match = scan_symbol_frame("US.TEST", "Test", "Test", frame, market="us")

    assert a_share_match is None
    assert us_match is not None
    assert us_match.engine.config.ma_fast_min_angle_degrees is None


def test_scan_symbol_rejects_unknown_market() -> None:
    with pytest.raises(ValueError, match="market must be 'a' or 'us'"):
        scan_symbol_frame(
            "INVALID",
            "Test",
            "Test",
            _latest_long_frame(),
            market="hk",  # type: ignore[arg-type]
        )


def test_us_scan_query_escapes_psycopg_percent_literal() -> None:
    assert "LIKE 'US.%%'" in _US_SHARE_SCAN_QUERY


def test_date_scan_queries_apply_requested_cutoff() -> None:
    assert "WHERE d.trade_date <= %s" in _A_SHARE_SCAN_ON_DATE_QUERY
    assert "AND k.kline_date <= %s" in _US_SHARE_SCAN_ON_DATE_QUERY


def test_us_scan_query_applies_price_market_cap_and_turnover_filters() -> None:
    assert "p.market_cap > 1000000000" in _US_SHARE_SCAN_QUERY
    assert "count(close_raw * volume_lots) = 50" in _US_SHARE_SCAN_QUERY
    assert "max(close_raw) FILTER (WHERE recent_rank = 1) > 5.0" in (
        _US_SHARE_SCAN_QUERY
    )
    assert "avg(close_raw * volume_lots) > 50000000.0" in _US_SHARE_SCAN_QUERY
    assert "percentile_cont(0.5) WITHIN GROUP" in _US_SHARE_SCAN_QUERY
    assert ") > 50000000.0" in _US_SHARE_SCAN_QUERY


def test_us_database_rows_accept_decimal_adjustment_factors() -> None:
    frame = _latest_long_frame()
    rows = _database_rows(frame)

    match, stale = _evaluate_rows(rows, frame.index[-1].date(), "us")

    assert stale is False
    assert match is not None
    assert match.symbol == "US.AAPL"


def test_us_database_rows_skip_invalid_historical_ohlc(
    caplog: pytest.LogCaptureFixture,
) -> None:
    frame = _latest_long_frame()
    rows = _database_rows(frame)
    invalid = list(rows[0])
    invalid[4] = 20.0
    invalid[6] = 21.0
    rows[0] = tuple(invalid)

    match, stale = _evaluate_rows(rows, frame.index[-1].date(), "us")

    assert stale is False
    assert match is not None
    assert "skipping 1 invalid OHLC bar(s) for US.AAPL" in caplog.text


def test_us_database_rows_treat_invalid_latest_ohlc_as_stale() -> None:
    frame = _latest_long_frame()
    rows = _database_rows(frame)
    invalid = list(rows[-1])
    invalid[4] = 20.0
    invalid[6] = 21.0
    rows[-1] = tuple(invalid)

    match, stale = _evaluate_rows(rows, frame.index[-1].date(), "us")

    assert stale is True
    assert match is None


def _database_rows(frame: pd.DataFrame) -> list[tuple[object, ...]]:
    return [
        (
            "US.AAPL",
            cast(pd.Timestamp, timestamp).date(),
            "Apple",
            "Consumer Electronics",
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
            Decimal("1.0"),
            Decimal("1.0"),
            Decimal("416797788.568"),
            None,
        )
        for timestamp, row in frame.iterrows()
    ]


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
