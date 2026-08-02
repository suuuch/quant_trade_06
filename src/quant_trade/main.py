"""End-to-end backtest entry point.

Usage:
    uv run python -m quant_trade.main --symbol 000001.SZ --start 20240101 --end 20260720
    uv run python -m quant_trade.main --symbols 000001.SZ,600519.SH --frequency daily
"""

from __future__ import annotations

import argparse

import backtrader as bt
import pandas as pd

from .config import StrategyParams
from .data_source import CsvDataSource, DataSource, PostgresDataSource
from .engine import run_engine
from .strategy_bt import SignalStrategy


def build_data_source(args: argparse.Namespace) -> DataSource:
    if args.csv:
        return CsvDataSource(args.csv)
    return PostgresDataSource()


def signals_for_symbol(
    ds: DataSource,
    symbol: str,
    params: StrategyParams,
    start: str,
    end: str,
) -> tuple[pd.DataFrame, list]:
    df = ds.load_ohlcv(symbol, params.frequency, start, end)
    if df.empty:
        return df, []
    sigs = run_engine(df, params, symbol=symbol)
    return df, sigs


def run_backtest(
    df: pd.DataFrame,
    signals: list,
    initial_cash: float = 1_000_000.0,
    commission: float = 0.0003,
    position_pct: float = 0.10,
    printlog: bool = False,
) -> tuple[bt.Cerebro, bt.Strategy]:
    cerebro = bt.Cerebro(stdstats=False)  # type: ignore[arg-type]
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.broker.set_coc(True)  # execute at signal bar's close

    feed = bt.feeds.PandasData(dataname=df)  # type: ignore[call-arg]
    cerebro.adddata(feed)

    cerebro.addstrategy(
        SignalStrategy,
        signals=signals,
        position_pct=position_pct,
        printlog=printlog,
    )

    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")

    strat = cerebro.run()
    return cerebro, strat[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="A-share M/W/RSI50 strategy backtester")
    ap.add_argument("--symbol", help="Single symbol (e.g. 000001.SZ)")
    ap.add_argument("--symbols", help="Comma-separated symbols")
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--end", default="20991231")
    ap.add_argument("--frequency", default="daily", choices=["daily", "hourly"])
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--position-pct", type=float, default=0.10)
    ap.add_argument("--csv", help="Use a local CSV (date,open,high,low,close,volume) instead of PG")
    ap.add_argument("--printlog", action="store_true")
    args = ap.parse_args()

    if args.symbol:
        symbols = [args.symbol]
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        ap.error("Provide --symbol or --symbols")

    params = StrategyParams(frequency=args.frequency)

    ds = build_data_source(args)
    total_signals: list = []
    for sym in symbols:
        df, sigs = signals_for_symbol(ds, sym, params, args.start, args.end)
        print(f"\n=== {sym} ===")
        print(f"  bars: {len(df)}  signals: {len(sigs)}")
        for s in sigs:
            print(
                f"  [{s.triggered_at.strftime('%Y-%m-%d')}] {s.pattern:14s} {s.direction:5s}"
                f" entry={s.trigger_price:8.2f} stop={s.stop_loss:8.2f} "
                f"inval={s.invalidation_price:8.2f} rsi={s.rsi_at_trigger:5.1f}"
            )
        total_signals.extend(sigs)
        if df.empty or not sigs:
            continue
        cerebro, strat = run_backtest(
            df, sigs,
            initial_cash=args.cash,
            position_pct=args.position_pct,
            printlog=args.printlog,
        )
        sharpe = strat.analyzers.sharpe.get_analysis().get("sharperatio")
        dd = strat.analyzers.dd.get_analysis()
        ta = strat.analyzers.ta.get_analysis()
        print(f"  final value: {cerebro.broker.getvalue():.2f}")
        print(f"  sharpe: {sharpe}")
        max_dd = dd.get("max", {}).get("drawdown") if hasattr(dd, "get") else None
        if max_dd is not None:
            print(f"  max drawdown: {max_dd:.2f}%")
        closed = ta.get("total", {}).get("closed", 0) if hasattr(ta, "get") else 0
        if closed:
            print(f"  closed trades: {closed}")
            net = ta.get("pnl", {}).get("net", {}).get("total")
            if net is not None:
                print(f"  net pnl: {net:.2f}")
        if strat.trade_log:
            print("  trade log:")
            for t in strat.trade_log:
                print(
                    f"    {t['entry_date']} {t['direction']:5s} {t['pattern']:14s}"
                    f" {t['entry_price']:.2f} -> {t['exit_price']:.2f}"
                    f" size={t['size']:5d} pnl={t['pnl']:9.2f} ({t['reason']})"
                )


if __name__ == "__main__":
    main()
