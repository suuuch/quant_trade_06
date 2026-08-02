"""Neckline + break/invalidation helpers (spec §3.2 - §3.4)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def neckline_short(df: pd.DataFrame, h1_idx: int, h2_idx: int) -> float:
    """M-top: min(low) between H1 and H2 (inclusive on both ends)."""
    if h2_idx <= h1_idx:
        raise ValueError("h2_idx must be > h1_idx")
    return float(df["low"].iloc[h1_idx : h2_idx + 1].min())


def neckline_long(df: pd.DataFrame, l1_idx: int, l2_idx: int) -> float:
    """W-bottom: max(high) between L1 and L2 (inclusive on both ends)."""
    if l2_idx <= l1_idx:
        raise ValueError("l2_idx must be > l1_idx")
    return float(df["high"].iloc[l1_idx : l2_idx + 1].max())


def break_below_at(
    close: pd.Series,
    atr: pd.Series,
    neckline: float,
    buffer_atr: float,
    mode: str,
    volume: pd.Series | None = None,
    vol_ma: pd.Series | None = None,
) -> int:
    """Return positional index of the first qualifying break, or -1.

    mode:
        "loose"    — close < neckline - buffer*ATR
        "standard" — two consecutive closes < neckline - buffer*ATR
        "strict"   — one close < neckline - buffer*ATR AND volume > vol_ma
    """
    if mode not in {"loose", "standard", "strict"}:
        raise ValueError(f"Unknown break mode: {mode}")
    n = len(close)
    threshold = neckline - buffer_atr * atr.to_numpy()
    closes = close.to_numpy()

    for i in range(n):
        if np.isnan(threshold[i]) or np.isnan(closes[i]):
            continue
        if closes[i] >= threshold[i]:
            continue
        if mode == "loose":
            return i
        if mode == "standard":
            # Return the SECOND (confirming) bar of the two-bar pair, since the
            # break is only known once bar i+1 confirms it.
            if i + 1 < n and not np.isnan(closes[i + 1]) and not np.isnan(threshold[i + 1]):
                if closes[i + 1] < threshold[i + 1]:
                    return i + 1
            continue
        if mode == "strict":
            if volume is None or vol_ma is None:
                raise ValueError("strict mode requires volume and vol_ma")
            v = volume.to_numpy()
            vm = vol_ma.to_numpy()
            if not np.isnan(v[i]) and not np.isnan(vm[i]) and v[i] > vm[i]:
                return i
    return -1


def break_above_at(
    close: pd.Series,
    atr: pd.Series,
    neckline: float,
    buffer_atr: float,
    mode: str,
    volume: pd.Series | None = None,
    vol_ma: pd.Series | None = None,
) -> int:
    """Mirror of break_below_at for long setups."""
    if mode not in {"loose", "standard", "strict"}:
        raise ValueError(f"Unknown break mode: {mode}")
    n = len(close)
    threshold = neckline + buffer_atr * atr.to_numpy()
    closes = close.to_numpy()

    for i in range(n):
        if np.isnan(threshold[i]) or np.isnan(closes[i]):
            continue
        if closes[i] <= threshold[i]:
            continue
        if mode == "loose":
            return i
        if mode == "standard":
            # Return the SECOND (confirming) bar of the two-bar pair.
            if i + 1 < n and not np.isnan(closes[i + 1]) and not np.isnan(threshold[i + 1]):
                if closes[i + 1] > threshold[i + 1]:
                    return i + 1
            continue
        if mode == "strict":
            if volume is None or vol_ma is None:
                raise ValueError("strict mode requires volume and vol_ma")
            v = volume.to_numpy()
            vm = vol_ma.to_numpy()
            if not np.isnan(v[i]) and not np.isnan(vm[i]) and v[i] > vm[i]:
                return i
    return -1


def invalid_short(
    close: float, h1: float, h2: float, atr_val: float, buffer_atr: float
) -> bool:
    return close > max(h1, h2) + buffer_atr * atr_val


def invalid_long(
    close: float, l1: float, l2: float, atr_val: float, buffer_atr: float
) -> bool:
    return close < min(l1, l2) - buffer_atr * atr_val
