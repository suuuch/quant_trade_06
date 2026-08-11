"""Delivery cache and audit storage for QQ signal batches."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import duckdb

from quant_trade.rsi50 import Direction


@dataclass(frozen=True)
class DeliveryImage:
    """One combined image and the symbols displayed in it."""

    path: Path
    symbols: tuple[str, ...]
    direction: Direction | None = None


@dataclass(frozen=True)
class DeliveryMetadata:
    """Stable metadata used to relate cache rows and send events."""

    strategy: str
    market: str = "a"
    pattern: str | None = None


@dataclass(frozen=True)
class PreparedDelivery:
    """A completed daily scan ready for immediate QQ delivery."""

    scan_date: date
    summary: str
    images: tuple[DeliveryImage, ...]
    metadata: DeliveryMetadata = field(
        default_factory=lambda: DeliveryMetadata("unknown")
    )


@dataclass(frozen=True)
class DeliverySignalResult:
    """One strategy signal row persisted for text/database review."""

    code: str
    signal_datetime: date
    side: str
    close_price: float
    signal_category: str
    executed_at: datetime
    signal_fill: bool = True
    name: str = ""
    industry: str = ""
    rsi: float | None = None
    atr: float | None = None
    market_cap_cny: float | None = None
    neckline: float | None = None


def save_prepared_delivery(delivery: PreparedDelivery, manifest: Path) -> None:
    """Persist a prepared scan so Supervisor restarts can send it."""
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scan_date": delivery.scan_date.isoformat(),
        "summary": delivery.summary,
        "metadata": {
            "strategy": delivery.metadata.strategy,
            "market": delivery.metadata.market,
            "pattern": delivery.metadata.pattern,
        },
        "images": [
            {
                "path": str(image.path.resolve()),
                "symbols": list(image.symbols),
                "direction": (
                    None if image.direction is None else image.direction.value
                ),
            }
            for image in delivery.images
        ],
    }
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(manifest)


def load_prepared_delivery(manifest: Path) -> PreparedDelivery | None:
    """Load the most recent prepared scan, returning None when unavailable."""
    if not manifest.exists():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        images = tuple(
            DeliveryImage(
                path=Path(item["path"]),
                symbols=tuple(str(symbol) for symbol in item["symbols"]),
                direction=(
                    None
                    if item.get("direction") is None
                    else Direction(str(item["direction"]))
                ),
            )
            for item in payload["images"]
        )
        if any(not image.path.exists() for image in images):
            return None
        metadata = payload.get("metadata", {})
        return PreparedDelivery(
            scan_date=date.fromisoformat(payload["scan_date"]),
            summary=str(payload["summary"]),
            images=images,
            metadata=DeliveryMetadata(
                strategy=str(metadata.get("strategy", "unknown")),
                market=str(metadata.get("market", "a")),
                pattern=(
                    None
                    if metadata.get("pattern") is None
                    else str(metadata["pattern"])
                ),
            ),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def delivery_images_exist(delivery: PreparedDelivery) -> bool:
    """Return whether every image referenced by a delivery still exists."""
    return all(image.path.exists() for image in delivery.images)


def delivery_duckdb_path(output_root: Path) -> Path:
    """Return the DuckDB path used for QQ delivery records."""
    return output_root / "qq_delivery.duckdb"


def save_delivery_to_duckdb(
    delivery: PreparedDelivery,
    database_path: Path,
    *,
    manifest_path: Path,
) -> None:
    """Persist one prepared delivery and its image metadata to DuckDB."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = delivery.metadata
    with duckdb.connect(str(database_path)) as connection:
        _ensure_delivery_duckdb_schema(connection)
        try:
            connection.execute("BEGIN")
            params = (
                metadata.strategy,
                metadata.market,
                metadata.pattern or "",
                delivery.scan_date,
            )
            connection.execute(
                """
                DELETE FROM delivery_images
                WHERE strategy = ? AND market = ? AND coalesce(pattern, '') = ?
                  AND scan_date = ?
                """,
                params,
            )
            connection.execute(
                """
                DELETE FROM deliveries
                WHERE strategy = ? AND market = ? AND coalesce(pattern, '') = ?
                  AND scan_date = ?
                """,
                params,
            )
            connection.execute(
                """
                INSERT INTO deliveries (
                    strategy,
                    market,
                    pattern,
                    scan_date,
                    summary,
                    image_count,
                    manifest_path,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp)
                """,
                (
                    metadata.strategy,
                    metadata.market,
                    metadata.pattern,
                    delivery.scan_date,
                    delivery.summary,
                    len(delivery.images),
                    str(manifest_path.resolve()),
                ),
            )
            connection.executemany(
                """
                INSERT INTO delivery_images (
                    strategy,
                    market,
                    pattern,
                    scan_date,
                    image_index,
                    path,
                    symbols,
                    direction
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        metadata.strategy,
                        metadata.market,
                        metadata.pattern,
                        delivery.scan_date,
                        index,
                        str(image.path.resolve()),
                        json.dumps(list(image.symbols), ensure_ascii=False),
                        None if image.direction is None else image.direction.value,
                    )
                    for index, image in enumerate(delivery.images, start=1)
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def save_signal_results_to_duckdb(
    database_path: Path,
    *,
    metadata: DeliveryMetadata,
    scan_date: date,
    results: list[DeliverySignalResult],
) -> None:
    """Persist per-symbol strategy signal rows to DuckDB."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path)) as connection:
        _ensure_delivery_duckdb_schema(connection)
        try:
            connection.execute("BEGIN")
            params = (
                metadata.strategy,
                metadata.market,
                metadata.pattern or "",
                scan_date,
            )
            connection.execute(
                """
                DELETE FROM strategy_signal_results
                WHERE strategy = ? AND market = ? AND coalesce(pattern, '') = ?
                  AND scan_date = ?
                """,
                params,
            )
            if results:
                connection.executemany(
                    """
                    INSERT INTO strategy_signal_results (
                        strategy,
                        market,
                        pattern,
                        scan_date,
                        code,
                        datetime,
                        side,
                        close_price,
                        signal_category,
                        executed_at,
                        signal_fill,
                        name,
                        industry,
                        rsi,
                        atr,
                        market_cap_cny,
                        neckline,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            metadata.strategy,
                            metadata.market,
                            metadata.pattern,
                            scan_date,
                            result.code,
                            result.signal_datetime,
                            result.side,
                            result.close_price,
                            result.signal_category,
                            result.executed_at,
                            result.signal_fill,
                            result.name,
                            result.industry,
                            result.rsi,
                            result.atr,
                            result.market_cap_cny,
                            result.neckline,
                            datetime.now(),
                        )
                        for result in results
                    ],
                )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def record_delivery_send_event(
    database_path: Path,
    *,
    metadata: DeliveryMetadata,
    scan_date: date,
    target_type: str,
    target_id: str,
    status: str,
    detail: str = "",
) -> None:
    """Append one QQ sending event to DuckDB."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path)) as connection:
        _ensure_delivery_duckdb_schema(connection)
        connection.execute(
            """
            INSERT INTO delivery_send_events (
                strategy,
                market,
                pattern,
                scan_date,
                target_type,
                target_id,
                status,
                detail,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
            """,
            (
                metadata.strategy,
                metadata.market,
                metadata.pattern,
                scan_date,
                target_type,
                target_id,
                status,
                detail,
            ),
        )


def _ensure_delivery_duckdb_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
            strategy TEXT NOT NULL,
            market TEXT NOT NULL,
            pattern TEXT,
            scan_date DATE NOT NULL,
            summary TEXT NOT NULL,
            image_count INTEGER NOT NULL,
            manifest_path TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    connection.execute("ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS pattern TEXT")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_images (
            strategy TEXT NOT NULL,
            market TEXT NOT NULL,
            pattern TEXT,
            scan_date DATE NOT NULL,
            image_index INTEGER NOT NULL,
            path TEXT NOT NULL,
            symbols TEXT NOT NULL,
            direction TEXT
        )
        """
    )
    connection.execute(
        "ALTER TABLE delivery_images ADD COLUMN IF NOT EXISTS pattern TEXT"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_send_events (
            strategy TEXT NOT NULL,
            market TEXT NOT NULL,
            pattern TEXT,
            scan_date DATE NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
        """
    )
    connection.execute(
        "ALTER TABLE delivery_send_events ADD COLUMN IF NOT EXISTS pattern TEXT"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_signal_results (
            strategy TEXT NOT NULL,
            market TEXT NOT NULL,
            pattern TEXT,
            scan_date DATE NOT NULL,
            code TEXT NOT NULL,
            datetime DATE NOT NULL,
            side TEXT NOT NULL,
            close_price DOUBLE NOT NULL,
            signal_category TEXT NOT NULL,
            executed_at TIMESTAMP NOT NULL,
            signal_fill BOOLEAN NOT NULL,
            name TEXT,
            industry TEXT,
            rsi DOUBLE,
            atr DOUBLE,
            market_cap_cny DOUBLE,
            neckline DOUBLE,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    connection.execute(
        "ALTER TABLE strategy_signal_results ADD COLUMN IF NOT EXISTS pattern TEXT"
    )
    connection.execute(
        """
        ALTER TABLE strategy_signal_results
        ADD COLUMN IF NOT EXISTS signal_fill BOOLEAN
        """
    )
    connection.execute(
        """
        ALTER TABLE strategy_signal_results
        ADD COLUMN IF NOT EXISTS signal_category TEXT
        """
    )
    connection.execute(
        """
        ALTER TABLE strategy_signal_results
        ADD COLUMN IF NOT EXISTS executed_at TIMESTAMP
        """
    )
    connection.execute(
        """
        ALTER TABLE strategy_signal_results
        ADD COLUMN IF NOT EXISTS datetime DATE
        """
    )
