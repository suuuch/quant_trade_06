"""One-off database explorer. Not part of the backtest package."""

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
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
            """
        )
        tables = cur.fetchall()
        print(f"Found {len(tables)} tables:")
        for schema, name in tables:
            print(f"  {schema}.{name}")

        if tables:
            print("\n--- Column details for each table ---")
            for schema, name in tables:
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (schema, name),
                )
                cols = cur.fetchall()
                print(f"\n[{schema}.{name}]")
                for cname, ctype, nullable in cols:
                    print(f"  {cname:30s} {ctype:25s} {'NULL' if nullable == 'YES' else 'NOT NULL'}")
    conn.close()


if __name__ == "__main__":
    main()
