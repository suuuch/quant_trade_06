"""Tests for the RSI trend-following signal engine."""

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
    calculate_fast_ma_feature,
    calculate_long_signal,
    calculate_rsi_latest_range_feature,
    calculate_rsi_trigger_feature,
    calculate_short_signal,
    moving_average_angle,
)


def _trigger_rsi_45_to_55_closes() -> list[float]:
    return (
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


def _signals_for_closes(
    closes: list[float],
    config: Rsi50Config | None = None,
) -> list[Signal | None]:
    engine = Rsi50SignalEngine(config)
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


def test_config_rejects_invalid_directional_rsi_ranges() -> None:
    with pytest.raises(ValueError, match="long_trigger_rsi_low"):
        Rsi50Config(long_trigger_rsi_low=59.0, long_trigger_rsi_high=58.0)
    with pytest.raises(ValueError, match="short_trigger_rsi_low"):
        Rsi50Config(short_trigger_rsi_low=51.0, short_trigger_rsi_high=50.0)

    with pytest.raises(ValueError, match="recent_rsi_days"):
        Rsi50Config(recent_rsi_days=4)


def test_config_rejects_invalid_ma20_angle_settings() -> None:
    with pytest.raises(ValueError, match="ma_fast_angle_bars"):
        Rsi50Config(ma_fast_angle_bars=1)
    with pytest.raises(ValueError, match="ma_fast_min_angle_degrees"):
        Rsi50Config(ma_fast_min_angle_degrees=90.0)


def test_config_returns_directional_rsi_filter_ranges() -> None:
    config = Rsi50Config()

    long_filter = config.rsi_filter_for(Direction.LONG)
    short_filter = config.rsi_filter_for(Direction.SHORT)

    assert (
        long_filter.trigger_low,
        long_filter.trigger_high,
    ) == (50.0, 58.0)
    assert (
        short_filter.trigger_low,
        short_filter.trigger_high,
    ) == (42.0, 50.0)


def test_moving_average_angle_uses_recent_fifteen_bar_regression() -> None:
    rising = [float(value) for value in range(10, 25)]
    falling = list(reversed(rising))

    assert moving_average_angle(rising) == pytest.approx(45.0)
    assert moving_average_angle(falling) == pytest.approx(-45.0)
    assert moving_average_angle([None, *rising[1:]]) is None


def test_engine_rejects_out_of_order_bars() -> None:
    engine = Rsi50SignalEngine()
    timestamp = datetime(2026, 1, 1)
    bar = Bar(timestamp, 10.0, 11.0, 9.0, 10.0, 100.0)
    engine.on_bar(bar)

    with pytest.raises(ValueError, match="strictly increasing"):
        engine.on_bar(bar)


def test_engine_emits_long_when_trend_and_rsi_ranges_match() -> None:
    signals = [
        signal
        for signal in _signals_for_closes(_trigger_rsi_45_to_55_closes())
        if signal
    ]

    assert signals
    assert all(signal.direction is Direction.LONG for signal in signals)
    assert all(50.0 <= signal.rsi <= 58.0 for signal in signals)


def test_engine_rejects_long_when_ma20_angle_is_below_threshold() -> None:
    closes = _trigger_rsi_45_to_55_closes()
    closes[-4:] = [86.0, 87.0, 88.0, 93.0]

    engine = Rsi50SignalEngine(Rsi50Config(ma_fast_min_angle_degrees=80.0))
    start = datetime(2025, 1, 1)
    for index, close in enumerate(closes):
        engine.on_bar(
            Bar(start + timedelta(days=index), close, close + 0.5, close - 0.5, close)
        )

    result = engine.calculate_current_signal(Direction.LONG)
    assert result is not None
    assert result.fast_trend_pass is False


def test_engine_emits_short_when_trend_and_rsi_ranges_match() -> None:
    closes = [220.0 - close for close in _trigger_rsi_45_to_55_closes()]
    signals = [signal for signal in _signals_for_closes(closes) if signal]

    assert signals
    assert all(signal.direction is Direction.SHORT for signal in signals)
    assert all(42.0 <= signal.rsi <= 50.0 for signal in signals)


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
    long_latest_rsi = long_result.feature(SignalFeature.RSI_LATEST_RANGE)
    short_latest_rsi = short_result.feature(SignalFeature.RSI_LATEST_RANGE)
    long_direction_rsi = long_result.feature(SignalFeature.RSI_DIRECTION_RANGE)
    short_direction_rsi = short_result.feature(SignalFeature.RSI_DIRECTION_RANGE)
    assert (long_latest_rsi.minimum, long_latest_rsi.maximum) == (40.0, 60.0)
    assert (short_latest_rsi.minimum, short_latest_rsi.maximum) == (40.0, 60.0)
    assert (long_direction_rsi.minimum, long_direction_rsi.maximum) == (50.0, 58.0)
    assert (short_direction_rsi.minimum, short_direction_rsi.maximum) == (42.0, 50.0)

    feature_calculators = (
        calculate_rsi_latest_range_feature,
        calculate_fast_ma_feature,
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
