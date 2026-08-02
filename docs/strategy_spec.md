# 量化策略实现规格（Strategy Spec）

> 本文档由 `docs/strategy.md` 整理而来，用于直接落地为可回测代码。
> 实施前请逐条 review；任何 TODO 标记的条目需先与用户确认。

---

## 0. 范围

| 项 | 取值 |
| --- | --- |
| 目标市场 | A 股 |
| 实施信号 | S1 高位 M 顶做空 / S2 低位 W 底做多 / S3 RSI 50 顺趋势 |
| 暂不实施 | RSI<40 续跌/反转、横盘续跌、双周期入场、三波衰竭（保留接口位） |
| 数据频率 | 默认日线（`frequency=daily`）；可切换小时线（`frequency=hourly`） |
| 技术栈 | Python 3.11+ / pandas / numpy；回测框架二选一：backtrader（推荐）、vectorbt |
| 数据源 | 抽象 `DataSource` 接口，先不绑定具体源 |

---

## 1. 术语表

| 术语 | 定义 |
| --- | --- |
| OHLCV | Open/High/Low/Close/Volume 时间序列，按 `frequency` 升序排列 |
| Pivot High（确认高点） | 在 `i` 处的 High 满足特定左右比较 + prominence 条件的局部最高点（见 §3.1） |
| Pivot Low（确认低点） | 与 Pivot High 对称定义在 Low 上 |
| Prominence | 当前极值点相对邻域最低/最高点的“凸出”幅度；M 顶用 `high - max(left_low, right_low)` |
| 颈线（neckline） | M 顶 = `min(low[H1_idx : H2_idx + 1])`；W 底 = `max(high[L1_idx : L2_idx + 1])` |
| H1/H2 | M 顶中两个确认高点，按时间先后；H2 在 H1 之后 |
| L1/L2 | W 底中两个确认低点，按时间先后；L2 在 L1 之后 |
| 顶背离 | H2 价格 ≥ H1（高或相近），但 H2 处 RSI < H1 处 RSI |
| 底背离 | L2 价格 ≤ L1（低或相近），但 L2 处 RSI > L1 处 RSI |
| 摆动区间 | H1 与 H2 之间（不含端点）的 K 线集合 |
| 中间回撤 | M 顶：`min(H1, H2) - middle_low`；W 底：`middle_high - max(L1, L2)` |
| Break Bar | 触发“跌破/突破”条件的那根 K 线 |

---

## 2. 通用指标

所有指标均按 `close` 序列计算，参数对每个标的独立生效。

| 指标 | 公式 | 默认参数 | 来源 |
| --- | --- | --- | --- |
| MA(N) | 简单移动平均：`close.rolling(N).mean()` | 20, 30 | 文档 §0/§1/§2 |
| RSI(N) | Wilder 平滑 RSI | 14 | 文档 §0/§1/§2/§3 |
| ATR(N) | Wilder 平滑 ATR（TR = max(H-L, |H-prev_close|, |L-prev_close|)） | 14 | 文档 §0/§1/§2 |
| MA slope | `MA[i] - MA[i - k]`，k 默认 3 | k=3 | 文档 §3 用“MA 20/30 斜率向上/向下” |
| 20 周期均量 | `volume.rolling(20).mean()` | 20 | 文档 §0/§1/§2 |

> TODO（实施时定）：MA 与 ATR 全部统一为 SMA / Wilder 平滑，不混用。

---

## 3. 通用工具

### 3.1 Pivot 检测（带 Prominence）

频率自适应的左右窗口：

| frequency | pivot_left | pivot_right | 启用 prominence |
| --- | --- | --- | --- |
| daily | 3 | 3 | 可选（默认关闭） |
| hourly | 5 | 5 | 启用（阈值 0.8 × ATR(14)） |

`is_pivot_high(i)`：

```python
is_pivot_high = (
    high[i] > max(high[i - L : i])
    and high[i] >= max(high[i + 1 : i + R + 1])
)
left_low  = min(low[i - L : i + 1])
right_low = min(low[i : i + R + 1])
prominence = high[i] - max(left_low, right_low)

confirmed = is_pivot_high and (prominence >= 0.8 * atr[i] if use_prominence else True)
```

`is_pivot_low(i)` 镜像实现（low < 邻域 + prominence ≥ 0.8 × ATR）。

### 3.2 颈线（neckline）

```python
# M 顶
neckline_short = min(low[H1_idx : H2_idx + 1])

# W 底
neckline_long = max(high[L1_idx : L2_idx + 1])
```

### 3.3 跌破 / 突破 确认

按文档 §“何时确认跌破”：

| 等级 | 条件 |
| --- | --- |
| 宽松 | `close < neckline - 0.1 × ATR(14)` |
| 标准（默认） | 连续 2 根 K 线 `close < neckline - 0.1 × ATR(14)` |
| 严格 | 1 根 K 线 `close < neckline - 0.1 × ATR(14)` **且** `volume > 20 周期均量` |

> 多头方向（W 底/顺趋势多头）用对称条件：`close > neckline + 0.1 × ATR(14)`。

### 3.4 失效条件（M 顶做空）

```python
# 收盘价重新站上 max(H1, H2) + 0.3 × ATR(14)
invalid_short = close[i] > max(H1, H2) + 0.3 * atr[i]
```

W 底镜像：`close[i] < min(L1, L2) - 0.3 * atr[i]`。

---

## 4. 信号 S1：高位 M 顶做空

### 4.1 形态发现（Pattern Detection）

输入：单标的 OHLCV + 已计算的 MA20/MA30/RSI(14)/ATR(14)。

输出：候选 M 顶列表，每条记录：

```python
MTopCandidate = {
    "h1_idx": int, "h2_idx": int,
    "h1_price": float, "h2_price": float,
    "h1_rsi": float, "h2_rsi": float,
    "neckline": float,                 # = min(low[h1_idx:h2_idx+1])
    "atr_at_h2": float,                # ATR(14)[h2_idx]
    "middle_pullback": float,          # = min(H1,H2) - neckline
    "candidate": bool,                 # 候选条件是否同时满足
    "confirmed_short": bool,           # 是否已触发做空
    "invalidated": bool,
    "short_entry_idx": Optional[int],
    "short_entry_price": Optional[float],
}
```

### 4.2 完整规则（按文档“完整 M 顶空头规则”编号）

| # | 条件 | 备注 |
| --- | --- | --- |
| 1 | `h1 = confirmed pivot high` | 见 §3.1 |
| 2 | `h2 = 在 h1 之后 10–60 根 K 线内的 confirmed pivot high` | 日线默认 5–30，小时线 10–60；以所选 frequency 的参数为准 |
| 3 | `abs(h1_price - h2_price) <= 1.0 × ATR(14)[h2_idx]` | 顶高差 |
| 4 | `min(h1_price, h2_price) - neckline >= 1.2 × ATR(14)[h2_idx]` | 中间回撤；小时线用 1.2，日线 ≥1.0 |
| 5 | `RSI(14) 在 h1 或 h2 附近（±3 根）曾处于 [75, 85]` | “RSI 最近进入 75–85”的具体化 |
| 6 | `h2_rsi < h1_rsi`（顶背离） | 优先级提示，不阻塞触发 |
| 7 | `close[break_idx] < neckline - 0.1 × ATR(14)` | break_idx 必须晚于 h2_idx |
| 8 | `MA20 向下（slope<0）` **或** `close < MA20` | 二选一 |

全部满足 → 触发做空信号，`short_entry_idx = break_idx`。

### 4.3 触发逻辑

1. 扫描到候选 → 状态置 `candidate=True`，开始监听颈线跌破。
2. 跌破触发 → `confirmed_short=True`，记录 entry。
3. 后续每根 K 线检查失效（§3.4），命中 → `invalidated=True`，信号结束。

### 4.4 风控

- 止损：`h2_price + 0.5 × ATR(14)[h2_idx]`（文档“第二个顶部上方 0.5 ATR”）
- 止盈：未指定 → TODO。建议第一版**不设硬止盈**，靠失效条件/形态破坏出场；后续可加 `R 倍 ATR` 跟踪止盈。

---

## 5. 信号 S2：低位 W 底做多

### 5.1 形态发现

输出：

```python
WBottomCandidate = {
    "l1_idx": int, "l2_idx": int,
    "l1_price": float, "l2_price": float,
    "l1_rsi": float, "l2_rsi": float,
    "neckline": float,                 # = max(high[l1_idx:l2_idx+1])
    "atr_at_l2": float,
    "middle_pullback": float,          # = neckline - max(L1,L2)
    "candidate": bool,
    "confirmed_long": bool,
    "invalidated": bool,
    "long_entry_idx": Optional[int],
    "long_entry_price": Optional[float],
}
```

### 5.2 完整规则（与 S1 镜像）

| # | 条件 | 备注 |
| --- | --- | --- |
| 1 | `l1 = confirmed pivot low` | |
| 2 | `l2 = 在 l1 之后 10–60 根 K 线内的 confirmed pivot low` | |
| 3 | `abs(l1_price - l2_price) <= 1.0 × ATR(14)[l2_idx]` | |
| 4 | `neckline - max(l1_price, l2_price) >= 1.2 × ATR(14)[l2_idx]` | |
| 5 | `RSI(14) 在 l1 或 l2 附近（±3 根）曾处于 [15, 25]` | |
| 6 | **【保留原文】** “第二高点对应 RSI 低于第一高点” | 文档原文如此；与底背离定义矛盾，实施时按用户指示**保持原文不动**，可能产生不触发或反向触发，需在回测中复核 |
| 7 | `close[break_idx] > neckline + 0.1 × ATR(14)` | |
| 8 | `MA20 向上（slope>0）` **或** `close > MA20` | |

### 5.3 触发 / 失效

- 触发：跌破/突破条件用 §3.3 多头版本
- 失效：`close < min(l1, l2) - 0.3 × ATR(14)`

### 5.4 风控

- 止损：`l2_price - 0.5 × ATR(14)[l2_idx]`

---

## 6. 信号 S3：RSI 50 顺趋势

### 6.1 触发条件

RSI 50 区域定义为 `[45, 55]`（文档要求“避免模糊表达”）。

#### 6.1.1 多头

| # | 条件 |
| --- | --- |
| A | `slope(MA20) > 0` 且 `slope(MA30) > 0` |
| B | `RSI(14) ∈ [45, 55]` |
| C | 价格形态 = W 底 / 更高低点（higher low）—— W 底复用 S2 的形态检测；higher low 简化为 `low[i] > low[lowest in last N bars]`，N 默认 20 |
| D | `close 突破 W 底颈线`（用 §3.3 多头版本） |
| E | `RSI(14) 同时重新站上 55`（与 D 同 bar 或 D 之后 1 根内） |

A ∧ B ∧ (C ∧ D) ∧ E → 触发做多。

#### 6.1.2 空头

| # | 条件 |
| --- | --- |
| A | `slope(MA20) < 0` 且 `slope(MA30) < 0` |
| B | `RSI(14) ∈ [45, 55]` |
| C | 价格形态 = M 顶 / 更低高点（lower high）—— M 顶复用 S1；lower high 简化为 `high[i] < high[highest in last N bars]` |
| D | `close 跌破 M 顶颈线`（用 §3.3 空头版本） |
| E | `RSI(14) 同时跌破 45`（与 D 同 bar 或 D 之后 1 根内） |

A ∧ B ∧ (C ∧ D) ∧ E → 触发做空。

### 6.2 风控

- 沿用 S1/S2 颈线 + ATR 止损结构
- “RSI 同时”要求：默认在 D 触发那根 K 线的收盘判断；放宽则允许 D 后 1 根 K 线内命中

---

## 7. 信号输出 Schema

`Signal` 记录（与文档“信号出现”卡片对齐）：

```python
@dataclass
class Signal:
    symbol: str
    frequency: str                # "daily" | "hourly"
    direction: str                # "long" | "short" | "watch_long" | "watch_short"
    pattern: str                  # "M_top" | "W_bottom" | "RSI50_trend"
    triggered_at: pd.Timestamp
    trigger_price: float
    daily_context: Optional[str]  # "底背离" / "顶背离" / None（双周期预留字段，单周期置 None）
    hourly_shape: Optional[str]   # "W底已突破" / "M顶已跌破" / None
    rsi_at_trigger: float
    invalidation_price: float
    stop_loss: float
    notes: str = ""
```

> 当前实施为单周期，`daily_context` / `hourly_shape` 保留为 `None`。

---

## 8. 数据输入接口

```python
class DataSource(Protocol):
    def load_ohlcv(self, symbol: str, frequency: str,
                   start: str, end: str) -> pd.DataFrame:
        """返回包含列 [open, high, low, close, volume] 的 DataFrame，
        索引为 pandas.DatetimeIndex，按时间升序。"""
```

第一版可实现 `CsvDataSource`（读本地文件）和 `AkShareDataSource`（在线拉取）。具体实现在 PR 阶段确定。

---

## 9. 参数总表

所有可调参数集中在 `config.yaml`（或 `StrategyParams` dataclass）：

```yaml
frequency: daily              # daily | hourly
indicators:
  rsi_period: 14
  atr_period: 14
  ma_fast: 20
  ma_slow: 30
  vol_ma: 20
  ma_slope_lookback: 3

pivot:
  daily:    { left: 3, right: 3, use_prominence: false }
  hourly:   { left: 5, right: 5, use_prominence: true,  prominence_atr: 0.8 }

pattern:
  # M 顶 / W 底 通用
  min_top_distance: 5         # daily; hourly: 10
  max_top_distance: 30        # daily; hourly: 60
  max_top_difference_atr: 1.0
  min_middle_pullback_atr: 1.0 # daily; hourly: 1.2
  # 颈线/确认
  break_buffer_atr: 0.1
  break_confirm: standard      # loose | standard | strict
  # 失效
  invalidation_buffer_atr: 0.3
  # 止损
  stop_loss_buffer_atr: 0.5

rsi50:
  zone: [45, 55]
  rsi_required_long: 55
  rsi_required_short: 45
  higher_low_lookback: 20
```

---

## 10. 框架映射

### 10.1 backtrader（推荐）

- `bt.Strategy` 子类持有一个 `SignalEngine`，按 bar 推进
- 每个候选形态 = 一个 `PatternState` 对象，状态机：`watching → entry_ready → entered → invalidated`
- 订单：`self.sell(exectype=bt.Order.Market)` 触发做空；止损用 `BracketOrder` 或 `sell(price=stop, exectype=bt.Order.Stop)`
- 评价指标：内置 `bt.analyzers`（Sharpe、DrawDown、TradeAnalyzer）

### 10.2 vectorbt（备选）

- 把 S1/S2/S3 各自转成 `entries` / `exits` 布尔数组
- 优势：参数扫描极快；劣势：状态机（candidate → entry）写起来繁琐，建议把 S1/S2 的形态事件预计算为布尔序列再喂给 vbt
- 适合“网格调参”，不适合表达复杂中间态

> 建议：先用 backtrader 落地，验证信号正确性；需要扫参再补一份 vbt 接口。

---

## 11. 待澄清 TODO

| 编号 | 主题 | 现状 | 建议处理 |
| --- | --- | --- | --- |
| T1 | W 底“第二高点对应 RSI 低于第一高点” | 文档原文保留 | 回测时观察是否反向触发，决定是否修正 |
| T2 | 完整 M 顶规则 5 用 `RSI ≥ 70`，候选条件用 `[75, 85]` | 数值不一致 | 本规格统一用 `[75, 85]`（更严） |
| T3 | 止盈规则 | 文档未给 | 第一版不设硬止盈，靠失效出场；后续补 `R × ATR` |
| T4 | “RSI 同时重新站上 55”的容忍窗口 | 未指定 | 默认 D 当根；可放宽到 D+1 |
| T5 | higher low / lower high 的“最近 N 根” | 未指定 | 默认 20 |
| T6 | MA20 斜率“向上/向下”的具体度量 | 未指定 | `MA[i] - MA[i-3]`，阈值 0（严格方向） |
| T7 | “突破 K 线成交量高于 20 周期均量” | S1/S2 写为 or，S3 未提 | S1/S2 保留为 or；S3 默认不要求 |
| T8 | 数据源 | 未定 | 先实现 `CsvDataSource` + `DataSource` 抽象 |

---

## 12. 建议文件布局

```
quant_trade_06/
├── docs/
│   ├── strategy.md            # 原始笔记（不动）
│   └── strategy_spec.md       # 本文件
├── src/
│   ├── config.py              # 参数 dataclass + yaml loader
│   ├── data_source.py         # DataSource 抽象 + CsvDataSource
│   ├── indicators.py          # MA / RSI / ATR / slope
│   ├── pivots.py              # §3.1 pivot 检测（含 prominence）
│   ├── neckline.py            # §3.2/3.3 颈线 + 跌破/突破
│   ├── patterns/
│   │   ├── m_top.py           # S1
│   │   ├── w_bottom.py        # S2
│   │   └── rsi50_trend.py     # S3
│   ├── engine.py              # 把三信号合成 Signal 列表
│   ├── strategy_bt.py         # backtrader Strategy
│   └── main.py                # 入口：取数 → 跑策略 → 输出报告
├── tests/
│   ├── test_indicators.py
│   ├── test_pivots.py
│   ├── test_m_top.py
│   ├── test_w_bottom.py
│   └── test_rsi50.py
└── README.md
```

---

## 13. 实施顺序

1. `config.py` + `indicators.py` + `pivots.py` + `neckline.py`（基础工具，附单元测试）
2. `m_top.py`：用一两个手工构造的 K 线序列验证端到端触发
3. `w_bottom.py`：镜像实现
4. `rsi50_trend.py`：复用 S1/S2 的颈线逻辑
5. `engine.py` → `strategy_bt.py` → 拉一份 A 股样本数据回测
6. 把回测结果贴回本文件 §11 表格，闭环 TODO
