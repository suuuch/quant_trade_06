"""S1: M-top short pattern detection (spec §4).

For every confirmed (h1, h2) pair that passes geometry + RSI filters, find
the first neckline break (or note pattern invalidation) and emit a Signal.
"""

from __future__ import annotations

import pandas as pd

from ..config import PatternParams
from ..neckline import (
    break_below_at,
    invalid_short,
    neckline_short,
)
from ..pivots import find_pivot_highs, pivot_indices
from .common import Signal, make_signal


def _rsi_in_band(
    rsi: pd.Series, idx: int, lo: float, hi: float, lookback: int
) -> bool:
    """RSI in [lo, hi] at any bar within ±lookback of idx (inclusive)."""
    n = len(rsi)
    lo_idx = max(0, idx - lookback)
    hi_idx = min(n, idx + lookback + 1)
    window = rsi.iloc[lo_idx:hi_idx]
    if window.isna().all():
        return False
    return bool(window.between(lo, hi).any())


def detect_m_top(
    df: pd.DataFrame,
    pivot_params,
    pattern_params: PatternParams,
) -> list[Signal]:
    """Scan df for M-top short candidates. df must have rsi, atr, vol_ma, ohlc."""
    pivot_mask = find_pivot_highs(df, pivot_params)
    h_indices = pivot_indices(pivot_mask)
    if len(h_indices) < 2:
        return []

    close = df["close"]
    atr_s = df["atr"]
    rsi_s = df["rsi"]
    vol = df["volume"] if "volume" in df.columns else None
    vol_ma = df["vol_ma"] if "vol_ma" in df.columns else None

    signals: list[Signal] = []
    min_dist = pattern_params.min_top_distance
    max_dist = pattern_params.max_top_distance
    max_diff = pattern_params.max_top_difference_atr
    min_pull = pattern_params.min_middle_pullback_atr
    inv_buf = pattern_params.invalidation_buffer_atr
    stop_buf = pattern_params.stop_loss_buffer_atr

    high = df["high"]
    n = len(df)

    for i, h1 in enumerate(h_indices):
        for h2 in h_indices[i + 1 :]:
            if h2 - h1 < min_dist:
                continue
            if h2 - h1 > max_dist:
                break  # h_indices sorted ascending: all later h2 are also out of range
            h1_price = float(high.iloc[h1])
            h2_price = float(high.iloc[h2])
            atr_h2 = float(atr_s.iloc[h2])
            if atr_h2 <= 0 or pd.isna(atr_h2):
                continue
            if abs(h1_price - h2_price) > max_diff * atr_h2:
                continue
            neck = neckline_short(df, h1, h2)
            middle_pullback = min(h1_price, h2_price) - neck
            if middle_pullback < min_pull * atr_h2:
                continue
            rsi_band_ok = _rsi_in_band(
                rsi_s, h1, 75.0, 85.0, pattern_params.rsi_lookback
            ) or _rsi_in_band(
                rsi_s, h2, 75.0, 85.0, pattern_params.rsi_lookback
            )
            if not rsi_band_ok:
                continue
            h1_rsi = float(rsi_s.iloc[h1])
            h2_rsi = float(rsi_s.iloc[h2])
            divergence = h2_rsi < h1_rsi  # priority hint, not blocking

            # Lifecycle: scan bars after h2
            start = h2 + 1
            if start >= n:
                continue
            close_after = close.iloc[start:]
            atr_after = atr_s.iloc[start:]
            vol_after = vol.iloc[start:] if vol is not None else None
            vol_ma_after = vol_ma.iloc[start:] if vol_ma is not None else None

            break_idx = break_below_at(
                close_after,
                atr_after,
                neck,
                pattern_params.break_buffer_atr,
                pattern_params.break_confirm,
                volume=vol_after,
                vol_ma=vol_ma_after,
            )
            inv_idx = -1
            for j in range(len(close_after)):
                c = float(close_after.iloc[j])
                a = float(atr_after.iloc[j]) if not pd.isna(atr_after.iloc[j]) else atr_h2
                if invalid_short(c, h1_price, h2_price, a, inv_buf):
                    inv_idx = j
                    break

            if break_idx == -1:
                continue
            if inv_idx != -1 and inv_idx < break_idx:
                continue

            # Rule 8: MA20 down OR close < MA20
            ma_fast = df["ma_fast"].iloc[start + break_idx]
            close_at_break = float(close_after.iloc[break_idx])
            if not (
                (pd.notna(ma_fast) and float(df["ma_fast_slope"].iloc[start + break_idx]) < 0)
                or close_at_break < float(ma_fast)
            ):
                continue

            entry_idx = start + break_idx
            entry_price = close_at_break
            stop_loss = h2_price + stop_buf * atr_h2
            invalidation = max(h1_price, h2_price) + inv_buf * atr_h2

            signals.append(
                make_signal(
                    df=df,
                    pattern="M_top",
                    direction="short",
                    entry_idx=entry_idx,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    invalidation=invalidation,
                    rsi_at_trigger=float(rsi_s.iloc[entry_idx]),
                    notes=(
                        f"h1={h1_price:.2f}@{df.index[h1].date()}, "
                        f"h2={h2_price:.2f}@{df.index[h2].date()}, "
                        f"neckline={neck:.2f}, divergence={'yes' if divergence else 'no'}"
                    ),
                )
            )
    return signals
