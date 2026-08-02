# quant_trade_06

A-share quantitative trading strategy — M-top short / W-bottom long / RSI50
trend-following. See [`docs/strategy_spec.md`](docs/strategy_spec.md) for the
full rule spec and [`docs/strategy.md`](docs/strategy.md) for the source notes.

## v1 status

Implements S1 (M-top short), S2 (W-bottom long), S3 (RSI-50 trend) per the
spec. Single-frequency (daily). PostgreSQL → backtrader pipeline end-to-end.

## Quickstart

```bash
# Install
uv sync

# Configure DB (one-time)
cp .env.example .env
# edit .env with your PG_HOST/PG_USER/PG_PASSWORD/PG_DB

# Run a single-symbol backtest
uv run python -m quant_trade.main --symbol 000001.SZ --start 20240101 --end 20260720

# Run a multi-symbol batch
uv run python -m quant_trade.main \
  --symbols 000001.SZ,600519.SH,300750.SZ \
  --start 20240101 --end 20260720

# Tests
uv run pytest -q
```

## Data source

Pulls from `tushare.daily` on the configured PostgreSQL. Required columns
(`trade_date`, `open`, `high`, `low`, `close`, `vol`) match the standard
Tushare schema. Symbol format is `ts_code` (e.g. `000001.SZ`, `600519.SH`).

To swap in a different source, implement the `DataSource` protocol in
`src/quant_trade/data_source.py` and pass it to `signals_for_symbol`.

## Layout

```
src/quant_trade/
├── config.py            # StrategyParams (pivot, pattern, rsi50, indicators)
├── data_source.py       # DataSource protocol + PostgresDataSource + CsvDataSource
├── indicators.py        # MA / Wilder RSI / Wilder ATR / slope / add_indicators
├── pivots.py            # pivot high/low with optional prominence
├── neckline.py          # neckline + break/above/below + invalidation
├── engine.py            # run all detectors on one symbol
├── strategy_bt.py       # backtrader strategy (consumes pre-computed signals)
├── main.py              # CLI entry
└── patterns/
    ├── common.py        # Signal dataclass + helpers
    ├── m_top.py         # S1 M-top short
    ├── w_bottom.py      # S2 W-bottom long
    └── rsi50_trend.py   # S3 RSI 50 area trend-following
tests/                    # pytest unit tests
docs/                     # strategy.md (notes) + strategy_spec.md (spec)
scripts/                  # one-off DB explorers / smoke tests
```

## Output

For each symbol the CLI prints:

- detected signals (`pattern`, `direction`, entry, stop, invalidation, RSI)
- final portfolio value, Sharpe, max drawdown
- per-trade log (entry/exit price, size, PnL, exit reason)

Position sizing: fixed fraction of portfolio (default 10%) per signal.
Exits: stop loss at H2 ± 0.5 ATR and pattern invalidation at max(H1,H2) ± 0.3 ATR.
No take-profit in v1 — exits are stop/invalidation/end-of-data.

## Known TODOs (from spec §11)

- W-bottom rule 6 keeps the original (logically inverted) text per user
  instruction. Watch the backtest for spurious filtering or non-filtering.
- No take-profit; first version exits only on stop / invalidation / EOD.
- Single-frequency only (daily); hourly needs an hourly OHLCV source.
- Backtest is one-symbol-at-a-time. Multi-symbol portfolio aggregation is
  left for a follow-up.

## Dev

```bash
uv run ruff check src tests
uv run ruff format src tests
uv run pyright src
uv run pytest -q
```
