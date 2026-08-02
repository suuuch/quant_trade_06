"""Technical indicators — MA, RSI (Wilder), ATR (Wilder), slope.

All return pandas Series aligned to the input DataFrame's index.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import IndicatorParams


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(period, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-smoothed RSI.

    First `period` bars are NaN; the first emitted value uses the SMA seed,
    matching backtrader / TA-Lib behavior.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder smoothing uses EMA with alpha = 1/period on the gains/losses
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # When avg_loss == 0, RSI is 100 by convention
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def true_range(
    high: pd.Series, low: pd.Series, close: pd.Series
) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Wilder-smoothed ATR (alpha = 1/period)."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def slope(series: pd.Series, lookback: int) -> pd.Series:
    """MA[i] - MA[i-lookback]. Positive = up, negative = down."""
    return series - series.shift(lookback)


def add_indicators(df: pd.DataFrame, params: IndicatorParams) -> pd.DataFrame:
    """Add MA/MA/RSI/ATR/slope/vol_ma columns to df in place. Returns df."""
    out = df.copy()
    out["ma_fast"] = sma(out["close"], params.ma_fast)
    out["ma_slow"] = sma(out["close"], params.ma_slow)
    out["rsi"] = rsi(out["close"], params.rsi_period)
    out["atr"] = atr(out["high"], out["low"], out["close"], params.atr_period)
    out["ma_fast_slope"] = slope(out["ma_fast"], params.ma_slope_lookback)
    out["ma_slow_slope"] = slope(out["ma_slow"], params.ma_slope_lookback)
    out["vol_ma"] = sma(out["volume"].astype(float), params.vol_ma)
    return out
