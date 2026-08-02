"""S2: W-bottom long pattern detection (spec §5)."""

from __future__ import annotations

import pandas as pd

from ..config import PatternParams
from ..neckline import (
    break_above_at,
    invalid_long,
    neckline_long,
)
from ..pivots import find_pivot_lows, pivot_indices
from .common import Signal, make_signal


def _rsi_in_band(
    rsi: pd.Series, idx: int, lo: float, hi: float, lookback: int
) -> bool:
    n = len(rsi)
    lo_idx = max(0, idx - lookback)
    hi_idx = min(n, idx + lookback + 1)
    window = rsi.iloc[lo_idx:hi_idx]
    if window.isna().all():
        return False
    return bool(window.between(lo, hi).any())


def detect_w_bottom(
    df: pd.DataFrame,
    pivot_params,
    pattern_params: PatternParams,
    symbol: str = "",
) -> list[Signal]:
    """Scan df for W-bottom long candidates.

    Rule 6 (divergence) is treated the same way as S1 rule 6: a priority hint,
    not a blocking filter. Bottom divergence = L2 RSI > L1 RSI (spec §1). The
    original (logically inverted) wording in strategy.md was replaced per user
    request; see README "Known TODOs".
    """
    pivot_low_mask = find_pivot_lows(df, pivot_params)
    l_indices = pivot_indices(pivot_low_mask)
    if len(l_indices) < 2:
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

    low = df["low"]
    n = len(df)

    for i, l1 in enumerate(l_indices):
        for l2 in l_indices[i + 1 :]:
            if l2 - l1 < min_dist:
                continue
            if l2 - l1 > max_dist:
                break  # l_indices sorted ascending: all later l2 are also out of range
            l1_price = float(low.iloc[l1])
            l2_price = float(low.iloc[l2])
            atr_l2 = float(atr_s.iloc[l2])
            if atr_l2 <= 0 or pd.isna(atr_l2):
                continue
            if abs(l1_price - l2_price) > max_diff * atr_l2:
                continue
            neck = neckline_long(df, l1, l2)
            middle_pullback = neck - max(l1_price, l2_price)
            if middle_pullback < min_pull * atr_l2:
                continue
            rsi_band_ok = _rsi_in_band(
                rsi_s, l1, 15.0, 25.0, pattern_params.rsi_lookback
            ) or _rsi_in_band(
                rsi_s, l2, 15.0, 25.0, pattern_params.rsi_lookback
            )
            if not rsi_band_ok:
                continue

            # Rule 6: bottom divergence (L2 RSI > L1 RSI). Priority hint,
            # informational only — not a blocking filter (mirrors S1 rule 6).
            l1_rsi = float(rsi_s.iloc[l1])
            l2_rsi = float(rsi_s.iloc[l2])
            divergence = l2_rsi > l1_rsi

            start = l2 + 1
            if start >= n:
                continue
            close_after = close.iloc[start:]
            atr_after = atr_s.iloc[start:]
            vol_after = vol.iloc[start:] if vol is not None else None
            vol_ma_after = vol_ma.iloc[start:] if vol_ma is not None else None

            break_idx = break_above_at(
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
                a = float(atr_after.iloc[j]) if not pd.isna(atr_after.iloc[j]) else atr_l2
                if invalid_long(c, l1_price, l2_price, a, inv_buf):
                    inv_idx = j
                    break

            if break_idx == -1:
                continue
            if inv_idx != -1 and inv_idx < break_idx:
                continue

            # Rule 8: MA20 up OR close > MA20
            ma_fast = df["ma_fast"].iloc[start + break_idx]
            close_at_break = float(close_after.iloc[break_idx])
            if not (
                (pd.notna(ma_fast) and float(df["ma_fast_slope"].iloc[start + break_idx]) > 0)
                or close_at_break > float(ma_fast)
            ):
                continue

            entry_idx = start + break_idx
            entry_price = close_at_break
            stop_loss = l2_price - stop_buf * atr_l2
            invalidation = min(l1_price, l2_price) - inv_buf * atr_l2

            signals.append(
                make_signal(
                    df=df,
                    pattern="W_bottom",
                    direction="long",
                    entry_idx=entry_idx,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    invalidation=invalidation,
                    rsi_at_trigger=float(rsi_s.iloc[entry_idx]),
                    symbol=symbol,
                    notes=(
                        f"l1={l1_price:.2f}@{df.index[l1].date()}, "
                        f"l2={l2_price:.2f}@{df.index[l2].date()}, "
                        f"neckline={neck:.2f}, divergence={'yes' if divergence else 'no'}"
                    ),
                )
            )
    return signals
