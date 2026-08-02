"""Integration tests for the RSI50 trend-following detector (S3, spec §6)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_trade.config import StrategyParams
from quant_trade.indicators import add_indicators
from quant_trade.patterns.rsi50_trend import _higher_low, _lower_high, detect_rsi50


def _frame(close: np.ndarray) -> pd.DataFrame:
    n = len(close)
    high = close + 0.5
    low = close - 0.5
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.linspace(8e5, 1.6e6, n),
        },
        index=pd.bdate_range("2024-01-01", periods=n),
    )


def _make_w_bottom_series() -> pd.DataFrame:
    """Deterministic W: uptrend, L1, rebound to the neckline, higher low L2, resumption."""
    close = np.concatenate(
        [
            np.linspace(100, 122.5, 45),
            np.linspace(122.5, 105, 12),
            np.linspace(105, 118, 14),
            np.linspace(118, 111, 6),
            np.linspace(111, 130, 25),
        ]
    )
    return _frame(close)


def _make_m_top_series() -> pd.DataFrame:
    """Deterministic inverted W (M-top) for the short side."""
    close = np.concatenate(
        [
            np.linspace(130, 107.5, 45),
            np.linspace(107.5, 125, 12),
            np.linspace(125, 112, 14),
            np.linspace(112, 119, 6),
            np.linspace(119, 100, 25),
        ]
    )
    return _frame(close)


def test_rsi50_long_on_w_bottom_resumption() -> None:
    params = StrategyParams()
    enriched = add_indicators(_make_w_bottom_series(), params.indicators)
    sigs = [
        s
        for s in detect_rsi50(enriched, params.rsi50, params.pattern, symbol="SYN")
        if s.direction == "long"
    ]
    assert len(sigs) == 1
    s = sigs[0]
    assert s.pattern == "RSI50_trend"
    assert str(s.triggered_at.date()) == "2024-05-01"  # the resumption breakout bar
    assert s.trigger_price > 118  # above the W neckline (~118)
    assert s.stop_loss < s.invalidation_price < s.trigger_price


def test_rsi50_short_on_m_top_breakdown() -> None:
    params = StrategyParams()
    enriched = add_indicators(_make_m_top_series(), params.indicators)
    sigs = [
        s
        for s in detect_rsi50(enriched, params.rsi50, params.pattern, symbol="SYN")
        if s.direction == "short"
    ]
    assert len(sigs) == 1
    s = sigs[0]
    assert str(s.triggered_at.date()) == "2024-05-01"
    assert s.trigger_price < 112  # below the M neckline (~112)
    assert s.trigger_price < s.invalidation_price < s.stop_loss


def test_rsi50_no_signal_without_pullback() -> None:
    params = StrategyParams()
    uptrend = _frame(np.linspace(100, 150, 90))
    assert (
        detect_rsi50(add_indicators(uptrend, params.indicators), params.rsi50, params.pattern)
        == []
    )
    flat = _frame(np.full(90, 100.0))
    assert detect_rsi50(add_indicators(flat, params.indicators), params.rsi50, params.pattern) == []


def test_higher_low_lower_high_semantics() -> None:
    lows = pd.Series([10, 9, 11, 12, 13, 14, 8, 9, 10, 11])
    highs = pd.Series([20, 19, 21, 22, 23, 24, 30, 29, 28, 27])
    # low[i] > min(low[i-3:i]); the new low at index 6 breaks the sequence.
    assert _higher_low(lows, lows, 3).astype(int).tolist() == [0, 0, 0, 1, 1, 1, 0, 1, 1, 1]
    # high[i] < max(high[i-3:i]); index 6 is a new high, later bars are lower highs.
    assert _lower_high(highs, highs, 3).astype(int).tolist() == [0, 0, 0, 0, 0, 0, 0, 1, 1, 1]
