"""Tests for the RSI 50 signal engine."""

from datetime import datetime, timedelta

import pytest

from quant_trade.rsi50 import (
    Bar,
    Direction,
    Rsi50Config,
    Rsi50SignalEngine,
    Signal,
    is_pivot_high,
    is_pivot_low,
)


def _pattern_closes() -> list[float]:
    return [100.0 + index * 0.5 for index in range(40)] + [
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


def _signals_for_closes(closes: list[float]) -> list[Signal | None]:
    engine = Rsi50SignalEngine()
    start = datetime(2025, 1, 1)
    return [
        engine.on_bar(
            Bar(
                start + timedelta(days=index),
                close,
                close + 0.5,
                close - 0.5,
                close,
                1_000.0,
            )
        )
        for index, close in enumerate(closes)
    ]


def test_daily_pivot_requires_three_right_bars() -> None:
    highs = [10.0, 11.0, 12.0, 15.0, 14.0, 13.0]
    assert not is_pivot_high(highs, 3, left=3, right=3)

    highs.append(12.0)
    assert is_pivot_high(highs, 3, left=3, right=3)


def test_pivot_low_is_mirror_of_pivot_high() -> None:
    lows = [15.0, 14.0, 13.0, 10.0, 11.0, 12.0, 13.0]
    assert is_pivot_low(lows, 3, left=3, right=3)


def test_config_rejects_invalid_pattern_distance() -> None:
    with pytest.raises(ValueError, match="min_pattern_distance"):
        Rsi50Config(min_pattern_distance=31, max_pattern_distance=30)


def test_engine_rejects_out_of_order_bars() -> None:
    engine = Rsi50SignalEngine()
    timestamp = datetime(2026, 1, 1)
    bar = Bar(timestamp, 10.0, 11.0, 9.0, 10.0, 100.0)
    engine.on_bar(bar)

    with pytest.raises(ValueError, match="strictly increasing"):
        engine.on_bar(bar)


def test_pivot_is_added_only_after_confirmation_delay() -> None:
    config = Rsi50Config(rsi_period=2, ma_fast=2, ma_slow=3, atr_period=2)
    engine = Rsi50SignalEngine(config)
    lows = [10.0, 9.0, 8.0, 5.0, 7.0, 8.0, 9.0]
    start = datetime(2026, 1, 1)

    for index, low in enumerate(lows[:-1]):
        engine.on_bar(
            Bar(
                start + timedelta(days=index),
                low + 1.0,
                low + 2.0,
                low,
                low + 1.0,
            )
        )
    assert engine.pivot_lows == []

    engine.on_bar(
        Bar(
            start + timedelta(days=6),
            lows[6] + 1.0,
            lows[6] + 2.0,
            lows[6],
            lows[6] + 1.0,
        )
    )
    assert engine.pivot_lows[0].index == 3


def test_engine_emits_long_after_w_bottom_breakout() -> None:
    signals = [signal for signal in _signals_for_closes(_pattern_closes()) if signal]

    assert len(signals) == 1
    assert signals[0].direction is Direction.LONG
    assert signals[0].first_pivot_index == 40
    assert signals[0].second_pivot_index == 47
    assert signals[0].close > signals[0].neckline + 0.1 * signals[0].atr
    assert signals[0].rsi > 55.0


def test_engine_emits_short_after_m_top_breakdown() -> None:
    closes = [220.0 - close for close in _pattern_closes()]
    signals = [signal for signal in _signals_for_closes(closes) if signal]

    assert len(signals) == 1
    assert signals[0].direction is Direction.SHORT
    assert signals[0].first_pivot_index == 40
    assert signals[0].second_pivot_index == 47
    assert signals[0].close < signals[0].neckline - 0.1 * signals[0].atr
    assert signals[0].rsi < 45.0
