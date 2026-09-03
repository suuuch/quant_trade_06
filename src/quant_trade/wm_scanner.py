"""Database scanning and chart rendering for independent W/M entries."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from quant_trade import config as app_config
from quant_trade.bars import iter_bars
from quant_trade.charts import (
    draw_candlesticks,
    finalize_date_axis,
    make_price_rsi_figure,
    mark_entry,
    plot_moving_averages,
    plot_rsi,
    save_figure,
)
from quant_trade.market_data import (
    DatabaseSettings,
    prepare_symbol_bars,
    scan_universe,
)
from quant_trade.models import (
    Direction,
    Market,
    WmScanBatch,
    WmSignal,
    WmSignalMatch,
)
from quant_trade.rsi50 import Rsi50SignalEngine
from quant_trade.wm_pattern import WmPatternEngine


def scan_wm_database_latest(
    settings: DatabaseSettings,
    *,
    market: Market = "a",
    lookback_bars: int = app_config.SCAN_LOOKBACK_BARS,
    enforce_freshness: bool = True,
) -> WmScanBatch:
    """Scan one market universe for latest-bar W/M neckline entries."""
    return scan_universe(
        settings,
        market=market,
        lookback_bars=lookback_bars,
        evaluate_rows=_evaluate_rows,
        enforce_freshness=enforce_freshness,
        cursor_name="wm_universe_scan",
    )


def scan_wm_symbol_frame(
    symbol: str,
    name: str,
    industry: str,
    frame: pd.DataFrame,
    *,
    market_cap_cny: float | None = None,
    market: Market = "a",
) -> list[WmSignalMatch]:
    """Return latest-bar W/M entries for one sorted OHLCV frame."""
    engine = WmPatternEngine()
    latest_signals: tuple[WmSignal, ...] = ()
    for index, bar in iter_bars(frame):
        signals = engine.on_bar(bar)
        if index == len(frame) - 1:
            latest_signals = signals
    return [
        WmSignalMatch(
            symbol=symbol,
            name=name,
            industry=industry,
            frame=frame,
            signal=signal,
            engine=engine,
            market_cap_cny=market_cap_cny,
            market=market,
        )
        for signal in latest_signals
    ]


def render_wm_signal_chart(
    match: WmSignalMatch,
    output: str | Path,
    *,
    window_bars: int = app_config.CHART_WINDOW_BARS,
) -> Path:
    """Render a W/M chart with entry markers and observation indicators."""
    signal = match.signal
    start = max(0, min(signal.first_pivot_index - 5, len(match.frame) - window_bars))
    frame = match.frame.iloc[start:]
    figure, price_axis, rsi_axis = make_price_rsi_figure()
    x_values = draw_candlesticks(price_axis, frame)
    indicator_engine = _indicator_engine(match.frame)
    plot_moving_averages(
        price_axis,
        x_values,
        indicator_engine.fast_ma_values[start:],
        indicator_engine.slow_ma_values[start:],
    )
    first_x = signal.first_pivot_index - start
    second_x = signal.second_pivot_index - start
    trigger_x = len(match.frame) - 1 - start
    is_w_bottom = signal.direction is Direction.LONG
    price_column = "low" if is_w_bottom else "high"
    pivot_prices = [
        match.frame.iloc[signal.first_pivot_index][price_column],
        match.frame.iloc[signal.second_pivot_index][price_column],
    ]
    signal_color = "#d94b55" if is_w_bottom else "#218c5b"
    price_axis.scatter(
        [first_x, second_x],
        pivot_prices,
        color=signal_color,
        s=50,
        zorder=5,
        label="Pivots",
    )
    price_axis.hlines(
        signal.neckline,
        first_x,
        trigger_x,
        color="#777777",
        linestyle="--",
        linewidth=1.2,
        label="Neckline",
    )
    mark_entry(price_axis, trigger_x, color=signal_color)
    price_axis.annotate(
        "P1", (first_x, pivot_prices[0]), xytext=(0, 10), textcoords="offset points"
    )
    price_axis.annotate(
        "P2", (second_x, pivot_prices[1]), xytext=(0, 10), textcoords="offset points"
    )
    pattern = "W-BOTTOM ENTRY" if is_w_bottom else "M-TOP ENTRY"
    price_axis.set_title(
        f"{match.symbol} {match.name} | {pattern} | {signal.timestamp:%Y-%m-%d}"
    )
    price_axis.set_ylabel("QFQ Price" if match.market == "a" else "Price")
    price_axis.set_yscale("log")
    price_axis.grid(alpha=0.18)
    price_axis.legend(loc="upper left", ncols=5, fontsize=8)

    plot_rsi(
        rsi_axis,
        x_values,
        indicator_engine.rsi_values[start:],
        levels=(45, 50, 55, 58),
        label="RSI(14) · 仅观察",
    )
    rsi_axis.legend(loc="upper left", fontsize=8)
    finalize_date_axis(rsi_axis, frame)
    return save_figure(figure, output)


def _indicator_engine(frame: pd.DataFrame) -> Rsi50SignalEngine:
    """Calculate MA20, MA30, and RSI solely for chart observation."""
    engine = Rsi50SignalEngine()
    for _, bar in iter_bars(frame):
        engine.on_bar(bar)
    return engine


def _evaluate_rows(
    rows: list[tuple[Any, ...]],
    scan_date: date,
    market: Market,
) -> tuple[list[WmSignalMatch], bool]:
    symbol_bars, stale = prepare_symbol_bars(rows, scan_date, market)
    if symbol_bars is None:
        return [], stale
    matches = scan_wm_symbol_frame(
        symbol_bars.symbol,
        symbol_bars.name,
        symbol_bars.industry,
        symbol_bars.frame,
        market_cap_cny=symbol_bars.market_cap_cny,
        market=market,
    )
    return matches, False
