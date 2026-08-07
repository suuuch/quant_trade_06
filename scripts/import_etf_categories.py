"""Import ETF category rows into PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

from quant_trade.etf_categories import parse_etf_categories

DEFAULT_INPUT = Path("data/etf_list.txt")


def main() -> None:
    """Load ETF categories from the project list into PostgreSQL."""
    load_dotenv()
    rows = parse_etf_categories(DEFAULT_INPUT.read_text())
    connection_kwargs = {
        "host": _required_env("PG_HOST"),
        "port": int(os.getenv("PG_PORT", "5432")),
        "dbname": _required_env("PG_DB"),
        "user": _required_env("PG_USER"),
        "password": _required_env("PG_PASSWORD"),
        "connect_timeout": 10,
    }

    with psycopg.connect(**connection_kwargs) as connection:
        with connection.cursor() as cursor:
            _create_tables(cursor)
            cursor.executemany(
                """
                INSERT INTO public.etf_categories (
                    symbol,
                    code,
                    category_group,
                    category_label,
                    source_order
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (symbol) DO UPDATE SET
                    code = EXCLUDED.code,
                    category_group = EXCLUDED.category_group,
                    category_label = EXCLUDED.category_label,
                    source_order = EXCLUDED.source_order,
                    updated_at = now()
                """,
                [
                    (
                        row.symbol,
                        row.code,
                        row.category_group,
                        row.category_label,
                        row.source_order,
                    )
                    for row in rows
                ],
            )
        connection.commit()

    print(f"imported {len(rows)} ETF category rows")


def _create_tables(cursor: psycopg.Cursor[Any]) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS public.etf_categories (
            symbol varchar(16) PRIMARY KEY,
            code varchar(32) NOT NULL UNIQUE,
            category_group text NOT NULL,
            category_label text NOT NULL,
            source_order integer NOT NULL,
            updated_at timestamp without time zone NOT NULL DEFAULT now()
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS public.etf_holdings (
            etf_symbol varchar(16) NOT NULL
                REFERENCES public.etf_categories(symbol) ON DELETE CASCADE,
            holding_code varchar(32) NOT NULL,
            holding_symbol varchar(32) NOT NULL,
            holding_name text,
            weight numeric(12, 8),
            shares numeric,
            market_value numeric,
            as_of_date date NOT NULL,
            source text NOT NULL,
            fetched_at timestamp without time zone NOT NULL DEFAULT now(),
            PRIMARY KEY (etf_symbol, holding_code, as_of_date, source)
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_etf_holdings_holding_code
        ON public.etf_holdings(holding_code)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_etf_holdings_etf_as_of
        ON public.etf_holdings(etf_symbol, as_of_date DESC)
        """
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


if __name__ == "__main__":
    main()
