"""Strategy parameters, matching docs/strategy_spec.md §9.

Defaults target daily frequency on A-shares.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IndicatorParams:
    rsi_period: int = 14
    atr_period: int = 14
    ma_fast: int = 20
    ma_slow: int = 30
    vol_ma: int = 20
    ma_slope_lookback: int = 3


@dataclass(frozen=True)
class PivotParams:
    left: int = 3
    right: int = 3
    use_prominence: bool = False
    prominence_atr: float = 0.8  # only used when use_prominence is True


@dataclass(frozen=True)
class PatternParams:
    min_top_distance: int = 5
    max_top_distance: int = 30
    max_top_difference_atr: float = 1.0
    min_middle_pullback_atr: float = 1.0
    break_buffer_atr: float = 0.1
    break_confirm: str = "standard"  # "loose" | "standard" | "strict"
    invalidation_buffer_atr: float = 0.3
    stop_loss_buffer_atr: float = 0.5
    rsi_lookback: int = 3  # ±K bars around pivot for RSI band check


@dataclass(frozen=True)
class Rsi50Params:
    zone: tuple[int, int] = (45, 55)
    rsi_required_long: int = 55
    rsi_required_short: int = 45
    higher_low_lookback: int = 20
    rsi_confirm_window: int = 1  # max bars after neckline break for RSI to cross


@dataclass(frozen=True)
class StrategyParams:
    frequency: str = "daily"
    indicators: IndicatorParams = field(default_factory=IndicatorParams)
    pivot: PivotParams = field(default_factory=PivotParams)
    pattern: PatternParams = field(default_factory=PatternParams)
    rsi50: Rsi50Params = field(default_factory=Rsi50Params)

    def for_hourly(self) -> StrategyParams:
        """Return a copy with hourly defaults (pivot 5/5 + prominence 0.8 ATR)."""
        return StrategyParams(
            frequency="hourly",
            indicators=self.indicators,
            pivot=PivotParams(left=5, right=5, use_prominence=True, prominence_atr=0.8),
            pattern=PatternParams(
                min_top_distance=10,
                max_top_distance=60,
                max_top_difference_atr=1.0,
                min_middle_pullback_atr=1.2,
                break_buffer_atr=self.pattern.break_buffer_atr,
                break_confirm=self.pattern.break_confirm,
                invalidation_buffer_atr=self.pattern.invalidation_buffer_atr,
                stop_loss_buffer_atr=self.pattern.stop_loss_buffer_atr,
                rsi_lookback=self.pattern.rsi_lookback,
            ),
            rsi50=self.rsi50,
        )
