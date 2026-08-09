"""Tests for independent W-bottom and M-top entry signals."""

from datetime import datetime, timedelta

from quant_trade.rsi50 import Bar, Direction
from quant_trade.wm_pattern import (
    WmPatternConfig,
    WmPatternEngine,
    WmSignal,
    is_pivot_high,
    is_pivot_low,
)


def _w_bottom_closes() -> list[float]:
    return [80.0] * 35 + [
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
        94.0,
    ]


def _signals(closes: list[float]) -> list[WmSignal]:
    engine = WmPatternEngine()
    start = datetime(2025, 1, 1)
    return [
        signal
        for index, close in enumerate(closes)
        for signal in engine.on_bar(
            Bar(
                start + timedelta(days=index),
                close,
                close + 0.5,
                close - 0.5,
                close,
            )
        )
    ]


def test_daily_pivots_require_three_right_bars() -> None:
    highs = [10.0, 11.0, 12.0, 15.0, 14.0, 13.0]
    lows = [15.0, 14.0, 13.0, 10.0, 11.0, 12.0]

    assert is_pivot_high(highs, 3) is False
    assert is_pivot_low(lows, 3) is False
    assert is_pivot_high([*highs, 12.0], 3) is True
    assert is_pivot_low([*lows, 13.0], 3) is True


def test_config_rejects_invalid_pattern_distance() -> None:
    try:
        WmPatternConfig(min_pattern_distance=31, max_pattern_distance=30)
    except ValueError as error:
        assert "min_pattern_distance" in str(error)
    else:
        raise AssertionError("invalid pattern distance was accepted")


def test_w_bottom_emits_long_neckline_entry() -> None:
    signals = _signals(_w_bottom_closes())

    assert len(signals) == 1
    signal = signals[0]
    assert signal.direction is Direction.LONG
    assert signal.first_pivot_index == 40
    assert signal.second_pivot_index == 47
    assert signal.close > signal.neckline + 0.1 * signal.atr


def test_m_top_emits_short_neckline_entry() -> None:
    signals = _signals([220.0 - close for close in _w_bottom_closes()])

    assert len(signals) == 1
    signal = signals[0]
    assert signal.direction is Direction.SHORT
    assert signal.first_pivot_index == 40
    assert signal.second_pivot_index == 47
    assert signal.close < signal.neckline - 0.1 * signal.atr
