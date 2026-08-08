"""Tests for the RSI 50 signal engine."""

from datetime import datetime, timedelta

import pytest

from quant_trade.rsi50 import (
    Bar,
    Direction,
    FeatureCalculationResult,
    Rsi50Config,
    Rsi50SignalEngine,
    Signal,
    SignalCalculationInput,
    SignalCalculationResult,
    SignalFeature,
    calculate_breakout_feature,
    calculate_fast_ma_feature,
    calculate_long_signal,
    calculate_rsi_trigger_feature,
    calculate_rsi_zone_feature,
    calculate_short_signal,
    calculate_slow_ma_feature,
    is_pivot_high,
    is_pivot_low,
    moving_average_angle,
)


def _trigger_rsi_45_to_55_closes() -> list[float]:
    return (
        [80.0] * 35
        + [118.0, 119.0, 120.0, 121.0, 122.0]
        + [
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
    )


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


def test_config_rejects_invalid_trigger_rsi_range() -> None:
    with pytest.raises(ValueError, match="trigger_rsi_low"):
        Rsi50Config(trigger_rsi_low=51.0, trigger_rsi_high=50.0)


def test_config_rejects_invalid_ma20_angle_settings() -> None:
    with pytest.raises(ValueError, match="ma_fast_angle_bars"):
        Rsi50Config(ma_fast_angle_bars=1)
    with pytest.raises(ValueError, match="ma_fast_min_angle_degrees"):
        Rsi50Config(ma_fast_min_angle_degrees=90.0)


def test_moving_average_angle_uses_recent_three_bar_regression() -> None:
    assert moving_average_angle([10.0, 11.0, 12.0]) == pytest.approx(45.0)
    assert moving_average_angle([12.0, 11.0, 10.0]) == pytest.approx(-45.0)
    assert moving_average_angle([None, 11.0, 12.0]) is None


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
    signals = [
        signal
        for signal in _signals_for_closes(_trigger_rsi_45_to_55_closes())
        if signal
    ]

    assert len(signals) == 1
    assert signals[0].direction is Direction.LONG
    assert signals[0].first_pivot_index == 40
    assert signals[0].second_pivot_index == 47
    assert signals[0].close > signals[0].neckline + 0.1 * signals[0].atr
    assert 45.0 <= signals[0].rsi <= 55.0


def test_engine_rejects_long_when_ma20_angle_is_below_40_degrees() -> None:
    closes = _trigger_rsi_45_to_55_closes()
    closes[-4:] = [86.0, 87.0, 88.0, 93.0]

    signals = [signal for signal in _signals_for_closes(closes) if signal]

    assert signals == []


def test_engine_emits_short_after_m_top_breakdown() -> None:
    closes = [220.0 - close for close in _trigger_rsi_45_to_55_closes()]
    signals = [signal for signal in _signals_for_closes(closes) if signal]

    assert len(signals) == 1
    assert signals[0].direction is Direction.SHORT
    assert signals[0].first_pivot_index == 40
    assert signals[0].second_pivot_index == 47
    assert signals[0].close < signals[0].neckline - 0.1 * signals[0].atr
    assert 45.0 <= signals[0].rsi <= 55.0


def test_direction_calculators_share_input_output_and_leave_filtering_outside() -> None:
    long_engine = Rsi50SignalEngine()
    short_engine = Rsi50SignalEngine()
    start = datetime(2025, 1, 1)
    closes = _trigger_rsi_45_to_55_closes()
    for index, close in enumerate(closes):
        long_engine.on_bar(
            Bar(start + timedelta(days=index), close, close + 0.5, close - 0.5, close)
        )
        short_close = 220.0 - close
        short_engine.on_bar(
            Bar(
                start + timedelta(days=index),
                short_close,
                short_close + 0.5,
                short_close - 0.5,
                short_close,
            )
        )

    long_result = long_engine.calculate_current_signal(Direction.LONG)
    short_result = short_engine.calculate_current_signal(Direction.SHORT)

    assert isinstance(long_result, SignalCalculationResult)
    assert isinstance(short_result, SignalCalculationResult)
    assert isinstance(long_result.inputs, SignalCalculationInput)
    assert isinstance(short_result.inputs, SignalCalculationInput)
    assert calculate_long_signal(long_result.inputs) == long_result
    assert calculate_short_signal(short_result.inputs) == short_result
    assert long_result.matched is True
    assert short_result.matched is True

    feature_calculators = (
        calculate_rsi_zone_feature,
        calculate_fast_ma_feature,
        calculate_slow_ma_feature,
        calculate_breakout_feature,
        calculate_rsi_trigger_feature,
    )
    feature_results = tuple(
        calculator(long_result.inputs) for calculator in feature_calculators
    )
    assert all(
        isinstance(result, FeatureCalculationResult) for result in feature_results
    )
    assert {result.feature for result in feature_results} == set(SignalFeature)
    assert feature_results == long_result.features
