"""RSI trend-following strategy package."""

from quant_trade.bars import Bar
from quant_trade.models import (
    Direction,
    PatternSignal,
    RsiTrendSignal,
    WmNecklineSignal,
)
from quant_trade.qq_bot import (
    QQBotClient,
    QQBotError,
    send_qq_bot_message,
    send_qq_group_message,
)
from quant_trade.rsi50 import (
    Rsi50Config,
    Rsi50SignalEngine,
    Signal,
)

__all__ = [
    "Bar",
    "Direction",
    "PatternSignal",
    "Rsi50Config",
    "Rsi50SignalEngine",
    "RsiTrendSignal",
    "QQBotClient",
    "QQBotError",
    "Signal",
    "WmNecklineSignal",
    "send_qq_bot_message",
    "send_qq_group_message",
]
