"""Tests for pivot detection with prominence."""

from __future__ import annotations

import pandas as pd

from quant_trade.config import PivotParams
from quant_trade.pivots import find_pivot_highs, find_pivot_lows


def _series(values: list[float]) -> pd.DataFrame:
    n = len(values)
    return pd.DataFrame(
        {
            "open": values,
            "high": [v + 0.5 for v in values],
            "low": [v - 0.5 for v in values],
            "close": values,
            "volume": [1000.0] * n,
            "atr": [1.0] * n,
        }
    )


def _flat_with_bump(
    n: int, base: float, bump_at: int, bump_height: float
) -> pd.DataFrame:
    """Build a near-flat series with a single small bump at bump_at.

    high/low are kept exactly at close to make prominence directly equal to
    the bump height above the local floor.
    """
    closes = [base] * n
    closes[bump_at] = base + bump_height
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000.0] * n,
            "atr": [1.0] * n,
        }
    )


def test_pivot_high_simple() -> None:
    """A bar with a clear peak should be a pivot with L=3,R=3."""
    vals = [10, 11, 12, 15, 13, 12, 11, 10]
    df = _series(vals)
    mask = find_pivot_highs(df, PivotParams(left=3, right=3))
    assert mask.iloc[3]  # the 15 bar
    assert mask.sum() == 1


def test_pivot_low_simple() -> None:
    vals = [10, 9, 8, 5, 7, 9, 10, 11]
    df = _series(vals)
    mask = find_pivot_lows(df, PivotParams(left=3, right=3))
    assert mask.iloc[3]  # the 5 bar
    assert mask.sum() == 1


def test_pivot_high_prominence_rejects_small() -> None:
    """A small bump (prominence < 0.8 ATR) should be filtered out."""
    vals = [10, 11, 12, 12.5, 12, 11, 10, 9]
    df = _series(vals)
    # without prominence: 12.5 is a pivot
    mask_no = find_pivot_highs(df, PivotParams(left=3, right=3, use_prominence=False))
    assert mask_no.iloc[3]
    # with prominence 0.8 ATR: 12.5 - max(left_low=9, right_low=8.5) = 12.5 - 9 = 3.5 >= 0.8 ✓
    # The bump in the prior 3 bars: left low is at index 0..3 lows = [9.5, 10.5, 11.5, 12], min=9.5
    # right low is at index 3..6 lows = [12, 11.5, 11, 10.5], min=10.5
    # prominence = 12.5 - max(9.5, 10.5) = 12.5 - 10.5 = 2.0  → > 0.8, still passes
    mask_yes = find_pivot_highs(df, PivotParams(left=3, right=3, use_prominence=True, prominence_atr=0.8))
    assert mask_yes.iloc[3]


def test_pivot_high_prominence_tiny_bump_filtered() -> None:
    """A bump with prominence < 0.8 ATR is filtered out."""
    df = _flat_with_bump(n=8, base=10.0, bump_at=3, bump_height=0.5)
    mask = find_pivot_highs(df, PivotParams(left=3, right=3, use_prominence=True, prominence_atr=0.8))
    # prominence = 0.5 < 0.8 → filtered
    assert not mask.any()


def test_pivot_high_prominence_big_bump_passes() -> None:
    """A bump with prominence >= 0.8 ATR passes the filter."""
    df = _flat_with_bump(n=8, base=10.0, bump_at=3, bump_height=1.5)
    mask = find_pivot_highs(df, PivotParams(left=3, right=3, use_prominence=True, prominence_atr=0.8))
    assert mask.iloc[3]
