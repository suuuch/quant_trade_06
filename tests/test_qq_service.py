"""Tests for the scheduled A-share QQ service helpers."""

from datetime import date, datetime, time
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import duckdb
import pytest

from quant_trade.delivery_store import (
    DeliveryImage,
    DeliveryMetadata,
    DeliverySignalResult,
    PreparedDelivery,
    delivery_duckdb_path,
    delivery_images_exist,
    load_prepared_delivery,
    save_delivery_to_duckdb,
    save_prepared_delivery,
    save_signal_results_to_duckdb,
)
from quant_trade.qq_bot import QQBotError
from quant_trade.qq_service import (
    ClearCacheCommand,
    ContinueCommand,
    QQSignalService,
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
from quant_trade.scanner import DatabaseSettings, MarketDataStatus


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

    assert "MA20 或 MA30 过去 10 天平均每天上涨大于 0.3%" in text
    assert "MA20 或 MA30 过去 10 天平均每天下跌大于 0.3%" in text
    assert "最新一天 RSI(14) 位于 42–58" in text
    assert "多头：最近 5 天 RSI 全部位于 50–58" in text
    assert "空头：最近 5 天 RSI 全部位于 42–50" in text


def test_filter_conditions_describe_us_share_same_ma_rules() -> None:
    text = format_filter_conditions(market="us")

    assert "MA20 或 MA30 过去 10 天平均每天上涨大于 0.3%" in text
    assert "MA20 或 MA30 过去 10 天平均每天下跌大于 0.3%" in text
    assert "多头：最近 5 天 RSI 全部位于 50–58" in text
    assert "空头：最近 5 天 RSI 全部位于 42–50" in text
    assert "MA20 向上" not in text


@pytest.mark.parametrize(
    ("content", "market", "pattern", "history"),
    [
        ("发送A股W底", "a", "w", False),
        ("发送美股W底", "us", "w", False),
        ("发送美股Ｗ底", "us", "w", False),
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
    us_command = parse_rsi_command("发送美股")
    us_dated_command = parse_rsi_command("发送美股2026-07-25")

    assert default_command is not None
    assert default_command.market == "a"
    assert default_command.scan_date is None
    assert dated_command is not None
    assert dated_command.market == "a"
    assert dated_command.scan_date == date(2026, 7, 25)
    assert spaced_command is not None
    assert spaced_command.scan_date == date(2026, 7, 25)
    assert us_command is not None
    assert us_command.market == "us"
    assert us_command.scan_date is None
    assert us_dated_command is not None
    assert us_dated_command.market == "us"
    assert us_dated_command.scan_date == date(2026, 7, 25)


def test_qq_command_parser_returns_atomic_command_types() -> None:
    assert isinstance(parse_qq_command("停止发送"), StopCommand)
    assert isinstance(parse_qq_command("清理缓存"), ClearCacheCommand)
    assert isinstance(parse_qq_command("继续"), ContinueCommand)
    assert isinstance(parse_qq_command("发送W底"), WmCommand)
    assert isinstance(parse_qq_command("发送美股Ｗ底"), WmCommand)
    assert parse_qq_command("发送美股Ｗ底") == WmCommand("us", "w")
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


def test_signal_results_are_written_to_duckdb(tmp_path: Path) -> None:
    database = delivery_duckdb_path(tmp_path)
    metadata = DeliveryMetadata("RSI 顺势交易", "a")
    executed_at = datetime(2026, 8, 10, 18, 30)

    save_signal_results_to_duckdb(
        database,
        metadata=metadata,
        scan_date=date(2026, 8, 10),
        results=[
            DeliverySignalResult(
                code="000001.SZ",
                signal_datetime=date(2026, 8, 10),
                side="long",
                close_price=12.34,
                signal_category="RSI 顺势交易多头",
                executed_at=executed_at,
                signal_fill=True,
                name="平安银行",
                industry="银行",
                rsi=52.1,
                atr=0.8,
                market_cap_cny=100_000_000.0,
            )
        ],
    )

    with duckdb.connect(str(database)) as connection:
        rows = connection.execute(
            """
            SELECT code, datetime, side, close_price, signal_category,
                   executed_at, signal_fill
            FROM strategy_signal_results
            """
        ).fetchall()

    assert rows == [
        (
            "000001.SZ",
            date(2026, 8, 10),
            "long",
            12.34,
            "RSI 顺势交易多头",
            executed_at,
            True,
        )
    ]


def test_batch_delivery_uses_passive_reply_for_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, int | None]] = []

    class FakeQQBotClient:
        def send_text(
            self,
            target_type: str,
            target_id: str,
            content: str,
            *,
            msg_id: str | None = None,
            msg_seq: int | None = None,
        ) -> dict[str, str]:
            calls.append(("text", msg_id, msg_seq))
            return {}

        def send_image(
            self,
            target_type: str,
            target_id: str,
            image_path: Path,
            *,
            content: str = "",
            msg_id: str | None = None,
            msg_seq: int | None = None,
        ) -> dict[str, str]:
            calls.append(("image", msg_id, msg_seq))
            return {}

    monkeypatch.setattr("quant_trade.qq_service.QQBotClient", FakeQQBotClient)
    image = tmp_path / "batch.png"
    image.write_bytes(b"png")
    service = QQSignalService(
        settings=DatabaseSettings("localhost", 5432, "db", "user", "password"),
        output_root=tmp_path,
        check_time=time(18),
        poll_seconds=300.0,
        send_delay=0.0,
        lookback_bars=240,
        charts_per_message=4,
    )
    prepared = PreparedDelivery(
        scan_date=date(2026, 8, 10),
        summary="summary",
        images=(DeliveryImage(image, ("000001.SZ",), Direction.LONG),),
        metadata=DeliveryMetadata("RSI 顺势交易", "a"),
    )

    service._deliver_sync(
        "group",
        "group-openid",
        "source-message-id",
        prepared,
        Event(),
    )

    assert calls == [
        ("text", "source-message-id", 1),
        ("image", "source-message-id", 2),
    ]


def test_batch_delivery_can_resume_after_failed_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, int | None]] = []
    attempts = {"images": 0}

    class FakeQQBotClient:
        def send_text(
            self,
            target_type: str,
            target_id: str,
            content: str,
            *,
            msg_id: str | None = None,
            msg_seq: int | None = None,
        ) -> dict[str, str]:
            calls.append(("text", content, msg_seq))
            return {}

        def send_image(
            self,
            target_type: str,
            target_id: str,
            image_path: Path,
            *,
            content: str = "",
            msg_id: str | None = None,
            msg_seq: int | None = None,
        ) -> dict[str, str]:
            attempts["images"] += 1
            calls.append(("image", content, msg_seq))
            if attempts["images"] == 2:
                raise QQBotError("temporary failure")
            return {}

    monkeypatch.setattr("quant_trade.qq_service.QQBotClient", FakeQQBotClient)
    images = []
    for index in range(2):
        image = tmp_path / f"batch_{index}.png"
        image.write_bytes(b"png")
        images.append(DeliveryImage(image, (f"00000{index}.SZ",), Direction.LONG))
    service = QQSignalService(
        settings=DatabaseSettings("localhost", 5432, "db", "user", "password"),
        output_root=tmp_path,
        check_time=time(18),
        poll_seconds=300.0,
        send_delay=0.0,
        lookback_bars=240,
        charts_per_message=4,
    )
    prepared = PreparedDelivery(
        scan_date=date(2026, 8, 10),
        summary="summary",
        images=tuple(images),
        metadata=DeliveryMetadata("RSI 顺势交易", "a"),
    )

    with pytest.raises(QQBotError):
        service._deliver_sync(
            "group",
            "group-openid",
            "source-message-id",
            prepared,
            Event(),
        )
    state = service._resume_states[("group", "group-openid")]
    service._deliver_sync(
        "group",
        "group-openid",
        "continue-message-id",
        state.prepared,
        Event(),
        start_image_index=state.next_image_index,
        send_summary=False,
    )

    assert [call[0] for call in calls] == ["text", "image", "image", "image"]
    assert calls[-1][1].startswith("RSI 顺势交易多头信号 2/2")
    assert calls[-1][2] == 1
    assert ("group", "group-openid") not in service._resume_states
