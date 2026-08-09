# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Normalized data model for an insider transaction.

The schema is deliberately source-agnostic: whichever provider we scrape
(MarketBeat today, SEDI later) is normalized into this same record so the
downstream processing/storage layer never has to care where it came from.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

# Tokens that indicate the "insider" is a company / fund / institution
# rather than a natural person (covers issuer buybacks too).
_INSTITUTION_TOKENS = re.compile(
    r"\b(corp|corporation|inc|incorporated|ltd|limited|llc|lp|plc|"
    r"fund|trust|capital|management|holdings?|partners?|ventures?|"
    r"group|company|co|bank|asset|investments?|sarl|gmbh|ag)\b",
    re.IGNORECASE,
)


@dataclass
class InsiderTransaction:
    # --- identity of the issuer the trade is in ---
    issuer_name: str
    exchange: str  # e.g. TSE, TSXV, NYSE
    ticker: str

    # --- who traded ---
    insider_name: str
    insider_role: str = ""  # Director / Officer / Insider / 10% Owner ...
    entity_type: str = "unknown"  # individual | institution

    # --- the trade ---
    transaction_date: str | None = None  # ISO yyyy-mm-dd
    transaction_type: str = ""  # Buy | Sell | Option Exercise | ...
    shares: int | None = None
    avg_price: float | None = None
    total_value: float | None = None
    currency: str = ""  # CAD / USD

    # --- provenance ---
    is_issuer_buyback: bool = False  # the company trading its own stock
    source: str = ""  # marketbeat | sedi
    source_url: str = ""
    scraped_at: str = ""  # ISO timestamp set by the scraper

    def classify(self) -> InsiderTransaction:
        """Fill entity_type / buyback flag from the names we have."""
        name = (self.insider_name or "").strip()
        is_inst = bool(_INSTITUTION_TOKENS.search(name))
        self.entity_type = "institution" if is_inst else "individual"

        # issuer trading its own shares: the insider name and the issuer name
        # are the same entity. MarketBeat shows a short issuer name
        # ("Athabasca Oil") but the full legal name on the row ("Athabasca Oil
        # Corporation"), so test containment in BOTH directions.
        def norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower())

        n, iss = norm(name), norm(self.issuer_name)
        if n and iss and (n in iss or iss in n):
            self.is_issuer_buyback = True
            self.entity_type = "institution"
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- parsing helpers shared across scrapers ----------


def parse_money(text: str) -> tuple[float | None, str]:
    """'C$1,700,250.00' -> (1700250.0, 'CAD').  '$5.20' -> (5.2, 'USD')."""
    if not text:
        return None, ""
    t = text.strip()
    currency = ""
    if t.startswith("C$") or "CAD" in t.upper():
        currency = "CAD"
    elif t.startswith("$") or "USD" in t.upper():
        currency = "USD"
    num = re.sub(r"[^0-9.\-]", "", t.replace(",", ""))
    try:
        return (float(num) if num not in ("", "-", ".") else None), currency
    except ValueError:
        return None, currency


def parse_int(text: str) -> int | None:
    if not text:
        return None
    num = re.sub(r"[^0-9\-]", "", text.replace(",", ""))
    try:
        return int(num) if num not in ("", "-") else None
    except ValueError:
        return None


def parse_us_date(text: str) -> str | None:
    """'3/24/2026' -> '2026-03-24'."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not m:
        return None
    mm, dd, yyyy = (int(x) for x in m.groups())
    try:
        return date(yyyy, mm, dd).isoformat()
    except ValueError:
        return None
