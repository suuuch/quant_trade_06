"""Tests for the scheduled A-share QQ service helpers."""

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from quant_trade.delivery_store import (
    DeliveryImage,
    DeliveryMetadata,
    PreparedDelivery,
    delivery_duckdb_path,
    delivery_images_exist,
    load_prepared_delivery,
    save_delivery_to_duckdb,
    save_prepared_delivery,
)
from quant_trade.qq_service import (
    ClearCacheCommand,
    RsiCommand,
    StopCommand,
    WmCommand,
    _delivery_direction_label,
    format_filter_conditions,
    market_data_ready,
    parse_qq_command,
    parse_rsi_command,
    parse_wm_command,
    seconds_until_check,
)
from quant_trade.rsi50 import Direction
from quant_trade.scanner import MarketDataStatus


def test_market_data_is_ready_only_when_both_tables_have_today() -> None:
    today = date(2026, 8, 10)

    assert market_data_ready(MarketDataStatus(today, True, today, today)) is True
    assert (
        market_data_ready(MarketDataStatus(today, True, date(2026, 8, 7), today))
        is False
    )
    assert market_data_ready(MarketDataStatus(today, False, today, today)) is False


def test_seconds_until_check_uses_today_then_next_day() -> None:
    zone = ZoneInfo("Asia/Shanghai")

    assert seconds_until_check(
        datetime(2026, 8, 10, 17, 30, tzinfo=zone), time(18)
    ) == pytest.approx(30 * 60)
    assert seconds_until_check(
        datetime(2026, 8, 10, 18, 30, tzinfo=zone), time(18)
    ) == pytest.approx(23.5 * 60 * 60)


def test_filter_conditions_include_current_a_share_rules() -> None:
    text = format_filter_conditions()

    assert "MA20 最近 15 Bar 拟合角度 > 20°" in text
    assert "MA20 最近 15 Bar 拟合角度 < -20°" in text
    assert "最新一天 RSI(14) 位于 40–60" in text
    assert "多头：最近 5 天 RSI 全部位于 50–65" in text
    assert "空头：最近 5 天 RSI 全部位于 35–50" in text
    assert "MA30" not in text


@pytest.mark.parametrize(
    ("content", "market", "pattern", "history"),
    [
        ("发送A股W底", "a", "w", False),
        ("发送美股M顶", "us", "m", False),
        ("发送A股WM", "a", "wm", False),
        ("发送形态", "a", "wm", False),
        ("发送历史A股W底", "a", "w", True),
        ("发送美股M顶历史", "us", "m", True),
    ],
)
def test_wm_commands_select_market_and_pattern(
    content: str,
    market: str,
    pattern: str,
    history: bool,
) -> None:
    command = parse_wm_command(content)

    assert command is not None
    assert command.market == market
    assert command.pattern == pattern
    assert command.history is history


def test_non_wm_send_command_is_left_for_rsi_handler() -> None:
    assert parse_wm_command("发送历史") is None


def test_rsi_command_accepts_optional_target_date() -> None:
    default_command = parse_rsi_command("发送")
    dated_command = parse_rsi_command("发送2026-07-25")
    spaced_command = parse_rsi_command("发送 2026-07-25")

    assert default_command is not None
    assert default_command.scan_date is None
    assert dated_command is not None
    assert dated_command.scan_date == date(2026, 7, 25)
    assert spaced_command is not None
    assert spaced_command.scan_date == date(2026, 7, 25)


def test_qq_command_parser_returns_atomic_command_types() -> None:
    assert isinstance(parse_qq_command("停止发送"), StopCommand)
    assert isinstance(parse_qq_command("清理缓存"), ClearCacheCommand)
    assert isinstance(parse_qq_command("发送W底"), WmCommand)
    assert isinstance(parse_qq_command("发送2026-07-25"), RsiCommand)
    assert parse_qq_command("随便看看") is None


def test_wm_delivery_label_does_not_use_rsi_strategy_name() -> None:
    assert _delivery_direction_label(None, "W/M") == "W/M"


def test_prepared_delivery_manifest_survives_restart(tmp_path: Path) -> None:
    image = tmp_path / "batch.png"
    image.write_bytes(b"png")
    delivery = PreparedDelivery(
        scan_date=date(2026, 8, 7),
        summary="历史扫描",
        images=(DeliveryImage(image, ("000001.SZ",)),),
    )
    manifest = tmp_path / "latest_delivery.json"

    save_prepared_delivery(delivery, manifest)

    assert load_prepared_delivery(manifest) == PreparedDelivery(
        scan_date=date(2026, 8, 7),
        summary="历史扫描",
        images=(DeliveryImage(image.resolve(), ("000001.SZ",)),),
    )


def test_prepared_delivery_manifest_preserves_image_direction(tmp_path: Path) -> None:
    image = tmp_path / "batch.png"
    image.write_bytes(b"png")
    delivery = PreparedDelivery(
        scan_date=date(2026, 8, 7),
        summary="历史扫描",
        images=(DeliveryImage(image, ("000001.SZ",), Direction.LONG),),
    )
    manifest = tmp_path / "latest_delivery.json"

    save_prepared_delivery(delivery, manifest)

    assert load_prepared_delivery(manifest) == PreparedDelivery(
        scan_date=date(2026, 8, 7),
        summary="历史扫描",
        images=(DeliveryImage(image.resolve(), ("000001.SZ",), Direction.LONG),),
    )


def test_delivery_images_exist_rejects_missing_paths(tmp_path: Path) -> None:
    existing = tmp_path / "existing.png"
    missing = tmp_path / "missing.png"
    existing.write_bytes(b"png")

    delivery = PreparedDelivery(
        scan_date=date(2026, 8, 7),
        summary="历史扫描",
        images=(
            DeliveryImage(existing, ("000001.SZ",)),
            DeliveryImage(missing, ("000002.SZ",)),
        ),
    )

    assert delivery_images_exist(delivery) is False


def test_prepared_delivery_is_written_to_duckdb(tmp_path: Path) -> None:
    image = tmp_path / "batch.png"
    image.write_bytes(b"png")
    delivery = PreparedDelivery(
        scan_date=date(2026, 8, 7),
        summary="RSI 顺势交易 A股日线扫描 2026-08-07",
        images=(DeliveryImage(image, ("000001.SZ",), Direction.LONG),),
        metadata=DeliveryMetadata("RSI 顺势交易", "a"),
    )
    manifest = tmp_path / "rsi_2026-08-07.json"
    database = delivery_duckdb_path(tmp_path)

    save_delivery_to_duckdb(
        delivery,
        database,
        manifest_path=manifest,
    )

    with duckdb.connect(str(database)) as connection:
        delivery_rows = connection.execute(
            "SELECT strategy, market, scan_date, image_count FROM deliveries"
        ).fetchall()
        image_rows = connection.execute(
            "SELECT image_index, symbols, direction FROM delivery_images"
        ).fetchall()

    assert delivery_rows == [("RSI 顺势交易", "a", date(2026, 8, 7), 1)]
    assert image_rows == [(1, '["000001.SZ"]', "long")]
