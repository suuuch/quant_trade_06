"""RSI 50 trend-following strategy package."""

from quant_trade.backtest import BacktestResult, run_backtest
from quant_trade.data import load_duckdb_bars
from quant_trade.rsi50 import (
    Bar,
    Direction,
    Rsi50Config,
    Rsi50SignalEngine,
    Signal,
)
from quant_trade.strategy import Rsi50TrendStrategy

__all__ = [
    "BacktestResult",
    "Bar",
    "Direction",
    "Rsi50Config",
    "Rsi50SignalEngine",
    "Rsi50TrendStrategy",
    "Signal",
    "load_duckdb_bars",
    "run_backtest",
]
