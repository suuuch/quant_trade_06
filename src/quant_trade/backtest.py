"""Backtrader backtest runner for the daily RSI trend-following strategy."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportCallIssue=false

from dataclasses import dataclass
from typing import Any, cast

import backtrader as bt
import pandas as pd

from quant_trade.rsi50 import Rsi50Config
from quant_trade.strategy import Rsi50TrendStrategy


@dataclass(frozen=True)
class BacktestResult:
    """Summary metrics from one Backtrader run."""

    initial_cash: float
    final_value: float
    return_percent: float
    max_drawdown_percent: float
    closed_trades: int
    won_trades: int
    lost_trades: int


def run_backtest(
    frame: pd.DataFrame,
    *,
    initial_cash: float = 1_000_000.0,
    commission: float = 0.001,
    config: Rsi50Config | None = None,
    target_percent: float = 0.95,
    allow_short: bool = True,
) -> BacktestResult:
    """Run the strategy against a datetime-indexed daily OHLCV frame."""
    data = _validate_frame(frame)
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    if commission < 0:
        raise ValueError("commission must not be negative")

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.adddata(bt.feeds.PandasData(dataname=data))
    cerebro.addstrategy(
        Rsi50TrendStrategy,
        config=config,
        target_percent=target_percent,
        allow_short=allow_short,
    )
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

    strategies = cerebro.run()
    strategy = strategies[0]
    final_value = float(cerebro.broker.getvalue())
    drawdown = cast(dict[str, Any], strategy.analyzers.drawdown.get_analysis())
    trades = cast(dict[str, Any], strategy.analyzers.trades.get_analysis())
    total = trades.get("total", {})
    won = trades.get("won", {})
    lost = trades.get("lost", {})
    return BacktestResult(
        initial_cash=initial_cash,
        final_value=final_value,
        return_percent=(final_value / initial_cash - 1.0) * 100.0,
        max_drawdown_percent=float(drawdown.get("max", {}).get("drawdown", 0.0)),
        closed_trades=int(total.get("closed", 0)),
        won_trades=int(won.get("total", 0)),
        lost_trades=int(lost.get("total", 0)),
    )


def _validate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("frame index must be a pandas DatetimeIndex")
    normalized = frame.rename(columns={column: str(column).lower() for column in frame})
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(normalized.columns)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"frame is missing columns: {names}")
    if not normalized.index.is_monotonic_increasing:
        raise ValueError("frame index must be sorted in increasing order")
    if normalized.index.has_duplicates:
        raise ValueError("frame index must not contain duplicate timestamps")
    if normalized[list(required)].isna().any().any():
        raise ValueError("OHLCV columns must not contain missing values")
    return normalized
