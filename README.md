# quant-trade-06

RSI 顺势交易日线信号扫描工具。项目从 PostgreSQL 读取 A 股或美股行情，
筛选最新交易日信号，生成可逐只核对的 K 线图，并支持通过 QQ 群发送结果。

## 当前策略

所有条件均按已完成的日线计算。

RSI 条件：

- RSI 使用 RSI(14)，最新一天先筛 40–60；
- 多头最近 5 天 RSI 全部位于 50–58，窗口不能少于 5 天；
- 空头最近 5 天 RSI 全部位于 42–50，窗口不能少于 5 天；
- 不使用 W 底、M 顶、摆动点、颈线或价格突破条件。

A 股趋势条件：

- 多头：最近 15 个 MA20 值的拟合直线角度严格大于 40°；
- 空头：最近 15 个 MA20 值的拟合直线角度严格小于 -40°。

MA20 角度使用原始价格单位计算：横轴每根 Bar 记为 1，对最近 15 个
MA20 值线性拟合后，以 `degrees(atan(slope))` 转换为角度。

美股使用相同的方向化 RSI 条件，但不启用 40° 门槛，只判断 MA20 的
方向。扫描前还会限制股票代码以 `US.` 开头、市值大于 10 亿美元、最新
收盘价大于 5 美元，并要求最近 50 个交易日的平均及中位成交额均大于
5,000 万美元。

完整策略说明见 [docs/rsi_50_trend_strategy.md](docs/rsi_50_trend_strategy.md)。

## 独立 W/M 形态策略

W 底和 M 顶已经从 RSI 顺势交易策略中解耦，通过单独的扫描器运行，不叠加 RSI
或均线条件：

- 日线摆动点采用 3/3，需要等待右侧 3 根 K 线确认；
- 两个同类摆动点间距为 5–30 Bar；
- 两端价差不超过 1 ATR；
- 两端之间的反弹或回撤至少为 1 ATR；
- W 底入场：收盘价严格高于颈线加 0.1 ATR；
- M 顶入场：收盘价严格低于颈线减 0.1 ATR。

W/M 结果使用独立缓存和图片，图中会标出 P1、P2、颈线、入场 K 线、MA20、
MA30 和 RSI(14)。价格区 Y 轴使用对数坐标，RSI 子图保持 0–100 线性坐标。
W 底 Entry 标记放在信号 K 线最低价下方，M 顶 Entry 标记放在最高价上方，
避免覆盖 K 线。
均线及 RSI 仅供观察，不参与 W/M 筛选，也不会改变 RSI 顺势交易的扫描结果。

## 环境准备

项目使用 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/) 管理依赖。

```bash
uv sync
cp .env.example .env
```

在 `.env` 中配置 PostgreSQL：

```dotenv
PG_HOST=127.0.0.1
PG_PORT=5432
PG_DB=eflab
PG_USER=admin
PG_PASSWORD=changeme
```

需要 QQ 功能时继续配置：

```dotenv
QQBOT_APPID=your_app_id
QQBOT_SECRET=your_secret
QQBOT_OPENID=your_openid
QQBOT_GROUP_OPENID=your_group_openid
```

## 单次扫描

只扫描并生成图片，不发送 QQ：

```bash
# A 股
uv run python scripts/scan_rsi50_to_qq.py --market a

# 美股
uv run python scripts/scan_rsi50_to_qq.py --market us
```

图片默认写入 `reports/qq_signals/<扫描日期>/`。命中结果按总市值从大到小
排序，每张 QQ 合并图片默认包含 4 只股票。

交易日执行时，如果 `tushare.daily` 或 `tushare.adj_factor` 缺少当天数据，
程序会直接熔断。只有在明确需要扫描数据库最后交易日时才使用：

```bash
uv run python scripts/scan_rsi50_to_qq.py \
  --market a \
  --skip-freshness-check
```

## 单次发送到 QQ

发送全部命中结果到群聊，默认读取 `QQBOT_GROUP_OPENID`，图片消息之间等待
10 秒：

```bash
uv run python scripts/scan_rsi50_to_qq.py --market a --send
```

常用选项：

```bash
# 只发送多头或空头
uv run python scripts/scan_rsi50_to_qq.py --market a --send --direction long
uv run python scripts/scan_rsi50_to_qq.py --market a --send --direction short

# 临时指定群或个人
uv run python scripts/scan_rsi50_to_qq.py \
  --market a \
  --send \
  --target-id your_openid

uv run python scripts/scan_rsi50_to_qq.py \
  --market a \
  --send \
  --target-type c2c

# 查看所有参数
uv run python scripts/scan_rsi50_to_qq.py --help
```

`--max-send 0` 表示发送全部结果，也是默认值。`--max-send N` 才会限制发送
的股票数量。若最后交易日距当前日期超过 3 天，单次发送会拒绝执行；确认后
可添加 `--allow-stale-data`。

## 常驻 QQ 服务

常驻服务同时负责交易日扫描和 QQ 群指令监听：

```bash
uv run python scripts/run_a_share_qq_service.py
```

默认行为：

- 每天 18:00（Asia/Shanghai）开始检查；
- 非交易日跳过当天检查；
- 交易日每 5 分钟检查一次 `tushare.daily` 和 `tushare.adj_factor`；
- 两张表的最新日期都等于当天后，自动扫描并生成全部图片；
- 扫描结果写入 `reports/qq_signals/latest_delivery.json`，重启后可继续使用；
- 服务只准备结果，不会在扫描完成后主动刷屏，等待群内指令发送；
- 每条图片消息之间默认等待 10 秒。

QQ 群指令需要 `@机器人`。`QQBOT_OPENID` 对应的单聊可以直接发送相同
命令；其他单聊 OpenID 会被忽略。

RSI 顺势交易指令：

- `发送`：发送当前已经准备好的扫描结果；当天尚未完成扫描时返回状态；
- `发送历史`：发送缓存中的最后交易日结果，不检查今天是否为交易日；若没有
  缓存，则扫描数据库最后交易日并发送。

独立 W/M 指令：

| 命令 | 结果 |
| --- | --- |
| `发送A股W底` | A 股 W 底入场信号 |
| `发送A股M顶` | A 股 M 顶入场信号 |
| `发送A股WM` | A 股全部 W/M 入场信号 |
| `发送美股W底` | 美股 W 底入场信号 |
| `发送美股M顶` | 美股 M 顶入场信号 |
| `发送美股WM` | 美股全部 W/M 入场信号 |

在任一 W/M 命令中加入 `历史`，会发送数据库最后交易日的缓存，不检查今天
是否为交易日或今天的数据是否已经到齐。例如：

```text
发送历史A股W底
发送历史A股M顶
发送历史A股WM
发送历史美股W底
发送历史美股M顶
发送历史美股WM
```

如果对应缓存不存在，机器人会按数据库最后交易日重新扫描、生成缓存并发送。

省略市场关键字时默认 A 股，例如 `发送W底` 等价于 `发送A股W底`。
首次请求某个尚无缓存的市场时，机器人先回复“开始扫描”，完成扫描和绘图后
自动发送结果。后续请求直接发送对应缓存。

服务参数：

```bash
uv run python scripts/run_a_share_qq_service.py --help
```

## Supervisor 部署

仓库提供配置模板
[`deploy/supervisor/quant_trade_06.conf`](deploy/supervisor/quant_trade_06.conf)。
当前服务器部署目录为：

```text
/home/such/Documents/workspace/quant_trade_06
```

服务器上的 uv 路径为：

```text
/home/such/.local/bin/uv
```

安装或更新 Supervisor 配置后执行：

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status quant_trade_06
```

常用管理命令：

```bash
sudo supervisorctl start quant_trade_06
sudo supervisorctl stop quant_trade_06
sudo supervisorctl restart quant_trade_06
```

日志位于：

```text
logs/qq_service.log
logs/qq_service_error.log
```

## 验证代码

```bash
uv run pytest
uv run ruff check .
uv run pyright
```
