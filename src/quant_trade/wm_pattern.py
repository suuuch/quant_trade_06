"""Independent W-bottom and M-top neckline entry signals."""

from dataclasses import dataclass
from datetime import datetime

from quant_trade.rsi50 import Bar, Direction


@dataclass(frozen=True)
class WmPatternConfig:
    """Parameters for confirmed daily W/M patterns and entries."""

    atr_period: int = 14
    pivot_left: int = 3
    pivot_right: int = 3
    min_pattern_distance: int = 5
    max_pattern_distance: int = 30
    max_peak_difference_atr: float = 1.0
    min_middle_retracement_atr: float = 1.0
    break_buffer_atr: float = 0.1

    def __post_init__(self) -> None:
        positive_ints = (
            self.atr_period,
            self.pivot_left,
            self.pivot_right,
            self.min_pattern_distance,
            self.max_pattern_distance,
        )
        if any(value <= 0 for value in positive_ints):
            raise ValueError("period and distance parameters must be positive")
        if self.min_pattern_distance > self.max_pattern_distance:
            raise ValueError(
                "min_pattern_distance must not exceed max_pattern_distance"
            )
        if self.max_peak_difference_atr < 0:
            raise ValueError("max_peak_difference_atr must not be negative")
        if self.min_middle_retracement_atr < 0:
            raise ValueError("min_middle_retracement_atr must not be negative")
        if self.break_buffer_atr < 0:
            raise ValueError("break_buffer_atr must not be negative")


@dataclass(frozen=True)
class Pivot:
    """One confirmed swing point."""

    index: int
    price: float
    atr: float


@dataclass(frozen=True)
class WmPattern:
    """One confirmed W-bottom or M-top candidate."""

    direction: Direction
    first_index: int
    second_index: int
    neckline: float


@dataclass(frozen=True)
class WmSignal:
    """A neckline entry signal emitted on a completed daily bar."""

    direction: Direction
    timestamp: datetime
    close: float
    atr: float
    neckline: float
    first_pivot_index: int
    second_pivot_index: int


def is_pivot_high(
    highs: list[float], index: int, left: int = 3, right: int = 3
) -> bool:
    """Return whether an index is a confirmed pivot high."""
    if index < left or index + right >= len(highs):
        return False
    price = highs[index]
    return price > max(highs[index - left : index]) and price >= max(
        highs[index + 1 : index + right + 1]
    )


def is_pivot_low(lows: list[float], index: int, left: int = 3, right: int = 3) -> bool:
    """Return whether an index is a confirmed pivot low."""
    if index < left or index + right >= len(lows):
        return False
    price = lows[index]
    return price < min(lows[index - left : index]) and price <= min(
        lows[index + 1 : index + right + 1]
    )


class WmPatternEngine:
    """Detect confirmed W/M patterns and their neckline entry signals."""

    def __init__(self, config: WmPatternConfig | None = None) -> None:
        self.config = config or WmPatternConfig()
        self.bars: list[Bar] = []
        self.atr_values: list[float | None] = []
        self.pivot_highs: list[Pivot] = []
        self.pivot_lows: list[Pivot] = []
        self._average_true_range: float | None = None
        self._emitted_patterns: set[tuple[Direction, int, int]] = set()

    def on_bar(self, bar: Bar) -> tuple[WmSignal, ...]:
        """Process one completed bar and return new W/M entry signals."""
        if self.bars and bar.timestamp <= self.bars[-1].timestamp:
            raise ValueError("bars must be supplied in strictly increasing order")
        self.bars.append(bar)
        self.atr_values.append(self._next_atr())
        self._confirm_pivot()

        signals: list[WmSignal] = []
        for direction in (Direction.LONG, Direction.SHORT):
            pattern = self.latest_pattern(direction)
            if pattern is None or not self._entry_passes(pattern):
                continue
            key = (direction, pattern.first_index, pattern.second_index)
            if key in self._emitted_patterns:
                continue
            self._emitted_patterns.add(key)
            atr = self.atr_values[-1]
            if atr is None:
                continue
            signals.append(
                WmSignal(
                    direction=direction,
                    timestamp=bar.timestamp,
                    close=bar.close,
                    atr=atr,
                    neckline=pattern.neckline,
                    first_pivot_index=pattern.first_index,
                    second_pivot_index=pattern.second_index,
                )
            )
        return tuple(signals)

    def latest_pattern(self, direction: Direction) -> WmPattern | None:
        """Return the latest valid pattern for one direction."""
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
        return WmPattern(direction, first.index, second.index, neckline)

    def _entry_passes(self, pattern: WmPattern) -> bool:
        atr = self.atr_values[-1]
        if atr is None:
            return False
        offset = self.config.break_buffer_atr * atr
        close = self.bars[-1].close
        if pattern.direction is Direction.LONG:
            return close > pattern.neckline + offset
        return close < pattern.neckline - offset

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
            true_ranges = [
                self._true_range(index) for index in range(self.config.atr_period)
            ]
            self._average_true_range = sum(true_ranges) / period
        else:
            self._average_true_range = (
                self._average_true_range * (period - 1) + true_range
            ) / period
        return self._average_true_range

    def _true_range(self, index: int) -> float:
        bar = self.bars[index]
        if index == 0:
            return bar.high - bar.low
        previous_close = self.bars[index - 1].close
        return max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
