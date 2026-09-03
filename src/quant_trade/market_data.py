"""PostgreSQL market-data access and shared universe scanning."""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg
from dotenv import load_dotenv

from quant_trade.models import Market, ScanBatch

logger = logging.getLogger(__name__)

TMatch = TypeVar("TMatch")


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


@dataclass(frozen=True)
class SymbolBars:
    """Normalized bars and metadata for one database symbol."""

    symbol: str
    name: str
    industry: str
    frame: pd.DataFrame
    market_cap_cny: float | None
    circulating_market_cap_cny: float | None


def validate_market(market: str) -> None:
    """Raise when market is not a supported literal."""
    if market not in ("a", "us"):
        raise ValueError("market must be 'a' or 'us'")


def sort_matches_by_market_cap(matches: list[TMatch]) -> list[TMatch]:
    """Sort matches by total market cap descending, missing values last."""
    return sorted(
        matches,
        key=lambda match: (
            getattr(match, "market_cap_cny", None) is not None,
            getattr(match, "market_cap_cny", None) or 0.0,
        ),
        reverse=True,
    )


def scan_universe(
    settings: DatabaseSettings,
    *,
    market: Market,
    lookback_bars: int,
    evaluate_rows: Callable[
        [list[tuple[Any, ...]], date, Market],
        tuple[Sequence[TMatch], bool],
    ],
    enforce_freshness: bool = True,
    today: date | None = None,
    scan_date: date | None = None,
    cursor_name: str = "universe_scan",
) -> ScanBatch[TMatch]:
    """Stream recent bars for one market and evaluate each symbol group."""
    validate_market(market)
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
        if scan_date is None and status.daily_latest is None:
            source = "tushare.daily" if market == "a" else "public.stock_klines"
            raise ValueError(f"{source} contains no data")
        target_scan_date = scan_date or status.daily_latest
        if target_scan_date is None:
            source = "tushare.daily" if market == "a" else "public.stock_klines"
            raise ValueError(f"{source} contains no data")

        matches: list[TMatch] = []
        scanned_symbols = 0
        stale_symbols = 0
        with connection.cursor(name=cursor_name) as cursor:
            cursor.itersize = 10_000
            _execute_scan_query(cursor, market, lookback_bars, scan_date)
            current_symbol = ""
            rows: list[tuple[Any, ...]] = []
            for row in cursor:
                symbol = str(row[0])
                if current_symbol and symbol != current_symbol:
                    found, stale = evaluate_rows(rows, target_scan_date, market)
                    scanned_symbols += 1
                    stale_symbols += int(stale)
                    matches.extend(found)
                    rows = []
                current_symbol = symbol
                rows.append(tuple(row))
            if rows:
                found, stale = evaluate_rows(rows, target_scan_date, market)
                scanned_symbols += 1
                stale_symbols += int(stale)
                matches.extend(found)

    return ScanBatch(
        target_scan_date,
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
    validate_market(market)
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


def _execute_scan_query(
    cursor: psycopg.Cursor[Any],
    market: Market,
    lookback_bars: int,
    scan_date: date | None,
) -> None:
    if scan_date is None:
        query = A_SHARE_SCAN_QUERY if market == "a" else US_SHARE_SCAN_QUERY
        cursor.execute(query, (lookback_bars,))
        return
    query = A_SHARE_SCAN_ON_DATE_QUERY if market == "a" else US_SHARE_SCAN_ON_DATE_QUERY
    cutoff_date: date | str = (
        scan_date.strftime("%Y%m%d") if market == "a" else scan_date
    )
    cursor.execute(query, (cutoff_date, lookback_bars))


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


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

A_SHARE_SCAN_QUERY = """
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

A_SHARE_SCAN_ON_DATE_QUERY = """
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
        WHERE d.trade_date <= %s
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

US_SHARE_SCAN_QUERY = """
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
           AND avg(close_raw * volume_lots) > 50000000.0
           AND percentile_cont(0.5) WITHIN GROUP (
               ORDER BY close_raw * volume_lots
           ) > 50000000.0
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

US_SHARE_SCAN_ON_DATE_QUERY = """
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
          AND k.kline_date <= %s
          AND p.market_cap > 1000000000
    ),
    eligible AS (
        SELECT symbol
        FROM ranked
        WHERE recent_rank <= 50
        GROUP BY symbol
        HAVING count(close_raw * volume_lots) = 50
           AND max(close_raw) FILTER (WHERE recent_rank = 1) > 5.0
           AND avg(close_raw * volume_lots) > 50000000.0
           AND percentile_cont(0.5) WITHIN GROUP (
               ORDER BY close_raw * volume_lots
           ) > 50000000.0
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

# Compatibility aliases for previous private names.
_A_SHARE_SCAN_QUERY = A_SHARE_SCAN_QUERY
_US_SHARE_SCAN_QUERY = US_SHARE_SCAN_QUERY
_A_SHARE_SCAN_ON_DATE_QUERY = A_SHARE_SCAN_ON_DATE_QUERY
_US_SHARE_SCAN_ON_DATE_QUERY = US_SHARE_SCAN_ON_DATE_QUERY
