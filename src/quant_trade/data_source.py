"""DataSource abstraction.

Concrete implementations load OHLCV from PostgreSQL (tushare.daily) or CSV.
Output is a pandas DataFrame indexed by date with columns
[open, high, low, close, volume].
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd
import psycopg
from dotenv import load_dotenv


@dataclass(frozen=True)
class DataSourceConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> DataSourceConfig:
        load_dotenv()
        return cls(
            host=os.environ["PG_HOST"],
            port=int(os.environ["PG_PORT"]),
            dbname=os.environ["PG_DB"],
            user=os.environ["PG_USER"],
            password=os.environ["PG_PASSWORD"],
        )


class DataSource(Protocol):
    def load_ohlcv(
        self,
        symbol: str,
        frequency: str,
        start: str,
        end: str,
    ) -> pd.DataFrame: ...


class PostgresDataSource:
    """Loads A-share daily klines from tushare.daily.

    symbol uses Tushare ts_code format, e.g. '000001.SZ' or '600519.SH'.
    frequency is informational; this source only serves daily granularity.
    """

    TABLE = "tushare.daily"

    def __init__(self, cfg: DataSourceConfig | None = None) -> None:
        self._cfg = cfg or DataSourceConfig.from_env()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            dbname=self._cfg.dbname,
            user=self._cfg.user,
            password=self._cfg.password,
        )

    def load_ohlcv(
        self,
        symbol: str,
        frequency: str = "daily",
        start: str = "19900101",
        end: str = "20991231",
    ) -> pd.DataFrame:
        if frequency != "daily":
            raise NotImplementedError(
                f"PostgresDataSource only serves daily; got {frequency!r}"
            )
        sql = f"""
            SELECT trade_date, open, high, low, close, vol
            FROM {self.TABLE}
            WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date
        """
        with self._connect() as conn:
            df = pd.read_sql_query(sql, conn, params=(symbol, start, end))
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        df = df.rename(columns={"vol": "volume"})
        df = df.set_index("trade_date").sort_index()
        return df[["open", "high", "low", "close", "volume"]]

    def list_symbols(self) -> list[str]:
        sql = "SELECT DISTINCT ts_code FROM tushare.daily ORDER BY ts_code"
        with self._connect() as conn:
            return [row[0] for row in conn.execute(sql).fetchall()]


class CsvDataSource:
    """Loads a single CSV file. Expected columns: date,open,high,low,close,volume."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load_ohlcv(
        self,
        symbol: str = "",
        frequency: str = "daily",
        start: str = "",
        end: str = "",
    ) -> pd.DataFrame:
        df = pd.read_csv(self._path, parse_dates=["date"])
        df = df.set_index("date").sort_index()
        if start:
            df = df[df.index >= pd.to_datetime(start)]
        if end:
            df = df[df.index <= pd.to_datetime(end)]
        return df[["open", "high", "low", "close", "volume"]]
