"""Pivot high/low detection with optional prominence filter.

See docs/strategy_spec.md §3.1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PivotParams


def find_pivot_highs(
    df: pd.DataFrame, params: PivotParams, atr_col: str = "atr"
) -> pd.Series:
    """Return a boolean Series. True at index i if i is a confirmed pivot high.

    Definition (spec §3.1):
        high[i] > max(high[i-L:i]) and high[i] >= max(high[i+1:i+R+1])
        prominence = high[i] - max(left_low, right_low) >= 0.8 * atr[i] (optional)
    """
    L, R = params.left, params.right
    high = df["high"].to_numpy()
    n = len(df)
    out = np.zeros(n, dtype=bool)

    for i in range(L, n - R):
        left = high[i - L : i]
        right = high[i + 1 : i + R + 1]
        if not (high[i] > left.max() and high[i] >= right.max()):
            continue
        if params.use_prominence:
            atr_i = df[atr_col].iloc[i]
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            low = df["low"].to_numpy()
            left_low = low[i - L : i + 1].min()
            right_low = low[i : i + R + 1].min()
            prominence = high[i] - max(left_low, right_low)
            if prominence < params.prominence_atr * atr_i:
                continue
        out[i] = True
    return pd.Series(out, index=df.index, name="is_pivot_high")


def find_pivot_lows(
    df: pd.DataFrame, params: PivotParams, atr_col: str = "atr"
) -> pd.Series:
    """Return a boolean Series. True at index i if i is a confirmed pivot low."""
    L, R = params.left, params.right
    low = df["low"].to_numpy()
    n = len(df)
    out = np.zeros(n, dtype=bool)

    for i in range(L, n - R):
        left = low[i - L : i]
        right = low[i + 1 : i + R + 1]
        if not (low[i] < left.min() and low[i] <= right.min()):
            continue
        if params.use_prominence:
            atr_i = df[atr_col].iloc[i]
            if np.isnan(atr_i) or atr_i <= 0:
                continue
            high = df["high"].to_numpy()
            left_high = high[i - L : i + 1].max()
            right_high = high[i : i + R + 1].max()
            prominence = min(left_high, right_high) - low[i]
            if prominence < params.prominence_atr * atr_i:
                continue
        out[i] = True
    return pd.Series(out, index=df.index, name="is_pivot_low")


def pivot_indices(mask: pd.Series) -> list[int]:
    """Return positional indices where mask is True."""
    return [int(i) for i in np.flatnonzero(mask.to_numpy())]
