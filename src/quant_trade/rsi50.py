"""Bar-by-bar signal engine for the daily RSI 50 trend strategy."""

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
    bars: int = 3,
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
class Rsi50Config:
    """Parameters fixed by the daily RSI 50 strategy document."""

    rsi_period: int = 14
    ma_fast: int = 20
    ma_slow: int = 30
    atr_period: int = 14
    rsi_zone_low: float = 45.0
    rsi_zone_high: float = 55.0
    trigger_rsi_low: float = 45.0
    trigger_rsi_high: float = 55.0
    ma_fast_angle_bars: int = 3
    ma_fast_min_angle_degrees: float | None = 40.0
    pivot_left: int = 3
    pivot_right: int = 3
    min_pattern_distance: int = 5
    max_pattern_distance: int = 30
    max_peak_difference_atr: float = 1.0
    min_middle_retracement_atr: float = 1.0
    break_buffer_atr: float = 0.1

    def __post_init__(self) -> None:
        positive_ints = (
            self.rsi_period,
            self.ma_fast,
            self.ma_slow,
            self.atr_period,
            self.pivot_left,
            self.pivot_right,
            self.min_pattern_distance,
            self.max_pattern_distance,
        )
        if any(value <= 0 for value in positive_ints):
            raise ValueError("period and distance parameters must be positive")
        if self.ma_fast >= self.ma_slow:
            raise ValueError("ma_fast must be less than ma_slow")
        if self.rsi_zone_low >= self.rsi_zone_high:
            raise ValueError("rsi_zone_low must be less than rsi_zone_high")
        if self.trigger_rsi_low > self.trigger_rsi_high:
            raise ValueError("trigger_rsi_low must not exceed trigger_rsi_high")
        if self.ma_fast_angle_bars < 2:
            raise ValueError("ma_fast_angle_bars must be at least 2")
        if self.ma_fast_min_angle_degrees is not None and not (
            0.0 < self.ma_fast_min_angle_degrees < 90.0
        ):
            raise ValueError("ma_fast_min_angle_degrees must be in (0, 90)")
        if self.min_pattern_distance > self.max_pattern_distance:
            raise ValueError(
                "min_pattern_distance must not exceed max_pattern_distance"
            )


@dataclass(frozen=True)
class Pivot:
    """A confirmed swing point."""

    index: int
    price: float
    atr: float


@dataclass(frozen=True)
class Pattern:
    """A confirmed W-bottom or M-top candidate."""

    direction: Direction
    first_index: int
    second_index: int
    neckline: float


@dataclass(frozen=True)
class Signal:
    """A trade signal produced on a completed daily bar."""

    direction: Direction
    timestamp: datetime
    close: float
    rsi: float
    neckline: float
    atr: float
    first_pivot_index: int
    second_pivot_index: int


@dataclass(frozen=True)
class SignalCalculationInput:
    """Shared immutable input for one directional signal calculation."""

    config: Rsi50Config
    pattern: Pattern
    bar: Bar
    rsi: float
    atr: float
    fast_ma: float
    previous_fast_ma: float
    slow_ma: float
    previous_slow_ma: float
    fast_ma_angle: float | None
    rsi_history: tuple[float | None, ...]


class SignalFeature(StrEnum):
    """Features evaluated for every directional signal candidate."""

    RSI_ZONE_ENTRY = "rsi_zone_entry"
    FAST_MA_TREND = "fast_ma_trend"
    SLOW_MA_TREND = "slow_ma_trend"
    BREAKOUT = "breakout"
    RSI_TRIGGER = "rsi_trigger"


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
    def breakout_threshold(self) -> float:
        """Return the directional neckline threshold."""
        result = self.feature(SignalFeature.BREAKOUT)
        threshold = (
            result.minimum
            if self.inputs.pattern.direction is Direction.LONG
            else result.maximum
        )
        if threshold is None:
            raise ValueError("breakout feature has no threshold")
        return threshold

    @property
    def fast_trend_pass(self) -> bool:
        """Return the MA20 feature result."""
        return self.feature(SignalFeature.FAST_MA_TREND).passed

    @property
    def slow_trend_pass(self) -> bool:
        """Return the MA30 feature result."""
        return self.feature(SignalFeature.SLOW_MA_TREND).passed

    @property
    def breakout_pass(self) -> bool:
        """Return the neckline breakout feature result."""
        return self.feature(SignalFeature.BREAKOUT).passed

    @property
    def rsi_trigger_pass(self) -> bool:
        """Return the current RSI feature result."""
        return self.feature(SignalFeature.RSI_TRIGGER).passed

    @property
    def matched(self) -> bool:
        """Return whether every directional signal condition passed."""
        return all(result.passed for result in self.features)


def calculate_rsi_zone_feature(
    inputs: SignalCalculationInput,
) -> FeatureCalculationResult:
    """Calculate whether RSI entered the configured zone after the first pivot."""
    matches = [
        (index, value)
        for index, value in enumerate(inputs.rsi_history)
        if index >= inputs.pattern.first_index
        if value is not None
        and inputs.config.rsi_zone_low <= value <= inputs.config.rsi_zone_high
    ]
    latest = matches[-1] if matches else None
    return FeatureCalculationResult(
        feature=SignalFeature.RSI_ZONE_ENTRY,
        passed=bool(matches),
        observed=latest[1] if latest is not None else None,
        observed_index=latest[0] if latest is not None else None,
        minimum=inputs.config.rsi_zone_low,
        maximum=inputs.config.rsi_zone_high,
    )


def calculate_fast_ma_feature(
    inputs: SignalCalculationInput,
) -> FeatureCalculationResult:
    """Calculate the directional MA20 trend feature."""
    direction = inputs.pattern.direction
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


def calculate_slow_ma_feature(
    inputs: SignalCalculationInput,
) -> FeatureCalculationResult:
    """Calculate the directional MA30 trend feature."""
    observed = inputs.slow_ma - inputs.previous_slow_ma
    is_long = inputs.pattern.direction is Direction.LONG
    return FeatureCalculationResult(
        feature=SignalFeature.SLOW_MA_TREND,
        passed=observed > 0.0 if is_long else observed < 0.0,
        observed=observed,
        minimum=0.0 if is_long else None,
        maximum=0.0 if not is_long else None,
    )


def calculate_breakout_feature(
    inputs: SignalCalculationInput,
) -> FeatureCalculationResult:
    """Calculate the directional neckline breakout feature."""
    is_long = inputs.pattern.direction is Direction.LONG
    offset = inputs.config.break_buffer_atr * inputs.atr
    threshold = (
        inputs.pattern.neckline + offset
        if is_long
        else inputs.pattern.neckline - offset
    )
    return FeatureCalculationResult(
        feature=SignalFeature.BREAKOUT,
        passed=(
            inputs.bar.close > threshold if is_long else inputs.bar.close < threshold
        ),
        observed=inputs.bar.close,
        minimum=threshold if is_long else None,
        maximum=threshold if not is_long else None,
    )


def calculate_rsi_trigger_feature(
    inputs: SignalCalculationInput,
) -> FeatureCalculationResult:
    """Calculate the current-bar RSI trigger feature."""
    return FeatureCalculationResult(
        feature=SignalFeature.RSI_TRIGGER,
        passed=(
            inputs.config.trigger_rsi_low
            <= inputs.rsi
            <= inputs.config.trigger_rsi_high
        ),
        observed=inputs.rsi,
        minimum=inputs.config.trigger_rsi_low,
        maximum=inputs.config.trigger_rsi_high,
    )


def _calculate_signal_features(
    inputs: SignalCalculationInput,
) -> tuple[FeatureCalculationResult, ...]:
    return (
        calculate_rsi_zone_feature(inputs),
        calculate_fast_ma_feature(inputs),
        calculate_slow_ma_feature(inputs),
        calculate_breakout_feature(inputs),
        calculate_rsi_trigger_feature(inputs),
    )


def calculate_long_signal(
    inputs: SignalCalculationInput,
) -> SignalCalculationResult:
    """Calculate all long conditions without filtering or mutating engine state."""
    if inputs.pattern.direction is not Direction.LONG:
        raise ValueError("long signal calculation requires a long pattern")
    return SignalCalculationResult(
        inputs=inputs,
        features=_calculate_signal_features(inputs),
    )


def calculate_short_signal(
    inputs: SignalCalculationInput,
) -> SignalCalculationResult:
    """Calculate all short conditions without filtering or mutating engine state."""
    if inputs.pattern.direction is not Direction.SHORT:
        raise ValueError("short signal calculation requires a short pattern")
    return SignalCalculationResult(
        inputs=inputs,
        features=_calculate_signal_features(inputs),
    )


def is_pivot_high(
    highs: list[float], index: int, left: int = 3, right: int = 3
) -> bool:
    """Return whether an index is a confirmed left/right pivot high."""
    if index < left or index + right >= len(highs):
        return False
    price = highs[index]
    return price > max(highs[index - left : index]) and price >= max(
        highs[index + 1 : index + right + 1]
    )


def is_pivot_low(lows: list[float], index: int, left: int = 3, right: int = 3) -> bool:
    """Return whether an index is a confirmed left/right pivot low."""
    if index < left or index + right >= len(lows):
        return False
    price = lows[index]
    return price < min(lows[index - left : index]) and price <= min(
        lows[index + 1 : index + right + 1]
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
        self.pivot_highs: list[Pivot] = []
        self.pivot_lows: list[Pivot] = []
        self._average_gain: float | None = None
        self._average_loss: float | None = None
        self._average_true_range: float | None = None
        self._emitted_patterns: set[tuple[Direction, int, int]] = set()

    def on_bar(self, bar: Bar) -> Signal | None:
        """Process one completed daily bar and return at most one signal."""
        if self.bars and bar.timestamp <= self.bars[-1].timestamp:
            raise ValueError("bars must be supplied in strictly increasing order")

        self.bars.append(bar)
        self._update_indicators()
        self._confirm_pivot()
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

    def _confirm_pivot(self) -> None:
        config = self.config
        candidate = len(self.bars) - 1 - config.pivot_right
        if candidate < config.pivot_left:
            return
        atr = self.atr_values[candidate]
        if atr is None:
            return

        highs = [bar.high for bar in self.bars]
        lows = [bar.low for bar in self.bars]
        if is_pivot_high(highs, candidate, config.pivot_left, config.pivot_right):
            self.pivot_highs.append(Pivot(candidate, highs[candidate], atr))
        if is_pivot_low(lows, candidate, config.pivot_left, config.pivot_right):
            self.pivot_lows.append(Pivot(candidate, lows[candidate], atr))

    def _evaluate_signal(self) -> Signal | None:
        for direction in (Direction.LONG, Direction.SHORT):
            calculation = self.calculate_current_signal(direction)
            if calculation is None or not calculation.matched:
                continue
            pattern = calculation.inputs.pattern
            key = (pattern.direction, pattern.first_index, pattern.second_index)
            if key in self._emitted_patterns:
                continue
            self._emitted_patterns.add(key)
            return self._make_signal(calculation)
        return None

    def calculate_current_signal(
        self,
        direction: Direction,
    ) -> SignalCalculationResult | None:
        """Calculate one direction on the current bar without filtering results."""
        pattern = self._latest_pattern(direction)
        if pattern is None:
            return None
        inputs = self._calculation_inputs(pattern)
        if inputs is None:
            return None
        if direction is Direction.LONG:
            return calculate_long_signal(inputs)
        return calculate_short_signal(inputs)

    def _calculation_inputs(
        self,
        pattern: Pattern,
    ) -> SignalCalculationInput | None:
        current = len(self.bars) - 1
        if current < 1:
            return None
        rsi = self.rsi_values[current]
        atr = self.atr_values[current]
        fast = self.fast_ma_values[current]
        previous_fast = self.fast_ma_values[current - 1]
        slow = self.slow_ma_values[current]
        previous_slow = self.slow_ma_values[current - 1]
        if (
            rsi is None
            or atr is None
            or fast is None
            or previous_fast is None
            or slow is None
            or previous_slow is None
        ):
            return None
        return SignalCalculationInput(
            config=self.config,
            pattern=pattern,
            bar=self.bars[current],
            rsi=rsi,
            atr=atr,
            fast_ma=fast,
            previous_fast_ma=previous_fast,
            slow_ma=slow,
            previous_slow_ma=previous_slow,
            fast_ma_angle=moving_average_angle(
                self.fast_ma_values,
                self.config.ma_fast_angle_bars,
            ),
            rsi_history=tuple(self.rsi_values),
        )

    def _latest_pattern(self, direction: Direction) -> Pattern | None:
        pivots = self.pivot_lows if direction is Direction.LONG else self.pivot_highs
        if len(pivots) < 2:
            return None
        first, second = pivots[-2:]
        distance = second.index - first.index
        config = self.config
        if not config.min_pattern_distance <= distance <= config.max_pattern_distance:
            return None
        if abs(second.price - first.price) > (
            config.max_peak_difference_atr * second.atr
        ):
            return None

        if direction is Direction.LONG:
            neckline = max(
                bar.high for bar in self.bars[first.index : second.index + 1]
            )
            retracement = neckline - max(first.price, second.price)
        else:
            neckline = min(bar.low for bar in self.bars[first.index : second.index + 1])
            retracement = min(first.price, second.price) - neckline
        if retracement < config.min_middle_retracement_atr * second.atr:
            return None
        return Pattern(direction, first.index, second.index, neckline)

    @staticmethod
    def _make_signal(calculation: SignalCalculationResult) -> Signal:
        inputs = calculation.inputs
        pattern = inputs.pattern
        return Signal(
            direction=pattern.direction,
            timestamp=inputs.bar.timestamp,
            close=inputs.bar.close,
            rsi=inputs.rsi,
            neckline=pattern.neckline,
            atr=inputs.atr,
            first_pivot_index=pattern.first_index,
            second_pivot_index=pattern.second_index,
        )
