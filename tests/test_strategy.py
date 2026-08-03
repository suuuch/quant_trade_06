"""Tests for market-specific Backtrader execution behavior."""

from quant_trade.rsi50 import Direction
from quant_trade.strategy import target_for_signal


def test_short_signal_closes_long_in_cash_equity_mode() -> None:
    target = target_for_signal(
        Direction.SHORT,
        target_percent=0.95,
        allow_short=False,
        current_size=100.0,
    )

    assert target == 0.0


def test_short_signal_does_not_open_short_in_cash_equity_mode() -> None:
    target = target_for_signal(
        Direction.SHORT,
        target_percent=0.95,
        allow_short=False,
        current_size=0.0,
    )

    assert target is None
