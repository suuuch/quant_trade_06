"""Parse and validate ETF category lists."""

from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORY_RE = re.compile(r"^#\s*=+\s*(?P<name>.*?)\s*=+\s*$")
ETF_RE = re.compile(
    r"^\s*'(?P<symbol>[A-Z][A-Z0-9]{0,5})'\s*:\s*'(?P<label>[^']+)'\s*,?\s*$"
)


@dataclass(frozen=True)
class EtfCategory:
    """One ETF category row parsed from the source list."""

    symbol: str
    code: str
    category_group: str
    category_label: str
    source_order: int


def parse_etf_categories(text: str) -> list[EtfCategory]:
    """Parse ETF category rows from the project ETF list format."""
    rows: list[EtfCategory] = []
    seen: set[str] = set()
    category_group = ""

    for line in text.splitlines():
        category_match = CATEGORY_RE.match(line.strip())
        if category_match is not None:
            category_group = category_match.group("name").strip()
            continue

        etf_match = ETF_RE.match(line)
        if etf_match is None:
            continue
        if not category_group:
            raise ValueError("ETF row appeared before a category header")

        symbol = etf_match.group("symbol")
        if symbol in seen:
            raise ValueError(f"duplicate ETF symbol: {symbol}")
        seen.add(symbol)

        rows.append(
            EtfCategory(
                symbol=symbol,
                code=f"US.{symbol}",
                category_group=category_group,
                category_label=etf_match.group("label").strip(),
                source_order=len(rows) + 1,
            )
        )

    if not rows:
        raise ValueError("no ETF category rows found")
    return rows
