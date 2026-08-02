"""HTML trade review report (Plotly embedded in a single .html file).

For each closed trade we render a candlestick + indicator chart spanning a
configurable window around entry, with horizontal lines for entry / stop /
invalidation / exit and pivot markers. A summary table at the top links to
each detail section.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .config import PivotParams, StrategyParams
from .indicators import add_indicators
from .pivots import find_pivot_highs, find_pivot_lows, pivot_indices


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add the technical indicator columns needed for the chart overlay."""
    return add_indicators(df, StrategyParams().indicators)


def _window(df: pd.DataFrame, entry_date: pd.Timestamp, bars: int) -> pd.DataFrame:
    """Return ±bars rows around the entry date (inclusive of entry)."""
    target = pd.DatetimeIndex([entry_date])
    idx = df.index.get_indexer(target, method="nearest")[0]
    lo = max(0, idx - bars)
    hi = min(len(df), idx + bars + 1)
    return df.iloc[lo:hi]


def _summary_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    return {
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades),
        "total_pnl": sum(pnls),
        "avg_pnl": sum(pnls) / len(trades),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": pf,
    }


def _trade_chart(
    trade: dict,
    df: pd.DataFrame,
    enriched: pd.DataFrame,
    pivot_params: PivotParams,
) -> go.Figure:
    """Build the per-trade candlestick + indicator chart."""
    entry_date = pd.Timestamp(trade["entry_date"])
    win = _window(df, entry_date, bars=30)
    win_enr = enriched.loc[win.index]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.72, 0.28],
        subplot_titles=("Price", "RSI(14)"),
    )

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=win.index,
            open=win["open"],
            high=win["high"],
            low=win["low"],
            close=win["close"],
            name="OHLC",
            increasing_line_color="#ef5350",
            decreasing_line_color="#26a69a",
        ),
        row=1,
        col=1,
    )

    # MA overlays
    for col, color, name in [
        ("ma_fast", "#ffa726", "MA20"),
        ("ma_slow", "#42a5f5", "MA30"),
    ]:
        fig.add_trace(
            go.Scatter(
                x=win_enr.index,
                y=win_enr[col],
                mode="lines",
                name=name,
                line=dict(color=color, width=1.2),
            ),
            row=1,
            col=1,
        )

    # Pivot markers on the window
    full_pivots_hi = find_pivot_highs(enriched, pivot_params)
    full_pivots_lo = find_pivot_lows(enriched, pivot_params)
    hi_idx = [i for i in pivot_indices(full_pivots_hi) if enriched.index[i] in win.index]
    lo_idx = [i for i in pivot_indices(full_pivots_lo) if enriched.index[i] in win.index]
    if hi_idx:
        fig.add_trace(
            go.Scatter(
                x=[enriched.index[i] for i in hi_idx],
                y=[enriched["high"].iloc[i] for i in hi_idx],
                mode="markers",
                marker=dict(symbol="triangle-down", size=10, color="#ab47bc"),
                name="pivot high",
            ),
            row=1,
            col=1,
        )
    if lo_idx:
        fig.add_trace(
            go.Scatter(
                x=[enriched.index[i] for i in lo_idx],
                y=[enriched["low"].iloc[i] for i in lo_idx],
                mode="markers",
                marker=dict(symbol="triangle-up", size=10, color="#ab47bc"),
                name="pivot low",
            ),
            row=1,
            col=1,
        )

    # Horizontal price levels: entry, stop, invalidation, exit
    stop = trade.get("stop_loss")
    if stop is None:
        stop = trade.get("invalidation_price")
    levels = [
        (trade["entry_price"], "#1e88e5", f"entry {trade['entry_price']:.2f}", "dash"),
        (stop, "#e53935", None, "dot"),
        (trade.get("invalidation_price"), "#8e24aa", None, "dot"),
        (trade["exit_price"], "#43a047", f"exit {trade['exit_price']:.2f}", "dash"),
    ]
    for price, color, label, dash in levels:
        if price is None:
            continue
        fig.add_hline(
            y=price,
            line=dict(color=color, width=1, dash=dash),
            annotation_text=label or f"{price:.2f}",
            annotation_position="right",
            annotation_font=dict(size=10, color=color),
            row=1,  # type: ignore[arg-type]
            col=1,  # type: ignore[arg-type]
        )

    # Entry / exit markers
    direction = trade["direction"]
    entry_sym = "triangle-up" if direction == "long" else "triangle-down"
    exit_sym = "triangle-down" if direction == "long" else "triangle-up"
    entry_color = "#1e88e5"
    reason = str(trade.get("reason", ""))
    exit_color = {"stop_loss": "#e53935", "invalidation": "#8e24aa"}.get(
        reason, "#43a047"
    )
    fig.add_trace(
        go.Scatter(
            x=[entry_date],
            y=[trade["entry_price"]],
            mode="markers+text",
            marker=dict(symbol=entry_sym, size=14, color=entry_color, line=dict(width=1, color="white")),
            text=["ENTRY"],
            textposition="top center",
            textfont=dict(size=10, color=entry_color),
            name="entry",
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    exit_date = pd.Timestamp(trade["exit_date"])
    fig.add_trace(
        go.Scatter(
            x=[exit_date],
            y=[trade["exit_price"]],
            mode="markers+text",
            marker=dict(symbol=exit_sym, size=14, color=exit_color, line=dict(width=1, color="white")),
            text=["EXIT"],
            textposition="bottom center",
            textfont=dict(size=10, color=exit_color),
            name="exit",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # Volume
    vol_colors = [
        "#ef5350" if c >= o else "#26a69a"
        for o, c in zip(win["open"], win["close"], strict=True)
    ]
    fig.add_trace(
        go.Bar(
            x=win.index,
            y=win["volume"],
            marker_color=vol_colors,
            opacity=0.4,
            name="volume",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # RSI subplot
    fig.add_trace(
        go.Scatter(
            x=win_enr.index,
            y=win_enr["rsi"],
            mode="lines",
            name="RSI(14)",
            line=dict(color="#5e35b1", width=1.4),
        ),
        row=2,
        col=1,
    )
    for level, color, _name in [
        (75, "#ef5350", "M-top band"),
        (25, "#26a69a", "W-bottom band"),
        (55, "#ffa726", "RSI50 long req"),
        (45, "#ffa726", "RSI50 short req"),
    ]:
        fig.add_hline(
            y=level,
            line=dict(color=color, width=0.6, dash="dot"),
            row=2,  # type: ignore[arg-type]
            col=1,  # type: ignore[arg-type]
        )
    fig.update_yaxes(range=[0, 100], row=2, col=1)

    fig.update_layout(
        title=(
            f"{trade['symbol']} {direction.upper()} {trade['pattern']} | "
            f"entry {trade['entry_date']} @ {trade['entry_price']:.2f} → "
            f"exit {trade['exit_date']} @ {trade['exit_price']:.2f} | "
            f"PnL {trade['pnl']:+.0f} ({trade.get('reason', '')})"
        ),
        xaxis_rangeslider_visible=False,
        height=620,
        margin=dict(l=50, r=80, t=70, b=40),
        legend=dict(orientation="h", y=1.05, x=0),
    )
    return fig


def _table_row(i: int, t: dict) -> str:
    cls = "win" if t["pnl"] > 0 else "loss"
    return (
        f'<tr class="{cls}">'
        f"<td>{i}</td>"
        f"<td><a href='#t{i}'>{t['symbol']}</a></td>"
        f"<td>{t['pattern']}</td>"
        f"<td>{t['direction']}</td>"
        f"<td>{t['entry_date']}</td>"
        f"<td>{t['entry_price']:.2f}</td>"
        f"<td>{t['exit_date']}</td>"
        f"<td>{t['exit_price']:.2f}</td>"
        f"<td>{t['size']}</td>"
        f'<td class="pnl">{t["pnl"]:+.0f}</td>'
        f"<td>{t.get('reason', '')}</td>"
        "</tr>"
    )


def _summary_html(stats: dict) -> str:
    pf = stats.get("profit_factor")
    pf_s = f"{pf:.2f}" if pf is not None and pf != float("inf") else "∞"
    return f"""
    <div class="summary">
      <div class="card"><div class="label">Trades</div><div class="value">{stats['n']}</div></div>
      <div class="card"><div class="label">Wins / Losses</div><div class="value">{stats.get('wins', 0)} / {stats.get('losses', 0)}</div></div>
      <div class="card"><div class="label">Win rate</div><div class="value">{stats['win_rate']*100:.1f}%</div></div>
      <div class="card"><div class="label">Total PnL</div><div class="value">{stats['total_pnl']:+.0f}</div></div>
      <div class="card"><div class="label">Avg PnL</div><div class="value">{stats['avg_pnl']:+.0f}</div></div>
      <div class="card"><div class="label">Profit factor</div><div class="value">{pf_s}</div></div>
    </div>
    """


def _render_html(
    trades: list[dict],
    data_by_symbol: dict[str, pd.DataFrame],
    pivot_params: PivotParams,
) -> str:
    stats = _summary_stats(trades)
    rows = "\n".join(_table_row(i, t) for i, t in enumerate(trades))

    sections: list[str] = []
    for i, trade in enumerate(trades):
        df = data_by_symbol.get(trade["symbol"])
        if df is None or df.empty:
            sections.append(
                f"<section id='t{i}'><h2>Trade {i}: {trade['symbol']}</h2>"
                f"<p class='warn'>No OHLCV data available for this symbol.</p></section>"
            )
            continue
        enriched = _enrich(df)
        fig = _trade_chart(trade, df, enriched, pivot_params)
        inner = fig.to_html(
            full_html=False,
            include_plotlyjs=False,
            div_id=f"chart{i}",
            config={"displaylogo": False, "responsive": True},
        )
        sections.append(
            f"<section id='t{i}'><h2>Trade {i}: {trade['symbol']} "
            f"{trade['pattern']} {trade['direction']}</h2>{inner}</section>"
        )

    css = """
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           margin: 0; padding: 24px; background: #fafafa; color: #212121; }
    h1 { margin: 0 0 16px 0; }
    h2 { margin: 32px 0 8px 0; padding-top: 16px; border-top: 1px solid #e0e0e0; }
    .summary { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
    .card { background: #fff; padding: 12px 16px; border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); min-width: 120px; }
    .card .label { font-size: 12px; color: #757575; }
    .card .value { font-size: 20px; font-weight: 600; margin-top: 4px; }
    table { width: 100%; border-collapse: collapse; background: #fff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-radius: 8px; overflow: hidden; }
    th, td { padding: 8px 12px; text-align: right; border-bottom: 1px solid #eee; }
    th { background: #f5f5f5; font-size: 12px; color: #555; text-transform: uppercase; }
    td:first-child, th:first-child, td:nth-child(2), th:nth-child(2),
    td:nth-child(3), th:nth-child(3), td:nth-child(4), th:nth-child(4) { text-align: left; }
    tr.win td.pnl { color: #2e7d32; font-weight: 600; }
    tr.loss td.pnl { color: #c62828; font-weight: 600; }
    section { background: #fff; padding: 16px; margin-top: 16px;
              border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    .warn { color: #ef6c00; }
    .meta { color: #757575; font-size: 13px; margin-bottom: 16px; }
    """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <title>Trade review — {datetime.now().strftime('%Y-%m-%d %H:%M')}</title>
      <style>{css}</style>
      <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    </head>
    <body>
      <h1>Quant Strategy Trade Review</h1>
      <p class="meta">{stats['n']} trades · generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
      {_summary_html(stats)}
      <table>
        <thead><tr>
          <th>#</th><th>Symbol</th><th>Pattern</th><th>Dir</th>
          <th>Entry</th><th>Price</th><th>Exit</th><th>Price</th>
          <th>Size</th><th>PnL</th><th>Reason</th>
        </tr></thead>
        <tbody>
          {rows}
        </tbody>
      </table>
      {''.join(sections)}
    </body>
    </html>
    """


def generate_report(
    trades: list[dict],
    data_by_symbol: dict[str, pd.DataFrame],
    output_path: str | Path,
    pivot_params: PivotParams | None = None,
) -> Path:
    """Render all trades into one self-contained HTML file. Returns the path."""
    if pivot_params is None:
        pivot_params = StrategyParams().pivot
    html = _render_html(trades, data_by_symbol, pivot_params)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def trades_from_results(
    symbol_trade_logs: Iterable[tuple[str, list[dict]]],
) -> list[dict]:
    """Flatten per-symbol trade logs into a single list (symbol-attached).

    Each `list[dict]` is the `SignalStrategy.trade_log` for one symbol.
    """
    out: list[dict] = []
    for symbol, log in symbol_trade_logs:
        for t in log:
            row = dict(t)
            row.setdefault("symbol", symbol)
            out.append(row)
    return out
