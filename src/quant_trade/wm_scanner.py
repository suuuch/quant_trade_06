"""Database scanning and chart rendering for independent W/M entries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import matplotlib
import pandas as pd
import psycopg

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from quant_trade.rsi50 import Bar, Direction, Rsi50SignalEngine
from quant_trade.scanner import (
    _A_SHARE_SCAN_QUERY,
    _US_SHARE_SCAN_QUERY,
    DatabaseSettings,
    Market,
    prepare_symbol_bars,
    read_market_data_status,
    validate_market_data_freshness,
)
from quant_trade.wm_pattern import WmPatternEngine, WmSignal

plt.rcParams["font.sans-serif"] = [
    "Noto Sans CJK SC",
    "WenQuanYi Zen Hei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


@dataclass
class WmSignalMatch:
    """One latest W/M entry and its chart data."""

    symbol: str
    name: str
    industry: str
    frame: pd.DataFrame
    signal: WmSignal
    engine: WmPatternEngine
    market_cap_cny: float | None = None
    market: Market = "a"


@dataclass(frozen=True)
class WmScanBatch:
    """Summary of one database-wide W/M scan."""

    scan_date: date
    scanned_symbols: int
    stale_symbols: int
    matches: list[WmSignalMatch]


def scan_wm_database_latest(
    settings: DatabaseSettings,
    *,
    market: Market = "a",
    lookback_bars: int = 240,
    enforce_freshness: bool = True,
) -> WmScanBatch:
    """Scan one market universe for latest-bar W/M neckline entries."""
    if market not in ("a", "us"):
        raise ValueError("market must be 'a' or 'us'")
    if lookback_bars < 90:
        raise ValueError("lookback_bars must be at least 90")
    connection_kwargs = {
        "host": settings.host,
        "port": settings.port,
        "dbname": settings.database,
        "user": settings.user,
        "password": settings.password,
        "connect_timeout": 10,
    }
    with psycopg.connect(**connection_kwargs) as connection:
        status = read_market_data_status(connection, market=market)
        if enforce_freshness:
            validate_market_data_freshness(status)
        if status.daily_latest is None:
            source = "tushare.daily" if market == "a" else "public.stock_klines"
            raise ValueError(f"{source} contains no data")
        scan_date = status.daily_latest
        matches: list[WmSignalMatch] = []
        scanned_symbols = 0
        stale_symbols = 0
        with connection.cursor(name="wm_universe_scan") as cursor:
            cursor.itersize = 10_000
            query = _A_SHARE_SCAN_QUERY if market == "a" else _US_SHARE_SCAN_QUERY
            cursor.execute(query, (lookback_bars,))
            current_symbol = ""
            rows: list[tuple[Any, ...]] = []
            for row in cursor:
                symbol = str(row[0])
                if current_symbol and symbol != current_symbol:
                    found, stale = _evaluate_rows(rows, scan_date, market)
                    scanned_symbols += 1
                    stale_symbols += int(stale)
                    matches.extend(found)
                    rows = []
                current_symbol = symbol
                rows.append(tuple(row))
            if rows:
                found, stale = _evaluate_rows(rows, scan_date, market)
                scanned_symbols += 1
                stale_symbols += int(stale)
                matches.extend(found)
    matches.sort(
        key=lambda match: (
            match.market_cap_cny is not None,
            match.market_cap_cny or 0.0,
        ),
        reverse=True,
    )
    return WmScanBatch(scan_date, scanned_symbols, stale_symbols, matches)


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
    for index, (timestamp, row) in enumerate(frame.iterrows()):
        signals = engine.on_bar(
            Bar(
                timestamp=cast(pd.Timestamp, timestamp).to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )
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
    window_bars: int = 100,
) -> Path:
    """Render a W/M chart with entry markers and observation indicators."""
    signal = match.signal
    start = max(0, min(signal.first_pivot_index - 5, len(match.frame) - window_bars))
    frame = match.frame.iloc[start:]
    x_values = list(range(len(frame)))
    dates = [timestamp.strftime("%Y-%m-%d") for timestamp in frame.index]
    figure, (price_axis, rsi_axis) = plt.subplots(
        2,
        1,
        figsize=(9, 6),
        height_ratios=(3, 1),
        sharex=True,
        constrained_layout=True,
    )
    for x_value, (_, row) in zip(x_values, frame.iterrows(), strict=True):
        rising = float(row["close"]) >= float(row["open"])
        color = "#d94b55" if rising else "#218c5b"
        price_axis.vlines(
            x_value,
            row["low"],
            row["high"],
            color=color,
            linewidth=1,
        )
        body_low = min(float(row["open"]), float(row["close"]))
        body_height = max(abs(float(row["close"]) - float(row["open"])), 0.001)
        price_axis.add_patch(
            Rectangle(
                (x_value - 0.32, body_low),
                0.64,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
            )
        )
    indicator_engine = _indicator_engine(match.frame)
    price_axis.plot(
        x_values,
        indicator_engine.fast_ma_values[start:],
        color="#d18b1f",
        linewidth=1.2,
        label="MA20",
    )
    price_axis.plot(
        x_values,
        indicator_engine.slow_ma_values[start:],
        color="#2f66d0",
        linewidth=1.2,
        label="MA30",
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
    trigger_bar = match.frame.iloc[-1]
    entry_marker_price = _entry_marker_price(
        low=float(trigger_bar["low"]),
        high=float(trigger_bar["high"]),
        direction=signal.direction,
    )
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
    price_axis.scatter(
        [trigger_x],
        [entry_marker_price],
        marker="^" if is_w_bottom else "v",
        color=signal_color,
        edgecolor="white",
        s=110,
        zorder=6,
        label="Entry",
    )
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

    rsi_axis.plot(
        x_values,
        indicator_engine.rsi_values[start:],
        color="#7a4cc2",
        linewidth=1.2,
        label="RSI(14) · 仅观察",
    )
    for level in (45, 50, 55, 58):
        rsi_axis.axhline(level, color="#888888", linestyle=":", linewidth=0.8)
    rsi_axis.set_ylim(0, 100)
    rsi_axis.set_ylabel("RSI(14)")
    rsi_axis.grid(alpha=0.18)
    rsi_axis.legend(loc="upper left", fontsize=8)
    step = max(1, len(dates) // 10)
    ticks = list(range(0, len(dates), step))
    rsi_axis.set_xticks(
        ticks,
        [dates[index] for index in ticks],
        rotation=30,
        ha="right",
    )
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=100)
    plt.close(figure)
    return destination


def _entry_marker_price(*, low: float, high: float, direction: Direction) -> float:
    """Place an entry marker 2% beyond the signal candle's high or low."""
    if direction is Direction.LONG:
        return low * 0.98
    return high * 1.02


def _indicator_engine(frame: pd.DataFrame) -> Rsi50SignalEngine:
    """Calculate MA20, MA30, and RSI solely for chart observation."""
    engine = Rsi50SignalEngine()
    for timestamp, row in frame.iterrows():
        engine.on_bar(
            Bar(
                timestamp=cast(pd.Timestamp, timestamp).to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )
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
