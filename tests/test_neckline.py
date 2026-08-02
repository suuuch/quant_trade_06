"""Tests for neckline and break/invalidation helpers."""

from __future__ import annotations

import pandas as pd

from quant_trade.neckline import (
    break_above_at,
    break_below_at,
    invalid_long,
    invalid_short,
    neckline_long,
    neckline_short,
)


def _ohlc(closes, highs=None, lows=None) -> pd.DataFrame:
    n = len(closes)
    if highs is None:
        highs = [c + 1 for c in closes]
    if lows is None:
        lows = [c - 1 for c in closes]
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


def test_neckline_short_min_between_tops() -> None:
    closes = [100, 102, 105, 95, 92, 99, 101, 90]
    df = _ohlc(closes, highs=closes, lows=[c - 1 for c in closes])
    # lows in [2:6] = [104, 94, 91, 98]; min is 91
    assert neckline_short(df, 2, 5) == 91.0


def test_neckline_long_max_between_bottoms() -> None:
    closes = [100, 95, 90, 95, 99, 92, 90, 95]
    df = _ohlc(closes, highs=closes, lows=[c - 1 for c in closes])
    assert neckline_long(df, 2, 6) == 99.0  # max of highs in [2:7]


def test_break_below_at_loose() -> None:
    close = pd.Series([100, 99, 95, 90, 88])
    a = pd.Series([2.0] * 5)
    neckline = 96
    idx = break_below_at(close, a, neckline, buffer_atr=0.1, mode="loose")
    # close < 96 - 0.2 = 95.8 → bar 2 (close=95)
    assert idx == 2


def test_break_below_at_standard() -> None:
    close = pd.Series([100, 99, 95.5, 90, 88])  # bar 2 close=95.5 < 95.8
    a = pd.Series([2.0] * 5)
    neckline = 96
    idx = break_below_at(close, a, neckline, buffer_atr=0.1, mode="standard")
    # bar 2 close=95.5 < 95.8 and bar 3 close=90 < 95.8 → two consecutive;
    # our impl returns the FIRST of the pair (bar 2).
    assert idx == 2


def test_break_below_at_standard_requires_two_consecutive() -> None:
    """A single bar below threshold should NOT trigger standard mode."""
    close = pd.Series([100, 99, 95.5, 96, 88])
    a = pd.Series([2.0] * 5)
    neckline = 96
    idx = break_below_at(close, a, neckline, buffer_atr=0.1, mode="standard")
    # bar 2 close=95.5 < 95.8 but bar 3 close=96 > 95.8 → no consecutive pair
    # bar 4 close=88 < 95.8 but only one bar after it, no pair
    assert idx == -1


def test_break_above_at_strict() -> None:
    close = pd.Series([100, 99, 102, 105, 108])
    a = pd.Series([2.0] * 5)
    vol = pd.Series([1000.0, 1000.0, 5000.0, 6000.0, 6000.0])  # spike at bar 2
    vol_ma = pd.Series([2000.0] * 5)
    neckline = 100
    # close > 100 + 0.2 = 100.2 → bar 2 (102)
    # bar 2 vol 5000 > 2000 → strict OK
    idx = break_above_at(close, a, neckline, buffer_atr=0.1, mode="strict", volume=vol, vol_ma=vol_ma)
    assert idx == 2


def test_break_above_at_strict_rejects_low_volume() -> None:
    close = pd.Series([100, 99, 102, 105, 108])
    a = pd.Series([2.0] * 5)
    vol = pd.Series([1000.0] * 5)  # never > 2000
    vol_ma = pd.Series([2000.0] * 5)
    neckline = 100
    idx = break_above_at(close, a, neckline, buffer_atr=0.1, mode="strict", volume=vol, vol_ma=vol_ma)
    assert idx == -1


def test_invalid_short() -> None:
    assert invalid_short(close=120, h1=110, h2=108, atr_val=10, buffer_atr=0.3)
    # 120 > max(110,108) + 3 = 113
    assert not invalid_short(close=112, h1=110, h2=108, atr_val=10, buffer_atr=0.3)


def test_invalid_long() -> None:
    assert invalid_long(close=80, l1=90, l2=92, atr_val=10, buffer_atr=0.3)
    # 80 < min(90,92) - 3 = 87
    assert not invalid_long(close=88, l1=90, l2=92, atr_val=10, buffer_atr=0.3)
