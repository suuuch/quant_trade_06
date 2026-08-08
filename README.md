# quant-trade-06

RSI50 日线趋势信号扫描工具，支持 A 股和美股，并可将扫描结果发送到 QQ。

## 环境准备

项目使用 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/) 管理依赖。

```bash
uv sync
cp .env.example .env
```

在 `.env` 中配置 PostgreSQL 连接：

```dotenv
PG_HOST=127.0.0.1
PG_PORT=5432
PG_DB=eflab
PG_USER=admin
PG_PASSWORD=changeme
```

发送 QQ 消息时还需要配置：

```dotenv
QQBOT_APPID=your_app_id
QQBOT_SECRET=your_secret
QQBOT_OPENID=your_openid
QQBOT_GROUP_OPENID=your_group_openid
```

## 执行扫描

只生成信号和图片，不发送 QQ 消息：

```bash
# A 股（默认市场）
uv run python scripts/scan_rsi50_to_qq.py --market a

# 美股
uv run python scripts/scan_rsi50_to_qq.py --market us
```

美股扫描会先应用以下股票池条件：

- 股票代码以 `US.` 开头；
- 市值大于 10 亿美元；
- 最新收盘价大于 5 美元；
- 最近 50 个交易日平均成交额大于 1,000 万美元；
- 最近 50 个交易日成交额中位数大于 1,000 万美元；
- 每日成交额按 `收盘价 × 成交量` 计算。

通过股票池筛选后，美股与 A 股使用相同的 RSI50 技术形态条件。
美股不启用 MA20 的 40° 角度门槛，只要求多头 MA20 向上、空头 MA20 向下。

默认图片输出到 `reports/qq_signals/<扫描日期>/`。

## 发送到 QQ

发送给个人：

```bash
uv run python scripts/scan_rsi50_to_qq.py --market a --send
uv run python scripts/scan_rsi50_to_qq.py --market us --send
```

发送到群聊：

```bash
uv run python scripts/scan_rsi50_to_qq.py \
  --market us \
  --send \
  --target-type group
```

也可以使用 `--target-id` 临时指定接收方，而不读取环境变量中的 openid。

## 常用参数

```bash
# 只保留多头信号
uv run python scripts/scan_rsi50_to_qq.py --market us --direction long

# 只保留空头信号
uv run python scripts/scan_rsi50_to_qq.py --market us --direction short

# 限制发送数量并显示每个信号的详细信息
uv run python scripts/scan_rsi50_to_qq.py \
  --market us \
  --send \
  --max-send 10 \
  --verbose

# 查看全部参数
uv run python scripts/scan_rsi50_to_qq.py --help
```

## 验证代码

```bash
uv run pytest
uv run ruff check .
uv run pyright
```
