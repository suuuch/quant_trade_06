"""Smoke tests for the Backtrader integration."""

import pandas as pd
import pytest

from quant_trade.backtest import run_backtest


def _daily_frame(rows: int = 80) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=rows, freq="D")
    close = pd.Series([100.0 + index * 0.2 for index in range(rows)], index=index)
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000.0,
        },
        index=index,
    )


def test_backtest_runs_on_daily_ohlcv_data() -> None:
    result = run_backtest(_daily_frame(), initial_cash=100_000.0)

    assert result.initial_cash == 100_000.0
    assert result.final_value == 100_000.0
    assert result.closed_trades == 0


def test_backtest_requires_datetime_index() -> None:
    frame = _daily_frame().reset_index(drop=True)

    with pytest.raises(ValueError, match="DatetimeIndex"):
        run_backtest(frame)


def test_backtest_executes_confirmed_long_signal() -> None:
    closes = (
        [45.04] * 35
        + [118.70, 128.32, 128.33, 132.96, 138.33]
        + [
            73.02,
            74.40,
            79.16,
            76.24,
            76.09,
            74.44,
            74.14,
            73.02,
            74.83,
            74.75,
            75.99,
            80.63,
            81.63,
            82.63,
        ]
    )
    index = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    close = pd.Series(closes, index=index)
    frame = pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1_000.0,
        },
        index=index,
    )

    result = run_backtest(
        frame,
        initial_cash=100_000.0,
        commission=0.0,
    )

    assert result.final_value > result.initial_cash
