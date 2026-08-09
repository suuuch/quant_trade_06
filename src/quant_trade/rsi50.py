"""Bar-by-bar signal engine for the daily RSI trend-following strategy."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import atan, degrees


class Direction(StrEnum):
    """Supported trade directions."""

    LONG = "long"
    SHORT = "short"


def moving_average_angle(
    values: Sequence[float | None],
    bars: int = 15,
) -> float | None:
    """Return the regression angle in degrees for recent moving-average values."""
    if bars < 2:
        raise ValueError("bars must be at least 2")
    if len(values) < bars:
        return None
    window = values[-bars:]
    if any(value is None for value in window):
        return None
    numeric = [float(value) for value in window if value is not None]
    x_mean = (bars - 1) / 2.0
    y_mean = sum(numeric) / bars
    numerator = sum(
        (index - x_mean) * (value - y_mean) for index, value in enumerate(numeric)
    )
    denominator = sum((index - x_mean) ** 2 for index in range(bars))
    return degrees(atan(numerator / denominator))


@dataclass(frozen=True)
class Bar:
    """One completed daily OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be the greatest OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be the smallest OHLC value")


@dataclass(frozen=True)
class RsiDirectionFilter:
    """RSI ranges required for one trade direction."""

    trigger_low: float
    trigger_high: float


@dataclass(frozen=True)
class Rsi50Config:
    """Parameters fixed by the daily RSI trend-following strategy document."""

    rsi_period: int = 14
    ma_fast: int = 20
    ma_slow: int = 30
    atr_period: int = 14
    rsi_zone_low: float = 40.0
    rsi_zone_high: float = 60.0
    long_trigger_rsi_low: float = 50.0
    long_trigger_rsi_high: float = 58.0
    short_trigger_rsi_low: float = 42.0
    short_trigger_rsi_high: float = 50.0
    recent_rsi_days: int = 5
    ma_fast_angle_bars: int = 15
    ma_fast_min_angle_degrees: float | None = 40.0

    def __post_init__(self) -> None:
        positive_ints = (
            self.rsi_period,
            self.ma_fast,
            self.ma_slow,
            self.atr_period,
        )
        if any(value <= 0 for value in positive_ints):
            raise ValueError("period and distance parameters must be positive")
        if self.ma_fast >= self.ma_slow:
            raise ValueError("ma_fast must be less than ma_slow")
        if self.rsi_zone_low >= self.rsi_zone_high:
            raise ValueError("rsi_zone_low must be less than rsi_zone_high")
        if self.long_trigger_rsi_low > self.long_trigger_rsi_high:
            raise ValueError(
                "long_trigger_rsi_low must not exceed long_trigger_rsi_high"
            )
        if self.short_trigger_rsi_low > self.short_trigger_rsi_high:
            raise ValueError(
                "short_trigger_rsi_low must not exceed short_trigger_rsi_high"
            )
        if self.recent_rsi_days < 5:
            raise ValueError("recent_rsi_days must be at least 5")
        if self.ma_fast_angle_bars < 2:
            raise ValueError("ma_fast_angle_bars must be at least 2")
        if self.ma_fast_min_angle_degrees is not None and not (
            0.0 < self.ma_fast_min_angle_degrees < 90.0
        ):
            raise ValueError("ma_fast_min_angle_degrees must be in (0, 90)")

    def rsi_filter_for(self, direction: Direction) -> RsiDirectionFilter:
        """Return the configured RSI filter ranges for one direction."""
        if direction is Direction.LONG:
            return RsiDirectionFilter(
                trigger_low=self.long_trigger_rsi_low,
                trigger_high=self.long_trigger_rsi_high,
            )
        return RsiDirectionFilter(
            trigger_low=self.short_trigger_rsi_low,
            trigger_high=self.short_trigger_rsi_high,
        )


@dataclass(frozen=True)
class Signal:
    """A trade signal produced on a completed daily bar."""

    direction: Direction
    timestamp: datetime
    close: float
    rsi: float
    atr: float


@dataclass(frozen=True)
class SignalCalculationInput:
    """Shared immutable input for one directional signal calculation."""

    config: Rsi50Config
    direction: Direction
    bar: Bar
    rsi: float
    atr: float
    fast_ma: float
    previous_fast_ma: float
    fast_ma_angle: float | None
    rsi_history: tuple[float | None, ...]


class SignalFeature(StrEnum):
    """Features evaluated for every directional signal candidate."""

    RSI_LATEST_RANGE = "rsi_latest_range"
    FAST_MA_TREND = "fast_ma_trend"
    RSI_DIRECTION_RANGE = "rsi_direction_range"


@dataclass(frozen=True)
class FeatureCalculationResult:
    """Uniform output from one feature calculation."""

    feature: SignalFeature
    passed: bool
    observed: float | None = None
    observed_index: int | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class SignalCalculationResult:
    """Uniform condition results returned by each signal calculator."""

    inputs: SignalCalculationInput
    features: tuple[FeatureCalculationResult, ...]

    def feature(self, name: SignalFeature) -> FeatureCalculationResult:
        """Return one named feature result."""
        for result in self.features:
            if result.feature is name:
                return result
        raise ValueError(f"missing signal feature: {name}")

    @property
    def fast_trend_pass(self) -> bool:
        """Return the MA20 feature result."""
        return self.feature(SignalFeature.FAST_MA_TREND).passed

    @property
    def rsi_trigger_pass(self) -> bool:
        """Return the directional recent-RSI feature result."""
        return self.feature(SignalFeature.RSI_DIRECTION_RANGE).passed

    @property
    def rsi_latest_range_pass(self) -> bool:
        """Return the latest RSI broad-range feature result."""
        return self.feature(SignalFeature.RSI_LATEST_RANGE).passed

    @property
    def matched(self) -> bool:
        """Return whether every directional signal condition passed."""
        return all(result.passed for result in self.features)


def calculate_rsi_latest_range_feature(
    inputs: SignalCalculationInput,
) -> FeatureCalculationResult:
    """Calculate whether the latest RSI is in the broad screening range."""
    return FeatureCalculationResult(
        feature=SignalFeature.RSI_LATEST_RANGE,
        passed=inputs.config.rsi_zone_low <= inputs.rsi <= inputs.config.rsi_zone_high,
        observed=inputs.rsi,
        observed_index=len(inputs.rsi_history) - 1,
        minimum=inputs.config.rsi_zone_low,
        maximum=inputs.config.rsi_zone_high,
    )


def calculate_fast_ma_feature(
    inputs: SignalCalculationInput,
) -> FeatureCalculationResult:
    """Calculate the directional MA20 trend feature."""
    direction = inputs.direction
    threshold = inputs.config.ma_fast_min_angle_degrees
    if threshold is None:
        observed = inputs.fast_ma - inputs.previous_fast_ma
        passed = observed > 0.0 if direction is Direction.LONG else observed < 0.0
        return FeatureCalculationResult(
            feature=SignalFeature.FAST_MA_TREND,
            passed=passed,
            observed=observed,
            minimum=0.0 if direction is Direction.LONG else None,
            maximum=0.0 if direction is Direction.SHORT else None,
        )
    observed = inputs.fast_ma_angle
    passed = observed is not None and (
        observed > threshold if direction is Direction.LONG else observed < -threshold
    )
    return FeatureCalculationResult(
        feature=SignalFeature.FAST_MA_TREND,
        passed=passed,
        observed=observed,
        minimum=threshold if direction is Direction.LONG else None,
        maximum=-threshold if direction is Direction.SHORT else None,
    )


def calculate_rsi_trigger_feature(
    inputs: SignalCalculationInput,
) -> FeatureCalculationResult:
    """Check that the recent RSI window is in the directional range."""
    rsi_filter = inputs.config.rsi_filter_for(inputs.direction)
    window = inputs.rsi_history[-inputs.config.recent_rsi_days :]
    complete = len(window) == inputs.config.recent_rsi_days and all(
        value is not None for value in window
    )
    passed = complete and all(
        rsi_filter.trigger_low <= value <= rsi_filter.trigger_high
        for value in window
        if value is not None
    )
    return FeatureCalculationResult(
        feature=SignalFeature.RSI_DIRECTION_RANGE,
        passed=passed,
        observed=inputs.rsi,
        minimum=rsi_filter.trigger_low,
        maximum=rsi_filter.trigger_high,
    )


def _calculate_signal_features(
    inputs: SignalCalculationInput,
) -> tuple[FeatureCalculationResult, ...]:
    return (
        calculate_rsi_latest_range_feature(inputs),
        calculate_fast_ma_feature(inputs),
        calculate_rsi_trigger_feature(inputs),
    )


def calculate_long_signal(
    inputs: SignalCalculationInput,
) -> SignalCalculationResult:
    """Calculate all long conditions without filtering or mutating engine state."""
    if inputs.direction is not Direction.LONG:
        raise ValueError("long signal calculation requires long direction")
    return SignalCalculationResult(
        inputs=inputs,
        features=_calculate_signal_features(inputs),
    )


def calculate_short_signal(
    inputs: SignalCalculationInput,
) -> SignalCalculationResult:
    """Calculate all short conditions without filtering or mutating engine state."""
    if inputs.direction is not Direction.SHORT:
        raise ValueError("short signal calculation requires short direction")
    return SignalCalculationResult(
        inputs=inputs,
        features=_calculate_signal_features(inputs),
    )


class Rsi50SignalEngine:
    """Evaluate the strategy incrementally without looking ahead."""

    def __init__(self, config: Rsi50Config | None = None) -> None:
        self.config = config or Rsi50Config()
        self.bars: list[Bar] = []
        self.rsi_values: list[float | None] = []
        self.atr_values: list[float | None] = []
        self.fast_ma_values: list[float | None] = []
        self.slow_ma_values: list[float | None] = []
        self._average_gain: float | None = None
        self._average_loss: float | None = None
        self._average_true_range: float | None = None

    def on_bar(self, bar: Bar) -> Signal | None:
        """Process one completed daily bar and return at most one signal."""
        if self.bars and bar.timestamp <= self.bars[-1].timestamp:
            raise ValueError("bars must be supplied in strictly increasing order")

        self.bars.append(bar)
        self._update_indicators()
        return self._evaluate_signal()

    def _update_indicators(self) -> None:
        closes = [bar.close for bar in self.bars]
        self.fast_ma_values.append(self._simple_average(closes, self.config.ma_fast))
        self.slow_ma_values.append(self._simple_average(closes, self.config.ma_slow))
        self.rsi_values.append(self._next_rsi())
        self.atr_values.append(self._next_atr())

    @staticmethod
    def _simple_average(values: list[float], period: int) -> float | None:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _next_rsi(self) -> float | None:
        period = self.config.rsi_period
        if len(self.bars) <= period:
            return None

        if self._average_gain is None or self._average_loss is None:
            changes = [
                self.bars[index].close - self.bars[index - 1].close
                for index in range(1, period + 1)
            ]
            self._average_gain = sum(max(change, 0.0) for change in changes) / period
            self._average_loss = sum(max(-change, 0.0) for change in changes) / period
        else:
            change = self.bars[-1].close - self.bars[-2].close
            self._average_gain = (
                self._average_gain * (period - 1) + max(change, 0.0)
            ) / period
            self._average_loss = (
                self._average_loss * (period - 1) + max(-change, 0.0)
            ) / period

        if self._average_loss == 0:
            return 100.0
        relative_strength = self._average_gain / self._average_loss
        return 100.0 - 100.0 / (1.0 + relative_strength)

    def _next_atr(self) -> float | None:
        period = self.config.atr_period
        bar = self.bars[-1]
        if len(self.bars) == 1:
            true_range = bar.high - bar.low
        else:
            previous_close = self.bars[-2].close
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )

        if len(self.bars) < period:
            return None
        if self._average_true_range is None:
            true_ranges: list[float] = []
            for index in range(period):
                current = self.bars[index]
                if index == 0:
                    true_ranges.append(current.high - current.low)
                    continue
                previous_close = self.bars[index - 1].close
                true_ranges.append(
                    max(
                        current.high - current.low,
                        abs(current.high - previous_close),
                        abs(current.low - previous_close),
                    )
                )
            self._average_true_range = sum(true_ranges) / period
        else:
            self._average_true_range = (
                self._average_true_range * (period - 1) + true_range
            ) / period
        return self._average_true_range

    def _evaluate_signal(self) -> Signal | None:
        for direction in (Direction.LONG, Direction.SHORT):
            calculation = self.calculate_current_signal(direction)
            if calculation is None or not calculation.matched:
                continue
            return self._make_signal(calculation)
        return None

    def calculate_current_signal(
        self,
        direction: Direction,
    ) -> SignalCalculationResult | None:
        """Calculate one direction on the current bar without filtering results."""
        inputs = self._calculation_inputs(direction)
        if inputs is None:
            return None
        if direction is Direction.LONG:
            return calculate_long_signal(inputs)
        return calculate_short_signal(inputs)

    def _calculation_inputs(
        self,
        direction: Direction,
    ) -> SignalCalculationInput | None:
        current = len(self.bars) - 1
        if current < 1:
            return None
        rsi = self.rsi_values[current]
        atr = self.atr_values[current]
        fast = self.fast_ma_values[current]
        previous_fast = self.fast_ma_values[current - 1]
        if (
            rsi is None
            or atr is None
            or fast is None
            or previous_fast is None
        ):
            return None
        return SignalCalculationInput(
            config=self.config,
            direction=direction,
            bar=self.bars[current],
            rsi=rsi,
            atr=atr,
            fast_ma=fast,
            previous_fast_ma=previous_fast,
            fast_ma_angle=moving_average_angle(
                self.fast_ma_values,
                self.config.ma_fast_angle_bars,
            ),
            rsi_history=tuple(self.rsi_values),
        )

    @staticmethod
    def _make_signal(calculation: SignalCalculationResult) -> Signal:
        inputs = calculation.inputs
        return Signal(
            direction=inputs.direction,
            timestamp=inputs.bar.timestamp,
            close=inputs.bar.close,
            rsi=inputs.rsi,
            atr=inputs.atr,
        )
