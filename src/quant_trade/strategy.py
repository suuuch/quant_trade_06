"""Backtrader adapter for backtests and live broker/data integrations."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUntypedBaseClass=false, reportUnknownVariableType=false

from typing import Any

import backtrader as bt

from quant_trade.rsi50 import Bar, Direction, Rsi50Config, Rsi50SignalEngine


def target_for_signal(
    direction: Direction,
    *,
    target_percent: float,
    allow_short: bool,
    current_size: float,
) -> float | None:
    """Translate a signal into a target exposure for the selected market mode."""
    if direction is Direction.LONG:
        return target_percent
    if allow_short:
        return -target_percent
    if current_size > 0:
        return 0.0
    return None


class Rsi50TrendStrategy(bt.Strategy):  # type: ignore[misc]
    """Execute RSI trend-following signals through a Backtrader broker.

    The strategy targets a percentage of equity on a signal. An opposite signal
    reverses the position. No stop-loss or take-profit is added because the
    source strategy document does not define either rule.
    """

    params = (
        ("config", None),
        ("target_percent", 0.95),
        ("allow_short", True),
    )

    def __init__(self) -> None:
        """Initialize the stateful signal engine."""
        config = self.p.config
        if config is not None and not isinstance(config, Rsi50Config):
            raise TypeError("config must be an Rsi50Config instance")
        if not 0.0 < self.p.target_percent <= 1.0:
            raise ValueError("target_percent must be in (0, 1]")
        self.engine = Rsi50SignalEngine(config)
        self.pending_order: Any | None = None
        self.last_signal: Direction | None = None

    def next(self) -> None:
        """Process one completed data bar and submit a target order if needed."""
        timestamp = self.data.datetime.datetime(0)
        signal = self.engine.on_bar(
            Bar(
                timestamp=timestamp,
                open=float(self.data.open[0]),
                high=float(self.data.high[0]),
                low=float(self.data.low[0]),
                close=float(self.data.close[0]),
                volume=float(self.data.volume[0]),
            )
        )
        if signal is None or self.pending_order is not None:
            return
        target = target_for_signal(
            signal.direction,
            target_percent=self.p.target_percent,
            allow_short=self.p.allow_short,
            current_size=float(self.position.size),
        )
        if target is None:
            return
        self.pending_order = self.order_target_percent(target=target)
        self.last_signal = signal.direction

    def notify_order(self, order: Any) -> None:
        """Release the order lock after completion, cancellation, or rejection."""
        terminal_statuses = {
            order.Completed,
            order.Canceled,
            order.Margin,
            order.Rejected,
        }
        if order.status in terminal_statuses:
            self.pending_order = None
