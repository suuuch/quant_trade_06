"""Confirm tushare.daily is A-share, and check for hourly data."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    conn = psycopg.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
    )
    queries = [
        (
            "A-share prefix distribution (ts_code suffix)",
            "SELECT RIGHT(ts_code, 3) AS suffix, COUNT(DISTINCT ts_code) FROM tushare.daily GROUP BY suffix ORDER BY suffix",
        ),
        (
            "Sample 000001.SZ rows",
            """
            SELECT ts_code, trade_date, open, high, low, close, vol, amount, pct_chg
            FROM tushare.daily
            WHERE ts_code = '000001.SZ'
            ORDER BY trade_date DESC LIMIT 5
            """,
        ),
        (
            "Sample 600519.SH (Maotai) rows",
            """
            SELECT ts_code, trade_date, open, high, low, close, vol, amount, pct_chg
            FROM tushare.daily
            WHERE ts_code = '600519.SH'
            ORDER BY trade_date DESC LIMIT 5
            """,
        ),
        (
            "trade_date format",
            "SELECT MIN(trade_date), MAX(trade_date) FROM tushare.daily",
        ),
        (
            "Check tushare.stk_holdernumber tables / 1h-like data existence",
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog','information_schema')
              AND (table_name ILIKE '%1h%' OR table_name ILIKE '%hour%' OR table_name ILIKE '%60m%' OR table_name ILIKE '%min%')
            ORDER BY 1,2
            """,
        ),
        (
            "stock_basic counts",
            "SELECT exchange, market, COUNT(*) FROM tushare.stock_basic GROUP BY exchange, market ORDER BY exchange, market",
        ),
        (
            "stock_basic sample A-share",
            "SELECT ts_code, symbol, name, industry, list_date FROM tushare.stock_basic WHERE ts_code IN ('000001.SZ','600519.SH','300750.SZ')",
        ),
    ]
    with conn.cursor() as cur:
        for label, sql in queries:
            cur.execute(sql)
            print(f"\n=== {label} ===")
            for row in cur.fetchall():
                print(row)
    conn.close()


if __name__ == "__main__":
    main()
