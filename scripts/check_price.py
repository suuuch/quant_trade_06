"""Quick check: what did the price do after the signal?"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


def main() -> None:
    load_dotnet = None  # noqa
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    conn = psycopg.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ["PG_PORT"]),
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
    )
    sql = """
        SELECT trade_date, open, high, low, close, vol
        FROM tushare.daily
        WHERE ts_code = '000001.SZ' AND trade_date >= '20250901' AND trade_date <= '20251031'
        ORDER BY trade_date
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        for r in rows:
            print(f"{r[0]}  o={r[1]:.2f} h={r[2]:.2f} l={r[3]:.2f} c={r[4]:.2f}")
    conn.close()


if __name__ == "__main__":
    main()
