"""Unified daily OHLCV bar type and frame helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import pandas as pd


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


def bar_from_row(timestamp: object, row: pd.Series) -> Bar:
    """Build a Bar from one OHLCV DataFrame row."""
    return Bar(
        timestamp=cast(pd.Timestamp, timestamp).to_pydatetime(),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
    )


def iter_bars(frame: pd.DataFrame) -> Iterator[tuple[int, Bar]]:
    """Yield ``(index, Bar)`` pairs from an adjusted OHLCV frame."""
    for index, (timestamp, row) in enumerate(frame.iterrows()):
        yield index, bar_from_row(timestamp, row)
