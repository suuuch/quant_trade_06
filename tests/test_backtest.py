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
    closes = [80.0] * 35 + [118.0, 119.0, 120.0, 121.0, 122.0] + [
        85.0,
        87.0,
        89.0,
        87.0,
        86.0,
        87.0,
        86.0,
        85.0,
        86.0,
        87.0,
        88.0,
        90.0,
        91.0,
        92.0,
    ]
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
