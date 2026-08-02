"""Integration test for W-bottom (S2) pattern detection on a synthetic series."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_trade.config import PivotParams, StrategyParams
from quant_trade.indicators import add_indicators
from quant_trade.patterns.w_bottom import detect_w_bottom


def _make_w_bottom_series() -> pd.DataFrame:
    """A clean W-bottom: prelude, downtrend to L1, bounce, higher low L2, breakout.

    The 35-bar prelude keeps L1 far enough from the start that both pivots
    survive the engine's warmup dropna (rsi/atr/ma need 20-30 bars).
    """
    rng = np.random.default_rng(1)
    pre, d1, b1, d2, b2 = 35, 15, 8, 12, 20
    n = pre + d1 + b1 + d2 + b2
    close = np.concatenate(
        [
            np.full(pre, 100.0),
            np.linspace(100, 90, d1),  # downtrend to L1 (~90, RSI into [15,25])
            np.linspace(90, 98, b1),  # bounce to the neckline (~98)
            np.linspace(98, 90, d2),  # pullback to L2 (higher low)
            np.linspace(90, 108, b2),  # breakout above the neckline
        ]
    ) + rng.normal(0, 0.3, n)
    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.linspace(800_000, 1_500_000, n).astype(float),
        },
        index=pd.bdate_range("2024-01-01", periods=n),
    )


def test_w_bottom_detected_with_break() -> None:
    df = _make_w_bottom_series()
    enriched = add_indicators(df, StrategyParams().indicators)
    sigs = detect_w_bottom(
        enriched, PivotParams(left=3, right=3), StrategyParams().pattern, symbol="SYN"
    )
    assert len(sigs) == 1
    s = sigs[0]
    assert s.pattern == "W_bottom"
    assert s.direction == "long"
    assert s.trigger_price > 98  # entry above the neckline (~98)
    assert s.stop_loss < s.trigger_price
    assert s.invalidation_price < s.trigger_price


def test_w_bottom_engine_runs_clean() -> None:
    df = _make_w_bottom_series()
    from quant_trade.engine import run_engine

    sigs = run_engine(df, StrategyParams(), symbol="SYN")
    w = [s for s in sigs if s.pattern == "W_bottom"]
    assert len(w) == 1
    assert w[0].direction == "long"
