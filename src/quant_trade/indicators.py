"""Shared technical-indicator helpers."""

from __future__ import annotations

from collections.abc import Sequence

from quant_trade.bars import Bar


def simple_moving_average(
    values: Sequence[float],
    period: int,
) -> float | None:
    """Return the simple moving average of the latest ``period`` values."""
    if period < 1:
        raise ValueError("period must be at least 1")
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def true_range(bar: Bar, previous_close: float | None) -> float:
    """Return the true range for one bar."""
    if previous_close is None:
        return bar.high - bar.low
    return max(
        bar.high - bar.low,
        abs(bar.high - previous_close),
        abs(bar.low - previous_close),
    )


class WilderAtrState:
    """Incremental Wilder ATR calculator shared by strategy engines."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("period must be at least 1")
        self.period = period
        self._average: float | None = None

    @property
    def average(self) -> float | None:
        """Return the latest ATR once the seed window is complete."""
        return self._average

    def update(self, bars: Sequence[Bar]) -> float | None:
        """Update ATR from the full bar history ending at the latest bar."""
        period = self.period
        if len(bars) < period:
            return None
        current = bars[-1]
        previous_close = None if len(bars) == 1 else bars[-2].close
        current_tr = true_range(current, previous_close)
        if self._average is None:
            seed = [
                true_range(
                    bars[index],
                    None if index == 0 else bars[index - 1].close,
                )
                for index in range(period)
            ]
            self._average = sum(seed) / period
        else:
            self._average = (self._average * (period - 1) + current_tr) / period
        return self._average
