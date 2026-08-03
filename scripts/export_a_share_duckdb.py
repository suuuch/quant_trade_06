"""Export selected A-share daily bars from PostgreSQL to DuckDB."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

import argparse
import os
from pathlib import Path

import duckdb
import pandas as pd
import psycopg
from dotenv import load_dotenv

DEFAULT_CODES = (
    "000001.SZ",
    "000858.SZ",
    "600036.SH",
    "600519.SH",
    "601318.SH",
)
DEFAULT_OUTPUT = Path("data/a_share_backtest.duckdb")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    return parser.parse_args()


def read_daily_bars(codes: list[str]) -> pd.DataFrame:
    """Read raw daily bars and adjustment factors for selected symbols."""
    load_dotenv()
    connection_kwargs = {
        "host": _required_env("PG_HOST"),
        "port": int(os.getenv("PG_PORT", "5432")),
        "dbname": _required_env("PG_DB"),
        "user": _required_env("PG_USER"),
        "password": _required_env("PG_PASSWORD"),
        "connect_timeout": 10,
    }
    query = """
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
            d.amount AS amount_thousand_cny,
            d.pct_chg,
            a.adj_factor
        FROM tushare.daily AS d
        JOIN public.stock_basic AS b ON b.ts_code = d.ts_code
        JOIN tushare.adj_factor AS a
          ON a.ts_code = d.ts_code
         AND a.trade_date = d.trade_date
        WHERE d.ts_code = ANY(%s)
        ORDER BY d.ts_code, d.trade_date
    """
    with psycopg.connect(**connection_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (codes,))
            rows = cursor.fetchall()
            columns = [column.name for column in cursor.description or ()]
    if not rows:
        raise ValueError("no A-share daily bars matched the requested codes")
    return pd.DataFrame(rows, columns=columns)


def prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Add forward-adjusted prices and normalized volume/amount columns."""
    prepared = frame.copy()
    prepared["trade_date"] = pd.to_datetime(prepared["trade_date"], format="%Y%m%d")
    latest_factor = prepared.groupby("symbol")["adj_factor"].transform("last")
    adjustment = prepared["adj_factor"] / latest_factor
    for column in ("open", "high", "low", "close"):
        prepared[column] = prepared[f"{column}_raw"] * adjustment
    prepared["volume"] = prepared["volume_lots"] * 100.0
    prepared["amount_cny"] = prepared["amount_thousand_cny"] * 1_000.0
    return prepared


def write_duckdb(frame: pd.DataFrame, output: Path) -> None:
    """Replace the local DuckDB bars and universe tables."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(output)) as connection:
        connection.register("export_frame", frame)
        connection.execute(
            """
            CREATE OR REPLACE TABLE a_share_daily AS
            SELECT
                symbol,
                CAST(trade_date AS DATE) AS trade_date,
                name,
                industry,
                open,
                high,
                low,
                close,
                volume,
                amount_cny,
                open_raw,
                high_raw,
                low_raw,
                close_raw,
                volume_lots,
                amount_thousand_cny,
                pct_chg,
                adj_factor
            FROM export_frame
            ORDER BY symbol, trade_date
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE universe AS
            SELECT
                symbol,
                first(name) AS name,
                first(industry) AS industry,
                min(trade_date) AS first_date,
                max(trade_date) AS last_date,
                count(*) AS bar_count,
                'tushare.daily' AS source
            FROM a_share_daily
            GROUP BY symbol
            ORDER BY symbol
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE export_metadata AS
            SELECT
                current_timestamp AS exported_at,
                count(*) AS total_bars,
                count(DISTINCT symbol) AS symbol_count,
                min(trade_date) AS first_date,
                max(trade_date) AS last_date,
                'qfq_latest' AS price_adjustment
            FROM a_share_daily
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS a_share_daily_symbol_date
            ON a_share_daily (symbol, trade_date)
            """
        )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def main() -> None:
    """Export configured symbols and print a compact summary."""
    args = parse_args()
    codes = list(dict.fromkeys(args.codes))
    frame = prepare_bars(read_daily_bars(codes))
    write_duckdb(frame, args.output)
    summary = (
        frame.groupby(["symbol", "name"], as_index=False)
        .agg(
            first_date=("trade_date", "min"),
            last_date=("trade_date", "max"),
            bars=("trade_date", "size"),
        )
        .sort_values("symbol")
    )
    print(f"wrote {len(frame)} bars to {args.output}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
