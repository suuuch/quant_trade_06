"""Signal engine: pre-compute signals for one symbol over a date range.

Returns a list of Signal objects sorted by triggered_at.
"""

from __future__ import annotations

import pandas as pd

from .config import StrategyParams
from .indicators import add_indicators
from .patterns.common import Signal
from .patterns.m_top import detect_m_top
from .patterns.rsi50_trend import detect_rsi50
from .patterns.w_bottom import detect_w_bottom


def run_engine(
    df: pd.DataFrame,
    params: StrategyParams,
    symbol: str = "",
) -> list[Signal]:
    """Compute indicators on df then run all enabled detectors."""
    if df.empty:
        return []
    enriched = add_indicators(df, params.indicators)
    enriched = enriched.dropna(subset=["rsi", "atr", "ma_fast", "ma_slow"])

    out: list[Signal] = []
    out.extend(s for s in detect_m_top(enriched, params.pivot, params.pattern))
    out.extend(s for s in detect_w_bottom(enriched, params.pivot, params.pattern, symbol=symbol))
    out.extend(s for s in detect_rsi50(enriched, params.rsi50, params.pattern, symbol=symbol))

    # Fill in symbol/frequency on M-top signals (they were empty in detector)
    for s in out:
        if not s.symbol:
            s.symbol = symbol
        s.frequency = params.frequency

    out.sort(key=lambda s: s.triggered_at)
    return out
