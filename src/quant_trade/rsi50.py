"""Bar-by-bar signal engine for the daily RSI 50 trend strategy."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Direction(StrEnum):
    """Supported trade directions."""

    LONG = "long"
    SHORT = "short"


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
        current = len(self.bars) - 1
        if current < 1:
            return None
        values = (
            self.rsi_values[current],
            self.atr_values[current],
            self.fast_ma_values[current],
            self.fast_ma_values[current - 1],
            self.slow_ma_values[current],
            self.slow_ma_values[current - 1],
        )
        if any(value is None for value in values):
            return None

        long_pattern = self._latest_pattern(Direction.LONG)
        if long_pattern is not None and self._long_triggered(long_pattern):
            return self._make_signal(long_pattern)

        short_pattern = self._latest_pattern(Direction.SHORT)
        if short_pattern is not None and self._short_triggered(short_pattern):
            return self._make_signal(short_pattern)
        return None

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

    def _long_triggered(self, pattern: Pattern) -> bool:
        key = (pattern.direction, pattern.first_index, pattern.second_index)
        if key in self._emitted_patterns:
            return False
        current = len(self.bars) - 1
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
            return False
        entered_zone = self._rsi_entered_zone(pattern.first_index, current)
        return bool(
            fast > previous_fast
            and slow > previous_slow
            and entered_zone
            and self.bars[current].close
            > pattern.neckline + self.config.break_buffer_atr * atr
            and rsi > self.config.rsi_zone_high
        )

    def _short_triggered(self, pattern: Pattern) -> bool:
        key = (pattern.direction, pattern.first_index, pattern.second_index)
        if key in self._emitted_patterns:
            return False
        current = len(self.bars) - 1
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
            return False
        entered_zone = self._rsi_entered_zone(pattern.first_index, current)
        return bool(
            fast < previous_fast
            and slow < previous_slow
            and entered_zone
            and self.bars[current].close
            < pattern.neckline - self.config.break_buffer_atr * atr
            and rsi < self.config.rsi_zone_low
        )

    def _rsi_entered_zone(self, start: int, end: int) -> bool:
        return any(
            value is not None
            and self.config.rsi_zone_low <= value <= self.config.rsi_zone_high
            for value in self.rsi_values[start : end + 1]
        )

    def _make_signal(self, pattern: Pattern) -> Signal:
        current = len(self.bars) - 1
        rsi = self.rsi_values[current]
        atr = self.atr_values[current]
        assert rsi is not None and atr is not None
        key = (pattern.direction, pattern.first_index, pattern.second_index)
        self._emitted_patterns.add(key)
        return Signal(
            direction=pattern.direction,
            timestamp=self.bars[current].timestamp,
            close=self.bars[current].close,
            rsi=rsi,
            neckline=pattern.neckline,
            atr=atr,
            first_pivot_index=pattern.first_index,
            second_pivot_index=pattern.second_index,
        )
