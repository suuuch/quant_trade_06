"""Integration test for M-top pattern detection on a synthetic series."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_trade.config import PivotParams, StrategyParams
from quant_trade.engine import run_engine
from quant_trade.indicators import add_indicators
from quant_trade.patterns.m_top import detect_m_top


def _make_m_top_series() -> pd.DataFrame:
    """A clean M-top with two pivots and a clean break.

    H1 ~ 106.5 around bar 35, pullback to ~92, H2 ~ 106.2 around bar 55,
    then break below 92 around bar 65. H1/H2 are within 1 ATR so the spec's
    max_top_difference filter passes; RSI tops at ~80 to satisfy [75, 85].
    """
    n = 100
    rng = np.random.default_rng(42)
    close = np.full(n, 100.0)
    # Pre-pivot noise
    close[0:25] = np.linspace(100, 100, 25) + rng.normal(0, 0.3, 25)
    # H1 around bar 35: rise 100 -> 106.5 over 15 days
    close[25:40] = np.linspace(100, 106.5, 15) + rng.normal(0, 0.3, 15)
    # pullback to 92 (so middle_pullback >= 1 ATR)
    close[40:50] = np.linspace(106.5, 92, 10) + rng.normal(0, 0.3, 10)
    # H2 around bar 55: rise 92 -> 106.2 over 10 days
    close[50:65] = np.linspace(92, 106.2, 15) + rng.normal(0, 0.3, 15)
    # break down to 80
    close[65:80] = np.linspace(106.2, 80, 15) + rng.normal(0, 0.3, 15)
    # recovery
    close[80:100] = np.linspace(80, 90, 20) + rng.normal(0, 0.3, 20)

    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.1, n),
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(800_000, 1_500_000, n).astype(float),
        },
        index=pd.bdate_range("2024-01-01", periods=n),
    )


def test_m_top_detected_with_break() -> None:
    df = _make_m_top_series()
    enriched = add_indicators(df, StrategyParams().indicators)
    sigs = detect_m_top(enriched, PivotParams(left=3, right=3), StrategyParams().pattern)
    assert len(sigs) >= 1
    s = sigs[0]
    assert s.pattern == "M_top"
    assert s.direction == "short"
    assert s.trigger_price < 92  # entry below neckline (91.52)
    assert s.stop_loss > 105    # stop = H2 (106.94) + 0.5 ATR (~0.77)
    assert s.invalidation_price > 106  # inval = max(H1,H2) + 0.3 ATR


def test_engine_runs_clean() -> None:
    df = _make_m_top_series()
    sigs = run_engine(df, StrategyParams(), symbol="SYN")
    m_top_sigs = [s for s in sigs if s.pattern == "M_top"]
    assert len(m_top_sigs) >= 1
