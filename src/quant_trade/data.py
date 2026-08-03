"""Load local DuckDB market data for Backtrader backtests."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from pathlib import Path

import duckdb
import pandas as pd


def load_duckdb_bars(database: str | Path, symbol: str) -> pd.DataFrame:
    """Load one symbol as a datetime-indexed OHLCV frame."""
    query = """
        SELECT trade_date, open, high, low, close, volume
        FROM a_share_daily
        WHERE symbol = ?
        ORDER BY trade_date
    """
    with duckdb.connect(str(database), read_only=True) as connection:
        frame = connection.execute(query, [symbol]).fetchdf()
    if frame.empty:
        raise ValueError(f"symbol not found in DuckDB: {symbol}")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    return frame.set_index("trade_date")
