"""Generate an interactive HTML report for manual signal verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pandas as pd
from plotly.offline import get_plotlyjs

from quant_trade.backtest import run_backtest
from quant_trade.data import load_duckdb_bars
from quant_trade.rsi50 import (
    Bar,
    Direction,
    Rsi50SignalEngine,
    Signal,
    SignalFeature,
)


def generate_signal_review(
    database: str | Path,
    output: str | Path,
    symbols: list[str],
) -> Path:
    """Write an interactive candlestick and signal-audit report."""
    payload = {symbol: _build_symbol_payload(database, symbol) for symbol in symbols}
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    html = _REPORT_TEMPLATE.replace("__PLOTLY_JS__", get_plotlyjs()).replace(
        "__REPORT_DATA__",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    destination.write_text(html, encoding="utf-8")
    return destination


def _build_symbol_payload(database: str | Path, symbol: str) -> dict[str, Any]:
    frame = load_duckdb_bars(database, symbol)
    engine = Rsi50SignalEngine()
    audits: list[dict[str, Any]] = []
    for timestamp, row in frame.iterrows():
        signal = engine.on_bar(
            Bar(
                timestamp=cast(pd.Timestamp, timestamp).to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )
        if signal is not None:
            audits.append(_audit_signal(engine, signal))

    result = run_backtest(frame, allow_short=False)
    return {
        "symbol": symbol,
        "bars": {
            "date": [timestamp.strftime("%Y-%m-%d") for timestamp in frame.index],
            "open": _rounded(frame["open"]),
            "high": _rounded(frame["high"]),
            "low": _rounded(frame["low"]),
            "close": _rounded(frame["close"]),
            "ma20": _rounded_optional(engine.fast_ma_values),
            "ma30": _rounded_optional(engine.slow_ma_values),
            "rsi": _rounded_optional(engine.rsi_values),
        },
        "signals": audits,
        "backtest": {
            "return_percent": round(result.return_percent, 2),
            "max_drawdown_percent": round(result.max_drawdown_percent, 2),
            "closed_trades": result.closed_trades,
            "won_trades": result.won_trades,
            "lost_trades": result.lost_trades,
        },
    }


def _audit_signal(
    engine: Rsi50SignalEngine,
    signal: Signal,
) -> dict[str, Any]:
    current = len(engine.bars) - 1
    first = signal.first_pivot_index
    second = signal.second_pivot_index
    is_long = signal.direction is Direction.LONG
    first_price = engine.bars[first].low if is_long else engine.bars[first].high
    second_price = engine.bars[second].low if is_long else engine.bars[second].high
    second_atr = engine.atr_values[second]
    if second_atr is None:
        raise ValueError("signal second pivot has no ATR value")
    calculation = engine.calculate_current_signal(signal.direction)
    if calculation is None:
        raise ValueError("signal has no current calculation")
    inputs = calculation.inputs
    if inputs.pattern.first_index != first or inputs.pattern.second_index != second:
        raise ValueError("signal calculation pattern does not match signal")
    fast_angle = inputs.fast_ma_angle
    if fast_angle is None:
        raise ValueError("signal has incomplete MA20 angle values")
    angle_threshold = engine.config.ma_fast_min_angle_degrees

    if is_long:
        retracement = signal.neckline - max(first_price, second_price)
        action = "目标多仓 95%"
        pattern_name = "W 底"
    else:
        retracement = min(first_price, second_price) - signal.neckline
        action = "平多至 0%（A 股现货）"
        pattern_name = "M 顶"
    break_threshold = calculation.breakout_threshold
    rsi_rule = (
        f"{engine.config.trigger_rsi_low:g} ≤ RSI ≤ {engine.config.trigger_rsi_high:g}"
    )
    rsi_pass = calculation.rsi_trigger_pass
    trend_pass = calculation.fast_trend_pass and calculation.slow_trend_pass
    break_pass = calculation.breakout_pass

    zone_index = calculation.feature(SignalFeature.RSI_ZONE_ENTRY).observed_index
    if zone_index is None:
        raise ValueError("signal has no RSI 45–55 observation")
    pivot_difference_atr = abs(second_price - first_price) / second_atr
    retracement_atr = retracement / second_atr
    distance = second - first
    checks = [
        {
            "name": "摆动点已确认",
            "value": "日线 3/3，两个摆动点均已等待右侧 3 根 K 线",
            "pass": True,
        },
        {
            "name": "形态间距",
            "value": f"{distance} 根，要求 5–30 根",
            "pass": 5 <= distance <= 30,
        },
        {
            "name": "两端价差",
            "value": f"{pivot_difference_atr:.3f} ATR，要求 ≤ 1 ATR",
            "pass": pivot_difference_atr <= 1.0,
        },
        {
            "name": "中间回撤/反弹",
            "value": f"{retracement_atr:.3f} ATR，要求 ≥ 1 ATR",
            "pass": retracement_atr >= 1.0,
        },
        {
            "name": "RSI 进入 45–55",
            "value": (
                f"{engine.bars[zone_index].timestamp:%Y-%m-%d}，"
                f"RSI {engine.rsi_values[zone_index]:.2f}"
            ),
            "pass": True,
        },
        {
            "name": "MA20 角度、MA30 方向",
            "value": (
                f"MA20 最近 {engine.config.ma_fast_angle_bars} 根 "
                f"{fast_angle:.2f}°"
                f"（{'仅判断方向' if angle_threshold is None else f'阈值 {angle_threshold:g}°'}）；"
                f"MA30 {inputs.previous_slow_ma:.3f} → {inputs.slow_ma:.3f}"
            ),
            "pass": trend_pass,
        },
        {
            "name": "收盘有效突破",
            "value": (
                f"收盘 {signal.close:.3f}；颈线 {signal.neckline:.3f}；"
                f"阈值 {break_threshold:.3f}"
            ),
            "pass": break_pass,
        },
        {
            "name": "RSI 动量确认",
            "value": f"RSI {signal.rsi:.2f}；要求 {rsi_rule}",
            "pass": rsi_pass,
        },
    ]
    return {
        "date": signal.timestamp.strftime("%Y-%m-%d"),
        "bar_index": current,
        "direction": signal.direction.value,
        "pattern": pattern_name,
        "action": action,
        "close": round(signal.close, 4),
        "rsi": round(signal.rsi, 2),
        "atr": round(signal.atr, 4),
        "neckline": round(signal.neckline, 4),
        "break_threshold": round(break_threshold, 4),
        "first_index": first,
        "second_index": second,
        "first_date": engine.bars[first].timestamp.strftime("%Y-%m-%d"),
        "second_date": engine.bars[second].timestamp.strftime("%Y-%m-%d"),
        "first_price": round(first_price, 4),
        "second_price": round(second_price, 4),
        "zone_index": zone_index,
        "checks": checks,
    }


def _rounded(series: pd.Series[float]) -> list[float]:
    return [round(float(value), 4) for value in series]


def _rounded_optional(values: list[float | None]) -> list[float | None]:
    return [None if value is None else round(value, 4) for value in values]


_REPORT_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RSI 50 日线信号审阅</title>
<script>__PLOTLY_JS__</script>
<style>
:root{color-scheme:light dark;--bg:#f5f6f8;--panel:#fff;--text:#172033;--muted:#667085;--border:#d9dee8;--long:#18864b;--short:#c43b46;--accent:#2463eb;--soft:#eef2f8}
@media(prefers-color-scheme:dark){:root{--bg:#11151d;--panel:#191f2a;--text:#edf1f7;--muted:#a9b2c3;--border:#343d4d;--long:#45c982;--short:#ff7180;--accent:#79a6ff;--soft:#222a37}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}.shell{max-width:1500px;margin:auto;padding:20px}.toolbar,.summary,.audit{background:var(--panel);border:1px solid var(--border);border-radius:10px}.toolbar{display:flex;gap:16px;align-items:end;flex-wrap:wrap;padding:14px}.field{display:grid;gap:5px}.field label{color:var(--muted);font-size:12px}select,button{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:7px 10px}button{cursor:pointer}button:disabled{opacity:.4;cursor:default}.counter{color:var(--muted);padding:8px 0}.summary{display:flex;gap:24px;flex-wrap:wrap;padding:12px 14px;margin-top:12px}.metric{display:grid;gap:2px}.metric span{color:var(--muted);font-size:12px}.metric strong{font-weight:500}.chart{height:680px;background:var(--panel);border:1px solid var(--border);border-radius:10px;margin-top:12px}.audit{margin-top:12px;overflow:hidden}.audit-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:14px;border-bottom:1px solid var(--border)}.long{color:var(--long)}.short{color:var(--short)}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:top}th{color:var(--muted);font-weight:500;background:var(--soft)}.pass{color:var(--long);font-weight:500}.signal-list{margin-top:12px;background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:auto;max-height:320px}.signal-list button{border:0;background:transparent;width:100%;text-align:left;border-radius:0;padding:9px 12px}.signal-list button:hover,.signal-list button.active{background:var(--soft)}@media(max-width:700px){.shell{padding:10px}.chart{height:560px}th,td{padding:8px}.summary{gap:14px}}
</style>
</head>
<body>
<main class="shell">
  <section class="toolbar">
    <div class="field"><label for="symbol">股票</label><select id="symbol"></select></div>
    <div class="field"><label for="signal">信号日期</label><select id="signal"></select></div>
    <button id="previous" type="button">上一个信号</button>
    <button id="next" type="button">下一个信号</button>
    <span class="counter" id="counter"></span>
  </section>
  <section class="summary" id="summary"></section>
  <div class="chart" id="chart" aria-label="日线 K 线、均线与 RSI 图"></div>
  <section class="audit">
    <div class="audit-head" id="audit-head"></div>
    <table><thead><tr><th>检查项</th><th>实际值</th><th>结论</th></tr></thead><tbody id="checks"></tbody></table>
  </section>
  <section class="signal-list" id="signal-list" aria-label="全部信号"></section>
</main>
<script>
const DATA=__REPORT_DATA__;
const symbolSelect=document.getElementById('symbol');
const signalSelect=document.getElementById('signal');
const previousButton=document.getElementById('previous');
const nextButton=document.getElementById('next');
const chart=document.getElementById('chart');
const symbols=Object.keys(DATA);
symbols.forEach(symbol=>symbolSelect.add(new Option(symbol,symbol)));
function currentStock(){return DATA[symbolSelect.value]}
function currentIndex(){return Math.max(0,Number(signalSelect.value)||0)}
function populateSignals(){
  signalSelect.innerHTML='';
  currentStock().signals.forEach((signal,index)=>signalSelect.add(new Option(`${signal.date} · ${signal.direction==='long'?'多头':'空头'} · ${signal.pattern}`,String(index))));
  signalSelect.value='0';render();
}
function render(){
  const stock=currentStock();const index=currentIndex();const signal=stock.signals[index];
  if(!signal)return;
  const bars=stock.bars;const long=signal.direction==='long';
  document.getElementById('counter').textContent=`${index+1} / ${stock.signals.length}`;
  previousButton.disabled=index===0;nextButton.disabled=index===stock.signals.length-1;
  document.getElementById('summary').innerHTML=`<div class="metric"><span>现货回测收益</span><strong>${stock.backtest.return_percent}%</strong></div><div class="metric"><span>最大回撤</span><strong>${stock.backtest.max_drawdown_percent}%</strong></div><div class="metric"><span>已平仓交易</span><strong>${stock.backtest.closed_trades}</strong></div><div class="metric"><span>信号总数</span><strong>${stock.signals.length}</strong></div>`;
  document.getElementById('audit-head').innerHTML=`<strong class="${signal.direction}">${signal.date} · ${long?'多头':'空头'} · ${signal.pattern}</strong><span>${signal.action} · 收盘 ${signal.close} · RSI ${signal.rsi}</span>`;
  document.getElementById('checks').innerHTML=signal.checks.map(check=>`<tr><td>${check.name}</td><td>${check.value}</td><td class="${check.pass?'pass':''}">${check.pass?'满足':'不满足'}</td></tr>`).join('');
  document.getElementById('signal-list').innerHTML=stock.signals.map((item,itemIndex)=>`<button type="button" data-index="${itemIndex}" class="${itemIndex===index?'active':''}">${String(itemIndex+1).padStart(2,'0')} · ${item.date} · ${item.direction==='long'?'多头':'空头'} · ${item.action}</button>`).join('');
  document.querySelectorAll('#signal-list button').forEach(button=>button.addEventListener('click',()=>{signalSelect.value=button.dataset.index;render()}));
  const markerColor=long?'#18864b':'#c43b46';const start=Math.max(0,signal.bar_index-45);const end=Math.min(bars.date.length-1,signal.bar_index+20);
  const traces=[
    {type:'candlestick',x:bars.date,open:bars.open,high:bars.high,low:bars.low,close:bars.close,name:'K线',increasing:{line:{color:'#18864b'}},decreasing:{line:{color:'#c43b46'}},xaxis:'x',yaxis:'y'},
    {type:'scatter',mode:'lines',x:bars.date,y:bars.ma20,name:'MA20',line:{width:1.3,color:'#d08b20'},xaxis:'x',yaxis:'y'},
    {type:'scatter',mode:'lines',x:bars.date,y:bars.ma30,name:'MA30',line:{width:1.3,color:'#2463eb'},xaxis:'x',yaxis:'y'},
    {type:'scatter',mode:'lines+markers',x:[bars.date[signal.first_index],bars.date[signal.second_index],signal.date],y:[signal.neckline,signal.neckline,signal.neckline],name:'颈线',line:{width:1.5,dash:'dash',color:'#7b8494'},marker:{size:0},xaxis:'x',yaxis:'y'},
    {type:'scatter',mode:'markers+text',x:[bars.date[signal.first_index],bars.date[signal.second_index]],y:[signal.first_price,signal.second_price],text:['P1','P2'],textposition:'top center',name:'摆动点',marker:{size:9,color:markerColor},xaxis:'x',yaxis:'y'},
    {type:'scatter',mode:'markers',x:[signal.date],y:[signal.close],name:'触发',marker:{size:14,color:markerColor,symbol:long?'triangle-up':'triangle-down',line:{width:1,color:'#fff'}},xaxis:'x',yaxis:'y'},
    {type:'scatter',mode:'lines',x:bars.date,y:bars.rsi,name:'RSI(14)',line:{width:1.5,color:'#805ad5'},xaxis:'x',yaxis:'y2'},
    {type:'scatter',mode:'markers',x:[signal.date],y:[signal.rsi],name:'RSI触发',marker:{size:10,color:markerColor},xaxis:'x',yaxis:'y2'}
  ];
  const style=getComputedStyle(document.documentElement);const bg=style.getPropertyValue('--panel').trim();const fg=style.getPropertyValue('--text').trim();const grid=style.getPropertyValue('--border').trim();
  Plotly.react(chart,traces,{paper_bgcolor:bg,plot_bgcolor:bg,font:{color:fg},margin:{l:55,r:25,t:35,b:42},legend:{orientation:'h',y:1.04},xaxis:{type:'date',range:[bars.date[start],bars.date[end]],rangeslider:{visible:false},gridcolor:grid,anchor:'y2'},yaxis:{domain:[0.34,1],gridcolor:grid,title:'前复权价格'},yaxis2:{domain:[0,0.23],range:[0,100],gridcolor:grid,title:'RSI'},shapes:[45,50,55].map(value=>({type:'line',xref:'paper',x0:0,x1:1,yref:'y2',y0:value,y1:value,line:{color:grid,width:value===50?1.2:1,dash:'dot'}})),hovermode:'x unified'},{responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']});
}
symbolSelect.addEventListener('change',populateSignals);signalSelect.addEventListener('change',render);previousButton.addEventListener('click',()=>{signalSelect.value=String(currentIndex()-1);render()});nextButton.addEventListener('click',()=>{signalSelect.value=String(currentIndex()+1);render()});
symbolSelect.value=symbols[0];populateSignals();
</script>
</body>
</html>
"""
