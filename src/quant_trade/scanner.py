"""Scan PostgreSQL market universes for latest RSI 50 signals."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import matplotlib
import pandas as pd
import psycopg
from dotenv import load_dotenv

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from quant_trade.rsi50 import Bar, Direction, Rsi50Config, Rsi50SignalEngine, Signal

Market = Literal["a", "us"]
logger = logging.getLogger(__name__)

plt.rcParams["font.sans-serif"] = [
    "Noto Sans CJK SC",
    "WenQuanYi Zen Hei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
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
    market: Market = "a"


@dataclass(frozen=True)
class ScanBatch:
    """Summary of one database-wide scan."""

    scan_date: date
    scanned_symbols: int
    stale_symbols: int
    matches: list[SignalMatch]


@dataclass(frozen=True)
class SymbolBars:
    """Normalized bars and metadata for one database symbol."""

    symbol: str
    name: str
    industry: str
    frame: pd.DataFrame
    market_cap_cny: float | None
    circulating_market_cap_cny: float | None


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
    lookback_bars: int = 240,
    enforce_freshness: bool = True,
    today: date | None = None,
) -> ScanBatch:
    """Stream recent bars for one market and evaluate each latest bar."""
    _validate_market(market)
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
        status = read_market_data_status(connection, market=market, today=today)
        if enforce_freshness:
            validate_market_data_freshness(status)
        if status.daily_latest is None:
            source = "tushare.daily" if market == "a" else "public.stock_klines"
            raise ValueError(f"{source} contains no data")
        scan_date = status.daily_latest

        matches: list[SignalMatch] = []
        scanned_symbols = 0
        stale_symbols = 0
        with connection.cursor(name="rsi50_universe_scan") as cursor:
            cursor.itersize = 10_000
            query = _A_SHARE_SCAN_QUERY if market == "a" else _US_SHARE_SCAN_QUERY
            cursor.execute(query, (lookback_bars,))
            current_symbol = ""
            rows: list[tuple[Any, ...]] = []
            for row in cursor:
                symbol = str(row[0])
                if current_symbol and symbol != current_symbol:
                    match, stale = _evaluate_rows(rows, scan_date, market)
                    scanned_symbols += 1
                    stale_symbols += int(stale)
                    if match is not None:
                        matches.append(match)
                    rows = []
                current_symbol = symbol
                rows.append(tuple(row))
            if rows:
                match, stale = _evaluate_rows(rows, scan_date, market)
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
    market: Market = "a",
    today: date | None = None,
) -> MarketDataStatus:
    """Read trading-calendar membership and latest required table dates."""
    _validate_market(market)
    current_date = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with connection.cursor() as cursor:
        if market == "a":
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
        else:
            cursor.execute(
                """
                SELECT
                    false,
                    max(kline_date),
                    max(kline_date)
                FROM public.stock_klines
                WHERE code LIKE 'US.%%'
                """
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
    market: Market = "a",
) -> SignalMatch | None:
    """Evaluate one sorted adjusted OHLCV frame on its latest bar."""
    _validate_market(market)
    config = Rsi50Config(ma_fast_min_angle_degrees=None) if market == "us" else None
    engine = Rsi50SignalEngine(config)
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
        market,
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
    """Render a PNG with candlesticks, moving averages, and RSI."""
    signal = match.signal
    start = max(0, len(match.frame) - window_bars)
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

    trigger_x = len(match.frame) - 1 - start
    is_long = signal.direction is Direction.LONG
    signal_color = "#d94b55" if is_long else "#218c5b"
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
    direction = "LONG" if is_long else "SHORT"
    price_axis.set_title(
        f"{match.symbol} {match.name} | {direction} | {signal.timestamp:%Y-%m-%d}"
    )
    price_axis.set_ylabel("QFQ Price" if match.market == "a" else "Price")
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
    market: Market,
) -> tuple[SignalMatch | None, bool]:
    symbol_bars, stale = prepare_symbol_bars(rows, scan_date, market)
    if symbol_bars is None:
        return None, stale
    match = scan_symbol_frame(
        symbol_bars.symbol,
        symbol_bars.name,
        symbol_bars.industry,
        symbol_bars.frame,
        market_cap_cny=symbol_bars.market_cap_cny,
        circulating_market_cap_cny=symbol_bars.circulating_market_cap_cny,
        market=market,
    )
    return match, False


def prepare_symbol_bars(
    rows: list[tuple[Any, ...]],
    scan_date: date,
    market: Market,
) -> tuple[SymbolBars | None, bool]:
    """Normalize one symbol's database rows for any signal strategy."""
    frame = pd.DataFrame(rows, columns=_SCAN_COLUMNS)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    stale = frame["trade_date"].iloc[-1].date() != scan_date
    if stale:
        return None, True
    adjustment = frame["adj_factor"].astype(float) / frame["latest_adj_factor"].astype(
        float
    )
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[f"{column}_raw"] * adjustment
    frame["volume"] = frame["volume_lots"] * (100.0 if market == "a" else 1.0)
    symbol = str(frame["symbol"].iloc[0])
    prices = frame[["open", "high", "low", "close"]]
    valid_prices = prices.notna().all(axis=1)
    valid_prices &= prices.abs().lt(math.inf).all(axis=1)
    valid_prices &= prices.gt(0.0).all(axis=1)
    valid_prices &= frame["high"].ge(prices.max(axis=1))
    valid_prices &= frame["low"].le(prices.min(axis=1))
    invalid_dates = frame.loc[~valid_prices, "trade_date"]
    if not invalid_dates.empty:
        dates = ", ".join(timestamp.strftime("%Y-%m-%d") for timestamp in invalid_dates)
        logger.warning(
            "skipping %d invalid OHLC bar(s) for %s: %s",
            len(invalid_dates),
            symbol,
            dates,
        )
    frame = frame.loc[valid_prices]
    if frame.empty or frame["trade_date"].iloc[-1].date() != scan_date:
        return None, True
    name = str(frame["name"].iloc[0])
    industry = str(frame["industry"].iloc[0] or "")
    total_mv = frame["total_mv"].iloc[-1]
    circ_mv = frame["circ_mv"].iloc[-1]
    market_cap_cny = None if pd.isna(total_mv) else float(total_mv) * 10_000.0
    circulating_market_cap_cny = None if pd.isna(circ_mv) else float(circ_mv) * 10_000.0
    bars = frame.set_index("trade_date")[["open", "high", "low", "close", "volume"]]
    return (
        SymbolBars(
            symbol=symbol,
            name=name,
            industry=industry,
            frame=bars,
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


def _validate_market(market: str) -> None:
    if market not in ("a", "us"):
        raise ValueError("market must be 'a' or 'us'")


def _parse_database_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return pd.to_datetime(str(value)).date()


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

_A_SHARE_SCAN_QUERY = """
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

_US_SHARE_SCAN_QUERY = """
    WITH ranked AS (
        SELECT
            k.code AS symbol,
            k.kline_date AS trade_date,
            COALESCE(p.long_name, k.code) AS name,
            COALESCE(p.industry, '') AS industry,
            k.open AS open_raw,
            k.high AS high_raw,
            k.low AS low_raw,
            k.close AS close_raw,
            k.volume AS volume_lots,
            1.0 AS adj_factor,
            p.market_cap / 10000.0 AS total_mv,
            NULL::double precision AS circ_mv,
            1.0 AS latest_adj_factor,
            row_number() OVER (
                PARTITION BY k.code ORDER BY k.kline_date DESC
            ) AS recent_rank
        FROM public.stock_klines AS k
        JOIN public.stock_profiles AS p ON p.code = k.code
        WHERE k.code LIKE 'US.%%'
          AND p.market_cap > 1000000000
    ),
    eligible AS (
        SELECT symbol
        FROM ranked
        WHERE recent_rank <= 50
        GROUP BY symbol
        HAVING count(close_raw * volume_lots) = 50
           AND max(close_raw) FILTER (WHERE recent_rank = 1) > 5.0
           AND avg(close_raw * volume_lots) > 10000000.0
           AND percentile_cont(0.5) WITHIN GROUP (
               ORDER BY close_raw * volume_lots
           ) > 10000000.0
    )
    SELECT
        p.symbol,
        p.trade_date,
        p.name,
        p.industry,
        p.open_raw,
        p.high_raw,
        p.low_raw,
        p.close_raw,
        p.volume_lots,
        p.adj_factor,
        p.latest_adj_factor,
        p.total_mv,
        p.circ_mv
    FROM ranked AS p
    JOIN eligible AS e ON e.symbol = p.symbol
    WHERE p.recent_rank <= %s
    ORDER BY p.symbol, p.trade_date
"""
