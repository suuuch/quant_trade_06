"""S3: RSI 50 area trend-following signal (spec §6).

This is a simplified v1 implementation. We use the higher-low / lower-high
fallback rather than re-detecting full M/W patterns, to keep the engine fast
and the code decoupled from S1/S2.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import PatternParams, Rsi50Params
from .common import Signal, make_signal


def _higher_low(close: pd.Series, low: pd.Series, lookback: int) -> pd.Series:
    """True at bar i if low[i] is the lowest in (i-lookback, i] AND
    there exists a lower low in (i-2*lookback, i-lookback] (forming a
    higher-low sequence)."""
    n = len(close)
    out = np.zeros(n, dtype=bool)
    lows = low.to_numpy()
    for i in range(2 * lookback, n):
        recent_window = lows[i - lookback : i + 1]
        if lows[i] != recent_window.min():
            continue
        prior_window = lows[i - 2 * lookback : i - lookback]
        if prior_window.size and lows[i] > prior_window.min():
            out[i] = True
    return pd.Series(out, index=close.index, name="higher_low")


def _lower_high(close: pd.Series, high: pd.Series, lookback: int) -> pd.Series:
    n = len(close)
    out = np.zeros(n, dtype=bool)
    highs = high.to_numpy()
    for i in range(2 * lookback, n):
        recent_window = highs[i - lookback : i + 1]
        if highs[i] != recent_window.max():
            continue
        prior_window = highs[i - 2 * lookback : i - lookback]
        if prior_window.size and highs[i] < prior_window.max():
            out[i] = True
    return pd.Series(out, index=close.index, name="lower_high")


def detect_rsi50(
    df: pd.DataFrame,
    rsi50_params: Rsi50Params,
    pattern_params: PatternParams,
    symbol: str = "",
) -> list[Signal]:
    """Detect RSI 50 area trend-following entries.

    Long: MA20/30 slopes up, RSI in [lo,hi], higher-low detected,
          close > prior recent high (proxy neckline), RSI crosses >= required.
    Short: mirror.
    """
    rsi = df["rsi"]
    ma_fast_slope = df["ma_fast_slope"]
    ma_slow_slope = df["ma_slow_slope"]
    close = df["close"]
    high = df["high"]
    low = df["low"]

    lo, hi = rsi50_params.zone
    required_long = float(rsi50_params.rsi_required_long)
    required_short = float(rsi50_params.rsi_required_short)
    lookback = rsi50_params.higher_low_lookback
    window = rsi50_params.rsi_confirm_window

    rsi_arr = rsi.to_numpy()
    close_arr = close.to_numpy()
    high_arr = high.to_numpy()
    low_arr = low.to_numpy()
    mfast_slope = ma_fast_slope.to_numpy()
    mslow_slope = ma_slow_slope.to_numpy()
    n = len(df)

    hl_mask = _higher_low(close, low, lookback).to_numpy()
    lh_mask = _lower_high(close, high, lookback).to_numpy()

    signals: list[Signal] = []
    inv_buf = pattern_params.invalidation_buffer_atr
    stop_buf = pattern_params.stop_loss_buffer_atr
    atr_arr = df["atr"].to_numpy()

    for i in range(n):
        if pd.isna(rsi_arr[i]) or pd.isna(mfast_slope[i]) or pd.isna(mslow_slope[i]):
            continue

        # Long setup
        if (
            mfast_slope[i] > 0
            and mslow_slope[i] > 0
            and lo <= rsi_arr[i] <= hi
            and hl_mask[i]
        ):
            # proxy neckline = high in the higher-low formation window
            neck = float(high_arr[i - lookback : i + 1].max())
            if close_arr[i] > neck:
                # check RSI within ±window bars crosses >= required_long
                hi_end = min(n, i + window + 1)
                if np.nanmax(rsi_arr[i : hi_end]) >= required_long:
                    atr_i = atr_arr[i] if not np.isnan(atr_arr[i]) else 0.0
                    stop_loss = float(low_arr[i - lookback : i + 1].min()) - stop_buf * atr_i
                    invalidation = close_arr[i] - inv_buf * atr_i
                    signals.append(
                        make_signal(
                            df=df,
                            pattern="RSI50_trend",
                            direction="long",
                            entry_idx=i,
                            entry_price=float(close_arr[i]),
                            stop_loss=stop_loss,
                            invalidation=invalidation,
                            rsi_at_trigger=float(rsi_arr[i]),
                            symbol=symbol,
                            notes=f"RSI in {rsi50_params.zone}, higher-low confirmed, close>neckline={neck:.2f}",
                        )
                    )
                    continue  # avoid double-firing long/short on the same bar

        # Short setup
        if (
            mfast_slope[i] < 0
            and mslow_slope[i] < 0
            and lo <= rsi_arr[i] <= hi
            and lh_mask[i]
        ):
            neck = float(low_arr[i - lookback : i + 1].min())
            if close_arr[i] < neck:
                hi_end = min(n, i + window + 1)
                if np.nanmin(rsi_arr[i : hi_end]) <= required_short:
                    atr_i = atr_arr[i] if not np.isnan(atr_arr[i]) else 0.0
                    stop_loss = float(high_arr[i - lookback : i + 1].max()) + stop_buf * atr_i
                    invalidation = close_arr[i] + inv_buf * atr_i
                    signals.append(
                        make_signal(
                            df=df,
                            pattern="RSI50_trend",
                            direction="short",
                            entry_idx=i,
                            entry_price=float(close_arr[i]),
                            stop_loss=stop_loss,
                            invalidation=invalidation,
                            rsi_at_trigger=float(rsi_arr[i]),
                            symbol=symbol,
                            notes=f"RSI in {rsi50_params.zone}, lower-high confirmed, close<neckline={neck:.2f}",
                        )
                    )
    return signals
