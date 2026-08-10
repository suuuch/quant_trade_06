"""Tests for the RSI trend-following QQ delivery command."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from quant_trade.qq_bot import QQTargetType

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "scan_rsi50_to_qq.py"
_SPEC = importlib.util.spec_from_file_location("scan_rsi50_to_qq", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load {_SCRIPT_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
parse_args = cast(Callable[[], argparse.Namespace], _MODULE.parse_args)
_default_target_id = cast(
    Callable[[QQTargetType], str],
    _MODULE._default_target_id,
)
_filter_conditions = cast(
    Callable[[str, str], str],
    _MODULE._filter_conditions,
)


def test_qq_delivery_defaults_to_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["scan_rsi50_to_qq.py"])

    args = parse_args()

    assert args.target_type == "group"


def test_default_group_target_comes_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QQBOT_GROUP_OPENID", "group-openid")

    assert _default_target_id("group") == "group-openid"


def test_a_share_summary_lists_active_filter_conditions() -> None:
    text = _filter_conditions("a", "both")

    assert "MA20 最近 15 Bar 拟合角度 > 20°" in text
    assert "MA20 最近 15 Bar 拟合角度 < -20°" in text
    assert "最新一天 RSI(14) 位于 42–58" in text
    assert "多头：最近 5 天 RSI 全部位于 50–58" in text
    assert "空头：最近 5 天 RSI 全部位于 42–50" in text
    assert "MA30" not in text
    assert "W 底" not in text
    assert "M 顶" not in text
