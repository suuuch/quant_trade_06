"""RSI trend-following strategy package."""

from quant_trade.qq_bot import (
    QQBotClient,
    QQBotError,
    send_qq_bot_message,
    send_qq_group_message,
)
from quant_trade.rsi50 import (
    Bar,
    Direction,
    Rsi50Config,
    Rsi50SignalEngine,
    Signal,
)

__all__ = [
    "Bar",
    "Direction",
    "Rsi50Config",
    "Rsi50SignalEngine",
    "QQBotClient",
    "QQBotError",
    "Signal",
    "send_qq_bot_message",
    "send_qq_group_message",
]
