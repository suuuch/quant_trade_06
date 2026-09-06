"""Shared direction, pattern-signal, and scan-match models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Generic, Literal, TypeVar

import pandas as pd

Market = Literal["a", "us"]

TSignal = TypeVar("TSignal", bound="PatternSignal")
TMatch = TypeVar("TMatch")


class Direction(StrEnum):
    """Supported trade directions."""

    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class PatternSignal:
    """Shared fields for every strategy signal emitted on a completed bar."""

    direction: Direction
    timestamp: datetime
    close: float
    atr: float


@dataclass(frozen=True)
class RsiTrendSignal(PatternSignal):
    """RSI trend-following entry signal."""

    rsi: float


@dataclass(frozen=True)
class WmNecklineSignal(PatternSignal):
    """W-bottom / M-top neckline entry signal."""

    neckline: float
    first_pivot_index: int
    second_pivot_index: int


@dataclass
class SymbolMatch(Generic[TSignal]):
    """One symbol hit plus the frame/engine needed to review it."""

    symbol: str
    name: str
    industry: str
    frame: pd.DataFrame
    signal: TSignal
    engine: object | None = None
    market_cap_cny: float | None = None
    circulating_market_cap_cny: float | None = None
    market: Market = "a"


@dataclass(frozen=True)
class ScanBatch(Generic[TMatch]):
    """Summary of one database-wide scan."""

    scan_date: date
    scanned_symbols: int
    stale_symbols: int
    matches: list[TMatch]
    ma_min_daily_return: float | None = None


# Compatibility aliases used by existing scanners and tests.
Signal = RsiTrendSignal
WmSignal = WmNecklineSignal
SignalMatch = SymbolMatch[RsiTrendSignal]
WmSignalMatch = SymbolMatch[WmNecklineSignal]
WmScanBatch = ScanBatch[WmSignalMatch]
