"""Tests for the RSI50 QQ delivery command."""

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


def test_qq_delivery_defaults_to_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["scan_rsi50_to_qq.py"])

    args = parse_args()

    assert args.target_type == "group"


def test_default_group_target_comes_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QQBOT_GROUP_OPENID", "group-openid")

    assert _default_target_id("group") == "group-openid"
