"""Scan PostgreSQL market universes for latest RSI trend-following signals."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from quant_trade import config as app_config
from quant_trade.bars import iter_bars
from quant_trade.charts import (
    SIGNAL_SHEET_COLUMNS,
    draw_candlesticks,
    finalize_date_axis,
    make_price_rsi_figure,
    mark_entry,
    plot_moving_averages,
    plot_rsi,
    render_signal_sheet,
    save_figure,
)
from quant_trade.market_data import (
    A_SHARE_SCAN_ON_DATE_QUERY,
    A_SHARE_SCAN_QUERY,
    US_SHARE_SCAN_ON_DATE_QUERY,
    US_SHARE_SCAN_QUERY,
    DatabaseSettings,
    DataFreshnessError,
    MarketDataStatus,
    SymbolBars,
    prepare_symbol_bars,
    read_market_data_status,
    scan_universe,
    sort_matches_by_market_cap,
    validate_market,
    validate_market_data_freshness,
)
from quant_trade.models import (
    Direction,
    Market,
    ScanBatch,
    Signal,
    SignalMatch,
)
from quant_trade.rsi50 import Rsi50Config, Rsi50SignalEngine

# Compatibility re-exports for previous private SQL names.
_A_SHARE_SCAN_QUERY = A_SHARE_SCAN_QUERY
_US_SHARE_SCAN_QUERY = US_SHARE_SCAN_QUERY
_A_SHARE_SCAN_ON_DATE_QUERY = A_SHARE_SCAN_ON_DATE_QUERY
_US_SHARE_SCAN_ON_DATE_QUERY = US_SHARE_SCAN_ON_DATE_QUERY


def select_matches_for_delivery(
    matches: list[SignalMatch],
    max_send: int,
) -> list[SignalMatch]:
    """Return the highest-ranked matches allowed for one delivery batch."""
    if max_send < 0:
        raise ValueError("max_send must not be negative")
    if max_send == 0:
        return matches.copy()
    return matches[:max_send]


def scan_database_latest(
    settings: DatabaseSettings,
    *,
    market: Market = "a",
    lookback_bars: int = app_config.SCAN_LOOKBACK_BARS,
    enforce_freshness: bool = True,
    today: date | None = None,
) -> ScanBatch[SignalMatch]:
    """Stream recent bars for one market and evaluate each latest bar."""
    batch = scan_universe(
        settings,
        market=market,
        lookback_bars=lookback_bars,
        evaluate_rows=_evaluate_rows,
        enforce_freshness=enforce_freshness,
        today=today,
        scan_date=None,
        cursor_name="rsi50_universe_scan",
    )
    return _with_overflow_ma_threshold(batch, market=market)


def scan_database_on_date(
    settings: DatabaseSettings,
    scan_date: date,
    *,
    market: Market = "a",
    lookback_bars: int = app_config.SCAN_LOOKBACK_BARS,
    enforce_freshness: bool = False,
) -> ScanBatch[SignalMatch]:
    """Stream recent bars up to one requested scan date."""
    batch = scan_universe(
        settings,
        market=market,
        lookback_bars=lookback_bars,
        evaluate_rows=_evaluate_rows,
        enforce_freshness=enforce_freshness,
        today=None,
        scan_date=scan_date,
        cursor_name="rsi50_universe_scan",
    )
    return _with_overflow_ma_threshold(batch, market=market)


def scan_symbol_frame(
    symbol: str,
    name: str,
    industry: str,
    frame: pd.DataFrame,
    *,
    market_cap_cny: float | None = None,
    circulating_market_cap_cny: float | None = None,
    market: Market = "a",
    config: Rsi50Config | None = None,
) -> SignalMatch | None:
    """Evaluate one sorted adjusted OHLCV frame on its latest bar."""
    validate_market(market)
    engine = Rsi50SignalEngine(config)
    latest_signal: Signal | None = None
    for index, bar in iter_bars(frame):
        signal = engine.on_bar(bar)
        if index == len(frame) - 1:
            latest_signal = signal
    if latest_signal is None:
        return None
    return SignalMatch(
        symbol=symbol,
        name=name,
        industry=industry,
        frame=frame,
        signal=latest_signal,
        engine=engine,
        market_cap_cny=market_cap_cny,
        circulating_market_cap_cny=circulating_market_cap_cny,
        market=market,
    )


def apply_overflow_ma_threshold(
    matches: list[SignalMatch],
    *,
    market: Market = "a",
) -> tuple[list[SignalMatch], float]:
    """Raise MA slope threshold when too many A-share symbols pass the filter.

    A shares default to ``MA_FAST_MIN_DAILY_RETURN`` (0.3%/day). When more than
    ``MA_FAST_OVERFLOW_MATCH_LIMIT`` symbols match, survivors are re-screened
    with ``MA_FAST_OVERFLOW_MIN_DAILY_RETURN`` (0.6%/day).

    US shares always keep the fixed 0.3%/day threshold.

    Returns the (possibly tightened) matches and the effective MA threshold.
    """
    default_threshold = app_config.MA_FAST_MIN_DAILY_RETURN
    if market == "us" or len(matches) <= app_config.MA_FAST_OVERFLOW_MATCH_LIMIT:
        return matches, default_threshold
    overflow_threshold = app_config.MA_FAST_OVERFLOW_MIN_DAILY_RETURN
    tightened_config = Rsi50Config(ma_fast_min_daily_return=overflow_threshold)
    tightened: list[SignalMatch] = []
    for match in matches:
        rematch = scan_symbol_frame(
            match.symbol,
            match.name,
            match.industry,
            match.frame,
            market_cap_cny=match.market_cap_cny,
            circulating_market_cap_cny=match.circulating_market_cap_cny,
            market=match.market,
            config=tightened_config,
        )
        if rematch is not None:
            tightened.append(rematch)
    return tightened, overflow_threshold


def _with_overflow_ma_threshold(
    batch: ScanBatch[SignalMatch],
    *,
    market: Market,
) -> ScanBatch[SignalMatch]:
    matches, threshold = apply_overflow_ma_threshold(
        batch.matches,
        market=market,
    )
    return ScanBatch(
        batch.scan_date,
        batch.scanned_symbols,
        batch.stale_symbols,
        matches,
        ma_min_daily_return=threshold,
    )


def render_signal_chart(
    match: SignalMatch,
    output: str | Path,
    *,
    window_bars: int = app_config.CHART_WINDOW_BARS,
) -> Path:
    """Render a PNG with candlesticks, moving averages, and RSI."""
    signal = match.signal
    engine = match.engine
    if not isinstance(engine, Rsi50SignalEngine):
        raise TypeError("SignalMatch.engine must be an Rsi50SignalEngine")
    start = max(0, len(match.frame) - window_bars)
    frame = match.frame.iloc[start:]
    figure, price_axis, rsi_axis = make_price_rsi_figure()
    x_values = draw_candlesticks(price_axis, frame)
    plot_moving_averages(
        price_axis,
        x_values,
        engine.fast_ma_values[start:],
        engine.slow_ma_values[start:],
    )

    trigger_x = len(match.frame) - 1 - start
    is_long = signal.direction is Direction.LONG
    signal_color = "#d94b55" if is_long else "#218c5b"
    mark_entry(price_axis, trigger_x, color=signal_color)
    direction = "LONG" if is_long else "SHORT"
    price_axis.set_title(
        f"{match.symbol} {match.name} | {direction} | {signal.timestamp:%Y-%m-%d}"
    )
    price_axis.set_ylabel("QFQ Price" if match.market == "a" else "Price")
    price_axis.grid(alpha=0.18)
    price_axis.legend(loc="upper left", ncols=3, fontsize=8)

    plot_rsi(rsi_axis, x_values, engine.rsi_values[start:])
    rsi_axis.scatter([trigger_x], [signal.rsi], color=signal_color, s=55, zorder=5)
    finalize_date_axis(rsi_axis, frame)
    return save_figure(figure, output)


def _evaluate_rows(
    rows: list[tuple[Any, ...]],
    scan_date: date,
    market: Market,
) -> tuple[list[SignalMatch], bool]:
    symbol_bars, stale = prepare_symbol_bars(rows, scan_date, market)
    if symbol_bars is None:
        return [], stale
    match = scan_symbol_frame(
        symbol_bars.symbol,
        symbol_bars.name,
        symbol_bars.industry,
        symbol_bars.frame,
        market_cap_cny=symbol_bars.market_cap_cny,
        circulating_market_cap_cny=symbol_bars.circulating_market_cap_cny,
        market=market,
    )
    return ([] if match is None else [match]), False


__all__ = [
    "SIGNAL_SHEET_COLUMNS",
    "DataFreshnessError",
    "DatabaseSettings",
    "Market",
    "MarketDataStatus",
    "ScanBatch",
    "SignalMatch",
    "SymbolBars",
    "prepare_symbol_bars",
    "read_market_data_status",
    "render_signal_chart",
    "render_signal_sheet",
    "scan_database_latest",
    "scan_database_on_date",
    "scan_symbol_frame",
    "select_matches_for_delivery",
    "apply_overflow_ma_threshold",
    "sort_matches_by_market_cap",
    "validate_market_data_freshness",
    "_A_SHARE_SCAN_QUERY",
    "_A_SHARE_SCAN_ON_DATE_QUERY",
    "_US_SHARE_SCAN_QUERY",
    "_US_SHARE_SCAN_ON_DATE_QUERY",
    "_evaluate_rows",
]
