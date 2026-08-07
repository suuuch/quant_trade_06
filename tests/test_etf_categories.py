"""Tests for ETF category parsing."""

import pytest

from quant_trade.etf_categories import parse_etf_categories


def test_parse_etf_categories_keeps_category_context() -> None:
    rows = parse_etf_categories(
        """
# ===== 核心 Sector ETFs (SPDRs/Vanguard) =====
    'XLK': '科技',

    # ===== 科技细分 =====
    'SMH': 'iShares半导体',
        """
    )

    assert rows[0].symbol == "XLK"
    assert rows[0].code == "US.XLK"
    assert rows[0].category_group == "核心 Sector ETFs (SPDRs/Vanguard)"
    assert rows[0].category_label == "科技"
    assert rows[0].source_order == 1
    assert rows[1].category_group == "科技细分"


def test_parse_etf_categories_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="duplicate ETF symbol: XLK"):
        parse_etf_categories(
            """
# ===== 核心 Sector ETFs (SPDRs/Vanguard) =====
    'XLK': '科技',
    'XLK': '科技',
            """
        )
