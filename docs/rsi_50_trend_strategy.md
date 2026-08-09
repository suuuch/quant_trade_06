# RSI 50 附近日线顺趋势交易

## 适用范围

本策略固定在日线周期执行，只记录 RSI 50 附近顺趋势交易相关规则。

## 指标参数

- RSI：RSI(14)
- 均线：MA20、MA30
- RSI 50 附近：45–55

## 多头信号

以下条件需要同时满足：

1. 最近 15 根 MA20 的回归斜率角度大于 40°，且 MA30 向上。
2. RSI 回落至 45–55。
3. RSI 同时位于 42–50（含边界）。
4. 从 T-5 到信号日 T 共 6 个 RSI(14) 全部位于 42–50（含边界）。

## 空头信号

以下条件需要同时满足：

1. 最近 15 根 MA20 的回归斜率角度小于 -40°，且 MA30 向下。
2. RSI 反弹至 45–55。
3. RSI 同时位于 45–55（含边界）。
4. 从 T-5 到信号日 T 共 6 个 RSI(14) 全部位于 50–58（含边界）。

## 核心逻辑

RSI 到达 50 附近不能单独触发交易。多头触发时 RSI 必须位于 45–55，且 T-5 至 T 全部位于 50–58；空头触发时 RSI 必须位于 42–50，且 T-5 至 T 全部位于 42–50。两种方向都必须同时得到均线趋势确认。

MA20 角度使用最近 15 个 MA20 值做线性回归。横轴每根 bar 记为 1，纵轴使用原始价格单位，回归斜率为 `k`，角度按 `degrees(atan(k))` 计算。多头要求角度严格大于 40°，空头要求角度严格小于 -40°。

## 当前参数

```text
timeframe = 1d
rsi_period = 14
ma_fast = 20
ma_slow = 30
rsi_zone_low = 45
rsi_zone_high = 55
long_trigger_rsi_low = 45
long_trigger_rsi_high = 55
short_trigger_rsi_low = 42
short_trigger_rsi_high = 50
recent_rsi_lookback = 5
long_recent_rsi_low = 50
long_recent_rsi_high = 58
short_recent_rsi_low = 42
short_recent_rsi_high = 50
ma_fast_angle_bars = 15
ma_fast_min_angle_degrees = 40
```
