# RSI 顺势交易

## 适用范围

本策略固定在日线周期执行，只记录 RSI 顺势交易筛选规则。
W/M 形态、MA30、价格突破等其他条件不参与本策略筛选。

## 指标参数

- RSI：RSI(14)
- 均线：MA20
- 最新一天 RSI 总筛：40–60
- 方向 RSI 窗口：最近 5 天，不能少于 5 天

## 多头信号

以下条件需要同时满足：

1. 最新一天 RSI 位于 40–60（含边界）。
2. 最近 15 根 MA20 的回归斜率角度大于 20°。
3. 最近 5 天 RSI(14) 全部位于 50–58（含边界）。

## 空头信号

以下条件需要同时满足：

1. 最新一天 RSI 位于 40–60（含边界）。
2. 最近 15 根 MA20 的回归斜率角度小于 -20°。
3. 最近 5 天 RSI(14) 全部位于 42–50（含边界）。

## 核心逻辑

筛选先看最新一天 RSI 是否处于 40–60，再判断 MA20 趋势角度，最后判断最近 5 天 RSI 是否持续位于对应方向区间。多头要求最近 5 天 RSI 全部位于 50–58；空头要求最近 5 天 RSI 全部位于 42–50。W/M 形态、MA30、价格突破、颈线等条件均不参与 RSI 顺势交易筛选。

MA20 角度使用最近 15 个 MA20 值做线性回归。横轴每根 bar 记为 1，纵轴使用原始价格单位，回归斜率为 `k`，角度按 `degrees(atan(k))` 计算。多头要求角度严格大于 20°，空头要求角度严格小于 -20°。

## 当前参数

```text
timeframe = 1d
rsi_period = 14
ma_fast = 20
rsi_zone_low = 40
rsi_zone_high = 60
long_trigger_rsi_low = 50
long_trigger_rsi_high = 58
short_trigger_rsi_low = 42
short_trigger_rsi_high = 50
recent_rsi_days = 5
ma_fast_angle_bars = 15
ma_fast_min_angle_degrees = 20
```
