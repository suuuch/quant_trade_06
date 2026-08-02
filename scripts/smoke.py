"""Smoke test: import, run engine on a tiny synthetic series, no DB."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_trade.config import StrategyParams
from quant_trade.engine import run_engine
from quant_trade.indicators import add_indicators, atr, rsi, sma


def make_synthetic(n: int = 200) -> pd.DataFrame:
    """Construct a price series with two clear M-tops and a W-bottom."""
    rng = np.random.default_rng(0)
    base = 100.0
    closes = np.full(n, base)
    # W-bottom around bar 40-70
    closes[30:40] = np.linspace(base, 80, 10)  # down
    closes[40:55] = np.linspace(80, 82, 15)  # up
    closes[55:65] = np.linspace(82, 78, 10)  # down again to lower
    closes[65:90] = np.linspace(78, 110, 25)  # up to H1

    # M-top around bar 90-130
    closes[90:100] = np.linspace(110, 95, 10)  # pullback
    closes[100:110] = np.linspace(95, 108, 10)  # up to H2 (slightly lower)
    closes[110:130] = np.linspace(108, 70, 20)  # break down

    closes[130:200] = np.linspace(70, 90, 70) + rng.normal(0, 0.5, 70)
    closes[40:65] += rng.normal(0, 0.5, 25)
    closes[90:130] += rng.normal(0, 0.5, 40)
    closes[0:30] = np.linspace(base - 5, base, 30) + rng.normal(0, 0.3, 30)

    closes = np.maximum(closes, 1.0)
    highs = closes + rng.uniform(0.5, 1.5, n)
    lows = closes - rng.uniform(0.5, 1.5, n)
    opens = closes + rng.normal(0, 0.3, n)
    volumes = rng.integers(1_000_000, 2_000_000, n).astype(float)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def main() -> None:
    df = make_synthetic(200)
    print(f"synthetic bars: {len(df)}")
    print("sma(20) tail:", sma(df["close"], 20).tail(3).round(2).to_list())
    print("rsi(14) tail:", rsi(df["close"], 14).tail(3).round(2).to_list())
    print("atr(14) tail:", atr(df["high"], df["low"], df["close"], 14).tail(3).round(2).to_list())

    params = StrategyParams(frequency="daily")
    sigs = run_engine(df, params, symbol="SYN")
    print(f"\nsignals: {len(sigs)}")
    for s in sigs:
        print(
            f"  {s.triggered_at.strftime('%Y-%m-%d')} {s.pattern:14s} {s.direction:5s}"
            f" entry={s.trigger_price:7.2f} stop={s.stop_loss:7.2f} "
            f"inval={s.invalidation_price:7.2f} rsi={s.rsi_at_trigger:5.1f}"
        )


if __name__ == "__main__":
    main()
