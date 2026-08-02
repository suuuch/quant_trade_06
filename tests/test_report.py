"""Smoke test for the HTML trade report."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant_trade.config import StrategyParams
from quant_trade.engine import run_engine
from quant_trade.report import generate_report, trades_from_results


def _synth(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    close = np.full(n, 100.0)
    close[30:50] = np.linspace(100, 80, 20)
    close[50:70] = np.linspace(80, 105, 20)
    close[70:90] = np.linspace(105, 95, 20)
    close[90:130] = np.linspace(95, 80, 40)
    close[130:200] = np.linspace(80, 90, 70) + rng.normal(0, 0.3, 70)
    close += rng.normal(0, 0.3, n)
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.1, n),
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
        },
        index=pd.bdate_range("2024-01-01", periods=n),
    )


def test_trades_from_results_attaches_symbol() -> None:
    logs = [
        ("000001.SZ", [{"entry_date": "2024-01-01", "entry_price": 10.0}]),
        ("600519.SH", [{"entry_date": "2024-02-01", "entry_price": 100.0}]),
    ]
    out = trades_from_results(logs)
    assert len(out) == 2
    assert out[0]["symbol"] == "000001.SZ"
    assert out[1]["symbol"] == "600519.SH"


def test_generate_report_renders_html(tmp_path: Path) -> None:
    df = _synth()
    sigs = run_engine(df, StrategyParams(), symbol="SYN")
    assert sigs, "test prerequisite: synthetic series should produce a signal"

    # Synthesize a fake trade log to feed into the report.
    s = sigs[0]
    trade = {
        "symbol": "SYN",
        "pattern": s.pattern,
        "direction": s.direction,
        "entry_date": s.triggered_at.strftime("%Y-%m-%d"),
        "entry_price": s.trigger_price,
        "exit_date": (s.triggered_at + pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
        "exit_price": s.trigger_price * 0.95,
        "size": 100,
        "pnl": -500.0,
        "stop_loss": s.stop_loss,
        "invalidation_price": s.invalidation_price,
        "reason": "stop_loss",
    }
    out = generate_report(
        trades=[trade],
        data_by_symbol={"SYN": df},
        output_path=tmp_path / "report.html",
    )
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "Trade review" in html
    assert "id='t0'" in html
    assert "stop_loss" in html
    # The plotly CDN script should be referenced (no inline JS).
    assert "plotly" in html.lower()


def test_generate_report_empty_trades(tmp_path: Path) -> None:
    """Empty trade list should still produce a valid file with a header."""
    out = generate_report(
        trades=[],
        data_by_symbol={},
        output_path=tmp_path / "empty.html",
    )
    assert out.exists()
    assert "0 trades" in out.read_text(encoding="utf-8")
