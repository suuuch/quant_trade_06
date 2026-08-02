"""backtrader strategy that consumes pre-computed Signal objects."""

from __future__ import annotations

from dataclasses import dataclass

import backtrader as bt

from .patterns.common import Signal


@dataclass
class _OpenPos:
    signal: Signal
    size: int
    entry_price: float


class SignalStrategy(bt.Strategy):
    """Stateful strategy that fires orders on pre-computed signals.

    Position sizing: fixed fraction of portfolio (default 10%) per signal.
    Exits: stop loss (price) and invalidation (price). No take-profit in v1.
    """

    params = (
        ("signals", None),        # list[Signal]
        ("position_pct", 0.10),   # fraction of portfolio value per entry
        ("printlog", False),
    )

    def __init__(self) -> None:
        self.signals_by_date: dict = {}
        for s in self.p.signals or []:
            d = s.triggered_at.date()
            self.signals_by_date.setdefault(d, []).append(s)
        self.open_positions: dict[str, _OpenPos] = {}
        self.trade_log: list[dict] = []
        self._order = None
        self._entry_bar: dict[str, int] = {}

    def log(self, msg: str) -> None:
        if self.p.printlog:
            print(msg)

    def next(self) -> None:
        if self._order is not None:
            return  # wait for pending order to settle

        data = self.datas[0]
        cur_date = data.datetime.date(0)
        cur_low = float(data.low[0])
        cur_high = float(data.high[0])

        # 1. Exits
        to_close: list[str] = []
        for sig_id, pos in self.open_positions.items():
            exit_price: float | None = None
            reason = ""
            if pos.signal.direction == "long":
                if cur_low <= pos.signal.stop_loss:
                    exit_price = pos.signal.stop_loss
                    reason = "stop_loss"
                elif cur_low <= pos.signal.invalidation_price:
                    exit_price = pos.signal.invalidation_price
                    reason = "invalidation"
            else:  # short
                if cur_high >= pos.signal.stop_loss:
                    exit_price = pos.signal.stop_loss
                    reason = "stop_loss"
                elif cur_high >= pos.signal.invalidation_price:
                    exit_price = pos.signal.invalidation_price
                    reason = "invalidation"
            if exit_price is not None:
                self._close(pos, exit_price, cur_date, reason)
                to_close.append(sig_id)
        for k in to_close:
            del self.open_positions[k]

        # 2. New entries
        for sig in self.signals_by_date.get(cur_date, []):
            sig_id = f"{sig.pattern}_{sig.direction}_{sig.triggered_at.strftime('%Y%m%d')}"
            if sig_id in self.open_positions:
                continue
            price = sig.trigger_price
            if price <= 0:
                continue
            size = max(1, int((self.broker.getvalue() * self.p.position_pct) / price))
            if sig.direction == "long":
                self._order = self.buy(size=size, price=price)
            else:
                self._order = self.sell(size=size, price=price)
            self.open_positions[sig_id] = _OpenPos(signal=sig, size=size, entry_price=price)
            self._entry_bar[sig_id] = len(self)
            self.log(f"ENTRY {sig.direction} {sig.pattern} {sig.symbol} @ {price:.2f} size={size}")

    def notify_order(self, order) -> None:
        if order.status in (order.Completed,):
            self._order = None
        elif order.status in (order.Canceled, order.Rejected, order.Margin):
            self.log(f"ORDER {order.getstatusname()}")
            self._order = None

    def stop(self) -> None:
        """Force-close any open position at the last bar close."""
        if self._order is not None:
            return
        for sig_id, pos in list(self.open_positions.items()):
            data = self.datas[0]
            price = float(data.close[0])
            self._close(pos, price, data.datetime.date(0), "end_of_data")
            del self.open_positions[sig_id]

    def _close(self, pos: _OpenPos, price: float, date, reason: str) -> None:
        if pos.signal.direction == "long":
            self._order = self.sell(size=pos.size, price=price)
        else:
            self._order = self.buy(size=pos.size, price=price)
        pnl = (price - pos.entry_price) * pos.size * (1 if pos.signal.direction == "long" else -1)
        self.trade_log.append(
            {
                "symbol": pos.signal.symbol,
                "pattern": pos.signal.pattern,
                "direction": pos.signal.direction,
                "entry_date": pos.signal.triggered_at.strftime("%Y-%m-%d"),
                "entry_price": pos.entry_price,
                "exit_date": date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date),
                "exit_price": price,
                "size": pos.size,
                "pnl": pnl,
                "reason": reason,
            }
        )
        self.log(f"EXIT  {pos.signal.direction} {pos.signal.pattern} @ {price:.2f} pnl={pnl:.2f} reason={reason}")
