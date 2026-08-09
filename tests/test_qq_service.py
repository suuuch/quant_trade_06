"""Tests for the scheduled A-share QQ service helpers."""

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant_trade.qq_service import (
    DeliveryImage,
    PreparedDelivery,
    format_filter_conditions,
    load_prepared_delivery,
    market_data_ready,
    parse_wm_command,
    save_prepared_delivery,
    seconds_until_check,
)
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

    assert "MA20 最近 15 Bar 拟合角度 > 40°" in text
    assert "MA20 最近 15 Bar 拟合角度 < -40°" in text
    assert "RSI(14) 曾进入 45–55" in text
    assert "多头：当前 RSI 45–55，T-5 至 T 全部位于 50–58" in text
    assert "空头：当前 RSI 42–50，T-5 至 T 全部位于 42–50" in text


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
