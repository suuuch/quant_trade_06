"""S3: RSI 50 area trend-following signal (spec §6).

Simplified v1 implementation: we use the higher-low / lower-high fallback
rather than re-detecting full M/W patterns, to keep the engine fast and the
code decoupled from S1/S2.

The spec's conditions A/B/C/D/E (§6.1.1) cannot realistically all hold on the
same bar: after a pullback, RSI recovers out of the [45,55] zone much faster
than MA30's slope turns positive, so a same-bar scan is effectively dead. We
keep the spec's *intent* as a sequence:

  B   RSI was inside the [lo, hi] zone at some point in the recent
      `lookback` bars (the pullback that cooled RSI);
  E   RSI is now back at/above `required_long` (the resumption);
  C   price is holding above the recent swing low (higher low);
  D   close breaks above the neckline = max(high) over the `lookback` bars
      (+ break buffer);
  A   MA20 and MA30 slopes are both up.

A latch keeps the signal to one entry per pullback episode: after a long
entry we stay disarmed until RSI falls back through the threshold (the next
pullback); mirror for short. Entry is emitted on the trigger bar using only
data up to that bar — no lookahead. Short is the mirror.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import PatternParams, Rsi50Params
from .common import Signal, make_signal


def _higher_low(close: pd.Series, low: pd.Series, lookback: int) -> pd.Series:
    """True at bar i if low[i] is above the lowest low of the prior
    `lookback` bars (spec §6.1.1-C: `low[i] > low[lowest in last N bars]`) —
    price is holding above the recent swing low, not making a new low."""
    n = len(close)
    out = np.zeros(n, dtype=bool)
    lows = low.to_numpy()
    for i in range(lookback, n):
        if lows[i] > lows[i - lookback : i].min():
            out[i] = True
    return pd.Series(out, index=close.index, name="higher_low")


def _lower_high(close: pd.Series, high: pd.Series, lookback: int) -> pd.Series:
    """Mirror of _higher_low: high[i] below the highest high of the prior
    `lookback` bars (spec §6.1.2-C)."""
    n = len(close)
    out = np.zeros(n, dtype=bool)
    highs = high.to_numpy()
    for i in range(lookback, n):
        if highs[i] < highs[i - lookback : i].max():
            out[i] = True
    return pd.Series(out, index=close.index, name="lower_high")


def detect_rsi50(
    df: pd.DataFrame,
    rsi50_params: Rsi50Params,
    pattern_params: PatternParams,
    symbol: str = "",
) -> list[Signal]:
    """Detect RSI 50 area trend-following entries.

    Long (spec §6.1.1): MA20/30 slopes up ∧ RSI recently in the zone ∧
    higher-low ∧ close freshly breaks the `lookback`-bar high neckline ∧ RSI
    back at/above `required_long`. Entry emitted on the breakout bar.
    Short (spec §6.1.2): mirror.
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

    rsi_arr = rsi.to_numpy()
    close_arr = close.to_numpy()
    high_arr = high.to_numpy()
    low_arr = low.to_numpy()
    mfast_slope = ma_fast_slope.to_numpy()
    mslow_slope = ma_slow_slope.to_numpy()
    atr_arr = df["atr"].to_numpy()
    n = len(df)

    hl_mask = _higher_low(close, low, lookback).to_numpy()
    lh_mask = _lower_high(close, high, lookback).to_numpy()

    break_buf = pattern_params.break_buffer_atr
    inv_buf = pattern_params.invalidation_buffer_atr
    stop_buf = pattern_params.stop_loss_buffer_atr

    signals: list[Signal] = []
    # Latch: one signal per pullback episode. After an entry we stay disarmed
    # until RSI falls back through the threshold (long) / rises back above it
    # (short), i.e. the next pullback into the zone.
    armed_long = True
    armed_short = True

    for i in range(lookback + 1, n):
        if (
            pd.isna(rsi_arr[i])
            or pd.isna(mfast_slope[i])
            or pd.isna(mslow_slope[i])
            or pd.isna(atr_arr[i])
        ):
            continue

        # Re-arm for each direction before evaluating the trigger.
        if not pd.isna(rsi_arr[i]):
            if rsi_arr[i] < required_long:
                armed_long = True
            if rsi_arr[i] > required_short:
                armed_short = True

        # Long setup + trigger at bar i (spec §6.1.1 A/B/C/D/E).
        if (
            armed_long
            and mfast_slope[i] > 0
            and mslow_slope[i] > 0
            and hl_mask[i]
            and rsi_arr[i] >= required_long
            and np.nanmin(rsi_arr[i - lookback : i]) <= hi  # RSI cooled to the zone
        ):
            neck = float(high_arr[i - lookback : i].max())
            if close_arr[i] > neck + break_buf * atr_arr[i]:
                swing_low = float(low_arr[i - lookback : i + 1].min())
                stop_loss = swing_low - stop_buf * atr_arr[i]
                invalidation = swing_low - inv_buf * atr_arr[i]
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
                        notes=(
                            f"RSI in {rsi50_params.zone} recently, "
                            f"close>neckline={neck:.2f}, rsi crossed {required_long:.0f}"
                        ),
                    )
                )
                armed_long = False

        # Short mirror (spec §6.1.2).
        if (
            armed_short
            and mfast_slope[i] < 0
            and mslow_slope[i] < 0
            and lh_mask[i]
            and rsi_arr[i] <= required_short
            and np.nanmax(rsi_arr[i - lookback : i]) >= lo  # RSI climbed to the zone
        ):
            neck = float(low_arr[i - lookback : i].min())
            if close_arr[i] < neck - break_buf * atr_arr[i]:
                swing_high = float(high_arr[i - lookback : i + 1].max())
                stop_loss = swing_high + stop_buf * atr_arr[i]
                invalidation = swing_high + inv_buf * atr_arr[i]
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
                        notes=(
                            f"RSI in {rsi50_params.zone} recently, "
                            f"close<neckline={neck:.2f}, rsi crossed {required_short:.0f}"
                        ),
                    )
                )
                armed_short = False
    return signals
