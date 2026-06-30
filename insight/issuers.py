# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Resolve a company NAME to concrete issuer candidates.

Going forward the app's primary input is a company name, not a ticker. This
module turns a free-text name into a ranked list of issuer candidates
(legal name, ticker, exchange, country). When more than one matches, the app
shows the list and the user picks the right one — tickers collide across
exchanges and listings (e.g. NFG = New Found Gold in Canada vs National Fuel
Gas in the US), so a name alone is never assumed unique.

Backend: TradingView's public symbol-search endpoint, which is reachable from
this host (unlike SEDI / canadianinsider, which are IP/Cloudflare-blocked) and
covers TSX / TSX-V / CSE / US listings. The resolver is deliberately isolated
behind `search_issuers()` so the authoritative SEDI issuer search can replace
it later without touching the app or watchlist code.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

_SEARCH_URL = "https://symbol-search.tradingview.com/symbol_search/"
_HEADERS = {
    # the endpoint 403s without a browser-like Origin/Referer
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
    "Accept": "application/json",
}

# TradingView exchange code -> the code our scrapers/watchlist use (MarketBeat).
_EXCHANGE_MAP = {
    "TSX": "TSE",
    "TSXV": "TSXV",
    "CSE": "CSE",
    "NEO": "NEO",
    "CBOE": "CSE",
    "NYSE": "NYSE",
    "NASDAQ": "NASDAQ",
    "AMEX": "NYSEAMERICAN",
}
# preferred listings float to the top of the candidate list
_EXCHANGE_RANK = {
    "TSE": 0,
    "TSXV": 1,
    "CSE": 2,
    "NEO": 3,
    "NYSE": 4,
    "NASDAQ": 5,
    "NYSEAMERICAN": 6,
}

_TAG_RE = re.compile(r"<[^>]+>")


def _strip(s: str) -> str:
    return _TAG_RE.sub("", s or "").replace("&amp;", "&").strip()


def map_exchange(tv_exchange: str) -> str:
    """TradingView exchange -> our internal/MarketBeat code (best effort)."""
    e = (tv_exchange or "").upper()
    return _EXCHANGE_MAP.get(e, e)


def candidate_key(exchange: str, ticker: str) -> str:
    return f"{(exchange or '').upper()}:{(ticker or '').upper()}"


def search_issuers(name: str, limit: int = 15, country_first: str = "CA") -> list[dict]:
    """Return ranked issuer candidates matching `name`.

    Each candidate: {legal_name, ticker, exchange, exchange_raw, country,
    type, key}. Canadian listings and primary exchanges are ranked first so
    the most likely intended issuer is at the top of the picker.
    """
    name = (name or "").strip()
    if not name:
        return []
    qs = urllib.parse.urlencode(
        {
            "text": name,
            "hl": "1",
            "lang": "en",
            "type": "stock",
            "domain": "production",
        }
    )
    req = urllib.request.Request(f"{_SEARCH_URL}?{qs}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = json.loads(resp.read().decode("utf-8"))

    seen: set[str] = set()
    out: list[dict] = []
    for item in raw:
        ticker = _strip(item.get("symbol", ""))
        exch = map_exchange(item.get("exchange", ""))
        if not ticker or not exch:
            continue
        key = candidate_key(exch, ticker)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "legal_name": _strip(item.get("description", "")),
                "ticker": ticker,
                "exchange": exch,
                "exchange_raw": _strip(item.get("exchange", "")),
                "country": (item.get("country") or "").upper(),
                "type": item.get("type", ""),
                "key": key,
            }
        )

    def rank(c: dict) -> tuple:
        return (
            0 if c["country"] == country_first else 1,
            _EXCHANGE_RANK.get(c["exchange"], 99),
            c["legal_name"].lower(),
        )

    out.sort(key=rank)

    # Collapse dual-listings of the SAME issuer in the SAME country (e.g. a name
    # listed on both TSXV and NEO/Cboe Canada) to one row, keeping the
    # best-ranked exchange. The issuer is identical for insider-filing purposes.
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    best: dict[tuple, dict] = {}
    for c in out:  # already rank-sorted, so first seen is best
        ckey = (norm(c["legal_name"]), c["country"])
        if ckey not in best:
            best[ckey] = c
    return list(best.values())[:limit]


# ---------- watchlist mutation ----------


def load_watchlist(config_path) -> dict:
    return json.loads(config_path.read_text())


def in_watchlist(cfg: dict, exchange: str, ticker: str) -> bool:
    key = candidate_key(exchange, ticker)
    return any(
        candidate_key(c.get("exchange", ""), c.get("ticker", "")) == key
        for c in cfg.get("companies", [])
    )


def add_to_watchlist(config_path, candidate: dict) -> tuple[bool, str]:
    """Append a resolved candidate to companies.json. Returns (added, msg)."""
    name = (candidate.get("legal_name") or candidate.get("name") or "").strip()
    exchange = (candidate.get("exchange") or "").upper()
    ticker = (candidate.get("ticker") or "").upper()
    if not (name and exchange and ticker):
        return False, "candidate missing name/exchange/ticker"

    cfg = load_watchlist(config_path)
    if in_watchlist(cfg, exchange, ticker):
        return False, f"{name} ({exchange}:{ticker}) is already on the watchlist"

    cfg["companies"].append(
        {
            "name": name,
            "exchange": exchange,
            "ticker": ticker,
            "country": candidate.get("country", ""),
            "confirmed": True,
        }
    )
    config_path.write_text(json.dumps(cfg, indent=2) + "\n")
    return True, f"Added {name} ({exchange}:{ticker})"
