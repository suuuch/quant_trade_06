"""Scan a handful of A-share symbols and tabulate signals + P&L."""

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
    sql = """
        SELECT ts_code, COUNT(*) AS n, MIN(trade_date) AS min_d, MAX(trade_date) AS max_d
        FROM tushare.daily
        WHERE trade_date BETWEEN '20240101' AND '20260720'
        GROUP BY ts_code
        HAVING COUNT(*) > 200
        ORDER BY RANDOM()
        LIMIT 20
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        symbols = [row[0] for row in cur.fetchall()]
    conn.close()
    print(" ".join(symbols))


if __name__ == "__main__":
    main()
