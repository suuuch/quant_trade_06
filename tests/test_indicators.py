"""Tests for indicators (sma, rsi, atr, slope)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_trade.indicators import atr, rsi, slope, sma, true_range


def test_sma_basic() -> None:
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = sma(s, 3)
    assert out.iloc[1] != out.iloc[1]  # NaN until min_periods
    assert out.iloc[2] == 2.0
    assert out.iloc[4] == 4.0


def test_sma_constant_series() -> None:
    s = pd.Series([10.0] * 20)
    out = sma(s, 5)
    assert (out.dropna() == 10.0).all()


def test_rsi_all_up() -> None:
    """RSI should be 100 when price only goes up."""
    s = pd.Series(np.arange(1, 30, dtype=float))
    out = rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_down() -> None:
    """RSI should be 0 when price only goes down."""
    s = pd.Series(np.arange(30, 0, -1, dtype=float))
    out = rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(0.0, abs=1e-3)


def test_rsi_flat() -> None:
    s = pd.Series([100.0] * 30)
    out = rsi(s, 14)
    # Wilder seed: avg_gain=0 → division by zero, we map to 100
    assert out.iloc[-1] == pytest.approx(100.0)


def test_atr_matches_simple_range() -> None:
    """ATR of a constant bar = high-low."""
    n = 30
    high = pd.Series([101.0] * n)
    low = pd.Series([100.0] * n)
    close = pd.Series([100.5] * n)
    out = atr(high, low, close, 14)
    assert out.iloc[-1] == pytest.approx(1.0)


def test_atr_gap() -> None:
    """TR includes gap: max(H-L, |H-prev_C|, |L-prev_C|)."""
    high = pd.Series([101.0, 110.0])
    low = pd.Series([100.0, 100.0])
    close = pd.Series([100.5, 100.0])
    tr = true_range(high, low, close)
    # bar 0: 1.0, bar 1: max(10, 9.5, 0.5) = 10
    assert tr.iloc[0] == pytest.approx(1.0)
    assert tr.iloc[1] == pytest.approx(10.0)


def test_slope() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 5.0, 8.0])
    out = slope(s, 2)
    assert out.iloc[1] != out.iloc[1]  # NaN at start
    assert out.iloc[2] == pytest.approx(2.0)  # 3 - 1
    assert out.iloc[4] == pytest.approx(5.0)  # 8 - 3
