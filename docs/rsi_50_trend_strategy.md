# RSI 顺势交易

## 适用范围

本策略固定在日线周期执行，只记录 RSI 顺势交易筛选规则。
W/M 形态、价格突破等其他条件不参与本策略筛选。

## 指标参数

- RSI：RSI(14)
- 均线：MA20、MA30
- 最新一天 RSI 总筛：42–58
- 方向 RSI 窗口：最近 5 天，不能少于 5 天

## 多头信号

以下条件需要同时满足：

1. 最新一天 RSI 位于 42–58（含边界）。
2. MA20 或 MA30 过去 10 天平均每天上涨大于 0.3%。
3. 最近 5 天 RSI(14) 全部位于 50–58（含边界）。

## 空头信号

以下条件需要同时满足：

1. 最新一天 RSI 位于 42–58（含边界）。
2. MA20 或 MA30 过去 10 天平均每天下跌大于 0.3%。
3. 最近 5 天 RSI(14) 全部位于 42–50（含边界）。

## 核心逻辑

筛选先看最新一天 RSI 是否处于 42–58，再判断 MA20 或 MA30 过去 10 天的平均日涨跌幅，最后判断最近 5 天 RSI 是否持续位于对应方向区间。多头要求最近 5 天 RSI 全部位于 50–58；空头要求最近 5 天 RSI 全部位于 42–50。W/M 形态、价格突破、颈线等条件均不参与 RSI 顺势交易筛选。

MA20/MA30 斜率各自使用最近 11 个均线值计算 10 个交易日收益率，再取算术平均。日收益率按 `(MA_t - MA_{t-1}) / MA_{t-1}` 计算。多头要求 MA20 或 MA30 的均值严格大于 0.3%，空头要求严格小于 -0.3%。

## 当前参数

```text
timeframe = 1d
rsi_period = 14
ma_fast = 20
rsi_zone_low = 40
rsi_zone_high = 60
long_trigger_rsi_low = 50
long_trigger_rsi_high = 65
short_trigger_rsi_low = 35
short_trigger_rsi_high = 50
recent_rsi_days = 5
ma_fast_slope_days = 10
ma_fast_min_daily_return = 0.003
```
