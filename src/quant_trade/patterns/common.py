"""Shared types and helpers for pattern detectors."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class Signal:
    symbol: str
    frequency: str
    direction: str  # "long" | "short"
    pattern: str    # "M_top" | "W_bottom" | "RSI50_trend"
    triggered_at: pd.Timestamp
    trigger_price: float
    rsi_at_trigger: float
    invalidation_price: float
    stop_loss: float
    daily_context: str | None = None
    hourly_shape: str | None = None
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["triggered_at"] = self.triggered_at.strftime("%Y-%m-%d")
        return d


def make_signal(
    df: pd.DataFrame,
    pattern: str,
    direction: str,
    entry_idx: int,
    entry_price: float,
    stop_loss: float,
    invalidation: float,
    rsi_at_trigger: float,
    notes: str = "",
    symbol: str = "",
    frequency: str = "daily",
) -> Signal:
    return Signal(
        symbol=symbol,
        frequency=frequency,
        direction=direction,
        pattern=pattern,
        triggered_at=df.index[entry_idx],
        trigger_price=entry_price,
        rsi_at_trigger=rsi_at_trigger,
        invalidation_price=invalidation,
        stop_loss=stop_loss,
        notes=notes,
    )
