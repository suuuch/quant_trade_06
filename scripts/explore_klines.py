"""Probe the kline tables for date ranges, period values, and sample data."""

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
        ("kline_data row count", "SELECT COUNT(*) FROM public.kline_data"),
        (
            "kline_data period distinct",
            "SELECT period, COUNT(*) FROM public.kline_data GROUP BY period ORDER BY period",
        ),
        (
            "kline_data date range per period",
            """
            SELECT period,
                   MIN(time_key)::date AS min_d, MAX(time_key)::date AS max_d,
                   COUNT(DISTINCT symbol) AS symbols
            FROM public.kline_data GROUP BY period ORDER BY period
            """,
        ),
        (
            "kline_data sample (daily)",
            """
            SELECT symbol, time_key, open, high, low, close, volume, period
            FROM public.kline_data
            WHERE period = 'daily'
            ORDER BY time_key DESC LIMIT 3
            """,
        ),
        ("stock_klines row count", "SELECT COUNT(*) FROM public.stock_klines"),
        (
            "stock_klines date range",
            "SELECT MIN(kline_date), MAX(kline_date), COUNT(DISTINCT code) FROM public.stock_klines",
        ),
        (
            "stock_klines sample",
            "SELECT * FROM public.stock_klines ORDER BY kline_date DESC LIMIT 3",
        ),
        ("tushare.daily row count", "SELECT COUNT(*) FROM tushare.daily"),
        (
            "tushare.daily date range",
            "SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT ts_code) FROM tushare.daily",
        ),
        (
            "tushare.daily sample",
            "SELECT * FROM tushare.daily ORDER BY trade_date DESC LIMIT 3",
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
