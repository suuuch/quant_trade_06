"""Scan the PostgreSQL A-share universe for latest RSI 50 signals."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import matplotlib
import pandas as pd
import psycopg
from dotenv import load_dotenv

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from quant_trade.rsi50 import Bar, Direction, Rsi50SignalEngine, Signal

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


@dataclass(frozen=True)
class DatabaseSettings:
    """PostgreSQL connection settings for the market-data database."""

    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        """Load PostgreSQL settings from the project environment."""
        load_dotenv()
        return cls(
            host=_required_env("PG_HOST"),
            port=int(os.getenv("PG_PORT", "5432")),
            database=_required_env("PG_DB"),
            user=_required_env("PG_USER"),
            password=_required_env("PG_PASSWORD"),
        )


class DataFreshnessError(RuntimeError):
    """Raised when an open trading day is missing required daily data."""


@dataclass(frozen=True)
class MarketDataStatus:
    """Trading-day and source-table freshness snapshot."""

    today: date
    is_trading_day: bool
    daily_latest: date | None
    adjustment_latest: date | None


@dataclass
class SignalMatch:
    """One latest-bar signal and the data needed to review it."""

    symbol: str
    name: str
    industry: str
    frame: pd.DataFrame
    signal: Signal
    engine: Rsi50SignalEngine
    market_cap_cny: float | None = None
    circulating_market_cap_cny: float | None = None


@dataclass(frozen=True)
class ScanBatch:
    """Summary of one database-wide scan."""

    scan_date: date
    scanned_symbols: int
    stale_symbols: int
    matches: list[SignalMatch]


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
    lookback_bars: int = 240,
    enforce_freshness: bool = True,
    today: date | None = None,
) -> ScanBatch:
    """Stream recent bars for all A shares and evaluate each latest bar."""
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
        status = read_market_data_status(connection, today=today)
        if enforce_freshness:
            validate_market_data_freshness(status)
        if status.daily_latest is None:
            raise ValueError("tushare.daily contains no data")
        scan_date = status.daily_latest

        matches: list[SignalMatch] = []
        scanned_symbols = 0
        stale_symbols = 0
        with connection.cursor(name="rsi50_universe_scan") as cursor:
            cursor.itersize = 10_000
            cursor.execute(_SCAN_QUERY, (lookback_bars,))
            current_symbol = ""
            rows: list[tuple[Any, ...]] = []
            for row in cursor:
                symbol = str(row[0])
                if current_symbol and symbol != current_symbol:
                    match, stale = _evaluate_rows(rows, scan_date)
                    scanned_symbols += 1
                    stale_symbols += int(stale)
                    if match is not None:
                        matches.append(match)
                    rows = []
                current_symbol = symbol
                rows.append(tuple(row))
            if rows:
                match, stale = _evaluate_rows(rows, scan_date)
                scanned_symbols += 1
                stale_symbols += int(stale)
                if match is not None:
                    matches.append(match)

    return ScanBatch(
        scan_date,
        scanned_symbols,
        stale_symbols,
        sort_matches_by_market_cap(matches),
    )


def read_market_data_status(
    connection: psycopg.Connection[Any],
    *,
    today: date | None = None,
) -> MarketDataStatus:
    """Read trading-calendar membership and latest required table dates."""
    current_date = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM public.trading_calendar
                    WHERE trade_date = %s
                ),
                (SELECT max(trade_date) FROM tushare.daily),
                (SELECT max(trade_date) FROM tushare.adj_factor)
            """,
            (current_date,),
        )
        row = cursor.fetchone()
    if row is None:
        raise ValueError("failed to read market-data freshness status")
    return MarketDataStatus(
        today=current_date,
        is_trading_day=bool(row[0]),
        daily_latest=_parse_database_date(row[1]),
        adjustment_latest=_parse_database_date(row[2]),
    )


def validate_market_data_freshness(status: MarketDataStatus) -> None:
    """Fuse execution when today's open session lacks complete daily data."""
    if not status.is_trading_day:
        return
    missing: list[str] = []
    if status.daily_latest != status.today:
        missing.append(f"tushare.daily={_format_optional_date(status.daily_latest)}")
    if status.adjustment_latest != status.today:
        missing.append(
            f"tushare.adj_factor={_format_optional_date(status.adjustment_latest)}"
        )
    if missing:
        detail = ", ".join(missing)
        raise DataFreshnessError(
            f"market data fuse: {status.today:%Y-%m-%d} is a trading day, "
            f"but required data is missing ({detail})"
        )


def scan_symbol_frame(
    symbol: str,
    name: str,
    industry: str,
    frame: pd.DataFrame,
    *,
    market_cap_cny: float | None = None,
    circulating_market_cap_cny: float | None = None,
) -> SignalMatch | None:
    """Evaluate one sorted adjusted OHLCV frame on its latest bar."""
    engine = Rsi50SignalEngine()
    latest_signal: Signal | None = None
    for index, (timestamp, row) in enumerate(frame.iterrows()):
        signal = engine.on_bar(
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
            latest_signal = signal
    if latest_signal is None:
        return None
    return SignalMatch(
        symbol,
        name,
        industry,
        frame,
        latest_signal,
        engine,
        market_cap_cny,
        circulating_market_cap_cny,
    )


def sort_matches_by_market_cap(matches: list[SignalMatch]) -> list[SignalMatch]:
    """Sort signals by total market cap descending, with missing values last."""
    return sorted(
        matches,
        key=lambda match: (
            match.market_cap_cny is not None,
            match.market_cap_cny or 0.0,
        ),
        reverse=True,
    )


def render_signal_chart(
    match: SignalMatch,
    output: str | Path,
    *,
    window_bars: int = 100,
) -> Path:
    """Render a PNG with candlesticks, MAs, pivots, neckline, and RSI."""
    signal = match.signal
    start = max(0, min(signal.first_pivot_index - 5, len(match.frame) - window_bars))
    frame = match.frame.iloc[start:]
    dates = [timestamp.strftime("%Y-%m-%d") for timestamp in frame.index]
    x_values = list(range(len(frame)))

    figure, (price_axis, rsi_axis) = plt.subplots(
        2,
        1,
        figsize=(12, 8),
        height_ratios=(3, 1),
        sharex=True,
        constrained_layout=True,
    )
    for x_value, (_, row) in zip(x_values, frame.iterrows(), strict=True):
        rising = float(row["close"]) >= float(row["open"])
        color = "#d94b55" if rising else "#218c5b"
        price_axis.vlines(x_value, row["low"], row["high"], color=color, linewidth=1)
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

    fast_ma = match.engine.fast_ma_values[start:]
    slow_ma = match.engine.slow_ma_values[start:]
    rsi_values = match.engine.rsi_values[start:]
    price_axis.plot(x_values, fast_ma, color="#d18b1f", linewidth=1.2, label="MA20")
    price_axis.plot(x_values, slow_ma, color="#2f66d0", linewidth=1.2, label="MA30")

    first_x = signal.first_pivot_index - start
    second_x = signal.second_pivot_index - start
    trigger_x = len(match.frame) - 1 - start
    is_long = signal.direction is Direction.LONG
    pivot_prices = (
        [
            match.frame.iloc[signal.first_pivot_index]["low"],
            match.frame.iloc[signal.second_pivot_index]["low"],
        ]
        if is_long
        else [
            match.frame.iloc[signal.first_pivot_index]["high"],
            match.frame.iloc[signal.second_pivot_index]["high"],
        ]
    )
    signal_color = "#d94b55" if is_long else "#218c5b"
    price_axis.scatter(
        [first_x, second_x],
        pivot_prices,
        color=signal_color,
        s=45,
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
        [signal.close],
        marker="^" if is_long else "v",
        color=signal_color,
        edgecolor="white",
        s=110,
        zorder=6,
        label="Signal",
    )
    price_axis.annotate(
        "P1", (first_x, pivot_prices[0]), xytext=(0, 10), textcoords="offset points"
    )
    price_axis.annotate(
        "P2", (second_x, pivot_prices[1]), xytext=(0, 10), textcoords="offset points"
    )
    direction = "LONG W-BOTTOM" if is_long else "SHORT M-TOP"
    price_axis.set_title(
        f"{match.symbol} {match.name} | {direction} | {signal.timestamp:%Y-%m-%d}"
    )
    price_axis.set_ylabel("QFQ Price")
    price_axis.grid(alpha=0.18)
    price_axis.legend(loc="upper left", ncols=4, fontsize=8)

    rsi_axis.plot(x_values, rsi_values, color="#7a4cc2", linewidth=1.2)
    for level in (45, 50, 55):
        rsi_axis.axhline(level, color="#888888", linestyle=":", linewidth=0.8)
    rsi_axis.scatter([trigger_x], [signal.rsi], color=signal_color, s=55, zorder=5)
    rsi_axis.set_ylim(0, 100)
    rsi_axis.set_ylabel("RSI(14)")
    rsi_axis.grid(alpha=0.18)

    step = max(1, len(dates) // 10)
    ticks = list(range(0, len(dates), step))
    rsi_axis.set_xticks(
        ticks, [dates[index] for index in ticks], rotation=30, ha="right"
    )
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=150)
    plt.close(figure)
    return destination


def render_signal_sheet(
    image_paths: list[Path],
    output: str | Path,
) -> Path:
    """Combine individual signal charts into one QQ delivery image."""
    if not image_paths:
        raise ValueError("image_paths must not be empty")
    columns = min(2, len(image_paths))
    rows = math.ceil(len(image_paths) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(12 * columns, 8 * rows),
        squeeze=False,
    )
    for axis, image_path in zip(axes.flat, image_paths, strict=False):
        axis.imshow(plt.imread(image_path))
        axis.axis("off")
    for axis in axes.flat[len(image_paths) :]:
        axis.axis("off")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.subplots_adjust(left=0, right=1, top=1, bottom=0, wspace=0, hspace=0)
    figure.savefig(destination, dpi=150)
    plt.close(figure)
    return destination


def _evaluate_rows(
    rows: list[tuple[Any, ...]],
    scan_date: date,
) -> tuple[SignalMatch | None, bool]:
    frame = pd.DataFrame(rows, columns=_SCAN_COLUMNS)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    stale = frame["trade_date"].iloc[-1].date() != scan_date
    if stale:
        return None, True
    adjustment = frame["adj_factor"] / frame["latest_adj_factor"]
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[f"{column}_raw"] * adjustment
    frame["volume"] = frame["volume_lots"] * 100.0
    symbol = str(frame["symbol"].iloc[0])
    name = str(frame["name"].iloc[0])
    industry = str(frame["industry"].iloc[0] or "")
    total_mv = frame["total_mv"].iloc[-1]
    circ_mv = frame["circ_mv"].iloc[-1]
    market_cap_cny = None if pd.isna(total_mv) else float(total_mv) * 10_000.0
    circulating_market_cap_cny = None if pd.isna(circ_mv) else float(circ_mv) * 10_000.0
    bars = frame.set_index("trade_date")[["open", "high", "low", "close", "volume"]]
    return (
        scan_symbol_frame(
            symbol,
            name,
            industry,
            bars,
            market_cap_cny=market_cap_cny,
            circulating_market_cap_cny=circulating_market_cap_cny,
        ),
        False,
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def _parse_database_date(value: object) -> date | None:
    if value is None:
        return None
    return pd.to_datetime(str(value), format="%Y%m%d").date()


def _format_optional_date(value: date | None) -> str:
    return "missing" if value is None else value.isoformat()


_SCAN_COLUMNS = [
    "symbol",
    "trade_date",
    "name",
    "industry",
    "open_raw",
    "high_raw",
    "low_raw",
    "close_raw",
    "volume_lots",
    "adj_factor",
    "latest_adj_factor",
    "total_mv",
    "circ_mv",
]

_SCAN_QUERY = """
    WITH ranked AS (
        SELECT
            d.ts_code AS symbol,
            d.trade_date,
            b.name,
            b.industry,
            d.open AS open_raw,
            d.high AS high_raw,
            d.low AS low_raw,
            d.close AS close_raw,
            d.vol AS volume_lots,
            a.adj_factor,
            db.total_mv,
            db.circ_mv,
            first_value(a.adj_factor) OVER (
                PARTITION BY d.ts_code ORDER BY d.trade_date DESC
            ) AS latest_adj_factor,
            row_number() OVER (
                PARTITION BY d.ts_code ORDER BY d.trade_date DESC
            ) AS recent_rank
        FROM tushare.daily AS d
        JOIN tushare.adj_factor AS a
          ON a.ts_code = d.ts_code
         AND a.trade_date = d.trade_date
        JOIN public.stock_basic AS b ON b.ts_code = d.ts_code
        LEFT JOIN tushare.daily_basic AS db
          ON db.ts_code = d.ts_code
         AND db.trade_date = d.trade_date
    )
    SELECT
        symbol,
        trade_date,
        name,
        industry,
        open_raw,
        high_raw,
        low_raw,
        close_raw,
        volume_lots,
        adj_factor,
        latest_adj_factor,
        total_mv,
        circ_mv
    FROM ranked
    WHERE recent_rank <= %s
    ORDER BY symbol, trade_date
"""
