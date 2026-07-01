# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Turn flat InsiderTransaction records into the shape the UI renders:

    watchlist company  ->  one "box" per insider/entity

Each box aggregates all of that insider's transactions for the company:
buy/sell counts, total transaction count, shares and dollar amounts bought
and sold, plus the insider's name, role and whether they're an individual,
an institution, or the issuer itself (a buyback).

The app layer never touches raw rows — it asks for this structure.
"""

from __future__ import annotations

import calendar
import json
from datetime import date
from glob import glob
from pathlib import Path


def months_ago(d: date, n: int) -> date:
    """The calendar date `n` months before `d` (clamped to a valid day)."""
    m = d.month - 1 - n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def latest_data_file(data_dir: Path) -> Path | None:
    """Newest data/insider_YYYY-MM-DD.json, or None if none exist yet."""
    files = sorted(glob(str(data_dir / "insider_*.json")))
    return Path(files[-1]) if files else None


def _empty_box(name: str, role: str, entity_type: str) -> dict:
    return {
        "insider_name": name,
        "insider_role": role,
        "entity_type": entity_type,
        "is_issuer_buyback": False,
        "txn_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "other_count": 0,
        "buy_shares": 0,
        "sell_shares": 0,
        "buy_value": 0.0,
        "sell_value": 0.0,
        "net_value": 0.0,
        "currency": "",
        "latest_date": None,
        "transactions": [],
    }


def _add_txn(box: dict, rec: dict) -> None:
    ttype = (rec.get("transaction_type") or "").lower()
    shares = rec.get("shares") or 0
    value = rec.get("total_value") or 0.0
    box["txn_count"] += 1
    if "buy" in ttype:
        box["buy_count"] += 1
        box["buy_shares"] += shares
        box["buy_value"] += value
        box["net_value"] += value
    elif "sell" in ttype:
        box["sell_count"] += 1
        box["sell_shares"] += shares
        box["sell_value"] += value
        box["net_value"] -= value
    else:
        box["other_count"] += 1
    if rec.get("currency") and not box["currency"]:
        box["currency"] = rec["currency"]
    if rec.get("is_issuer_buyback"):
        box["is_issuer_buyback"] = True
    d = rec.get("transaction_date")
    if d and (box["latest_date"] is None or d > box["latest_date"]):
        box["latest_date"] = d
    box["transactions"].append(
        {
            "date": d,
            "type": rec.get("transaction_type", ""),
            "shares": shares,
            "avg_price": rec.get("avg_price"),
            "total_value": value,
            "currency": rec.get("currency", ""),
        }
    )


def build_view(records: list[dict], watchlist: list[dict]) -> dict:
    """Group records into companies -> insider boxes.

    `watchlist` (companies.json entries) defines which companies appear: issuers
    with zero scraped transactions still show (as empty cards) to make coverage
    gaps visible, and records for issuers not on the watchlist are ignored so a
    removed company disappears instead of lingering from stale scraped data.
    """
    companies: dict[str, dict] = {}

    def company_key(exchange: str, ticker: str) -> str:
        return f"{(exchange or '').upper()}:{(ticker or '').upper()}"

    # seed from the watchlist so uncovered names still render
    for c in watchlist:
        key = company_key(c.get("exchange", ""), c.get("ticker", ""))
        companies[key] = {
            "key": key,
            "issuer_name": c.get("name", key),
            "exchange": (c.get("exchange") or "").upper(),
            "ticker": (c.get("ticker") or "").upper(),
            "confirmed": c.get("confirmed", True),
            "boxes_by_insider": {},
        }

    for rec in records:
        key = company_key(rec.get("exchange", ""), rec.get("ticker", ""))
        comp = companies.get(key)
        if comp is None:
            # The watchlist is the source of truth for what the app shows, so
            # skip records for issuers not on it (e.g. one just removed, or an
            # ad-hoc `--tickers` scrape). Their rows still live in the CSV/JSON.
            continue
        # prefer a real scraped issuer name over the watchlist label
        if rec.get("issuer_name"):
            comp["issuer_name"] = rec["issuer_name"]

        name = (rec.get("insider_name") or "Unknown").strip()
        role = (rec.get("insider_role") or "").strip()
        ikey = f"{name}|{role}"
        box = comp["boxes_by_insider"].get(ikey)
        if box is None:
            box = comp["boxes_by_insider"][ikey] = _empty_box(
                name, role, rec.get("entity_type", "unknown")
            )
        _add_txn(box, rec)

    # finalize: dict -> sorted list, round money, compute company totals
    out_companies = []
    for comp in companies.values():
        boxes = list(comp["boxes_by_insider"].values())
        for b in boxes:
            b["buy_value"] = round(b["buy_value"], 2)
            b["sell_value"] = round(b["sell_value"], 2)
            b["net_value"] = round(b["net_value"], 2)
            b.pop("boxes_by_insider", None)
        # biggest movers first (by gross dollar activity)
        boxes.sort(key=lambda b: b["buy_value"] + b["sell_value"], reverse=True)
        comp.pop("boxes_by_insider", None)
        comp["boxes"] = boxes
        comp["txn_count"] = sum(b["txn_count"] for b in boxes)
        comp["insider_count"] = len(boxes)
        comp["buy_value"] = round(sum(b["buy_value"] for b in boxes), 2)
        comp["sell_value"] = round(sum(b["sell_value"] for b in boxes), 2)
        comp["latest_date"] = max(
            (b["latest_date"] for b in boxes if b["latest_date"]), default=None
        )
        out_companies.append(comp)

    # companies with activity first, then alphabetical
    out_companies.sort(key=lambda c: (-c["txn_count"], c["issuer_name"]))
    return {
        "companies": out_companies,
        "total_companies": len(out_companies),
        "total_transactions": sum(c["txn_count"] for c in out_companies),
        "covered_companies": sum(1 for c in out_companies if c["txn_count"]),
    }


# ---- people view: same records, grouped by insider across companies ----------
# The company view answers "who traded THIS company?"; the people view answers
# "what did THIS person trade, across every watchlist company?". It is a pure
# re-slice of the same records — no extra scraping — so its coverage is exactly
# the companies on the watchlist.


def _person_key(name: str) -> str:
    """Collapse case/whitespace so the same insider merges across companies."""
    return " ".join((name or "").split()).lower()


def _empty_person(name: str, entity_type: str) -> dict:
    return {
        "insider_name": name,
        "entity_type": entity_type,
        "roles": [],
        "txn_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "other_count": 0,
        "buy_shares": 0,
        "sell_shares": 0,
        "buy_value": 0.0,
        "sell_value": 0.0,
        "net_value": 0.0,
        "currency": "",
        "latest_date": None,
        "company_count": 0,
        "companies_by_key": {},
    }


def _empty_person_company(key: str, issuer_name: str, exchange: str, ticker: str) -> dict:
    return {
        "key": key,
        "issuer_name": issuer_name,
        "exchange": exchange,
        "ticker": ticker,
        "txn_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "buy_shares": 0,
        "sell_shares": 0,
        "buy_value": 0.0,
        "sell_value": 0.0,
        "net_value": 0.0,
        "currency": "",
        "latest_date": None,
    }


def _accumulate(agg: dict, rec: dict) -> None:
    """Fold one record's buy/sell numbers into a person or per-company aggregate."""
    ttype = (rec.get("transaction_type") or "").lower()
    shares = rec.get("shares") or 0
    value = rec.get("total_value") or 0.0
    agg["txn_count"] += 1
    if "buy" in ttype:
        agg["buy_count"] += 1
        agg["buy_shares"] += shares
        agg["buy_value"] += value
        agg["net_value"] += value
    elif "sell" in ttype:
        agg["sell_count"] += 1
        agg["sell_shares"] += shares
        agg["sell_value"] += value
        agg["net_value"] -= value
    else:
        agg.setdefault("other_count", 0)
        agg["other_count"] += 1
    if rec.get("currency") and not agg["currency"]:
        agg["currency"] = rec["currency"]
    d = rec.get("transaction_date")
    if d and (agg["latest_date"] is None or d > agg["latest_date"]):
        agg["latest_date"] = d


def build_people_view(records: list[dict], watchlist: list[dict]) -> dict:
    """Group records into insiders -> the companies they traded.

    Only records for companies on the `watchlist` are counted (the watchlist is
    the source of truth for what the app shows). Issuer buybacks are excluded —
    they are the company trading its own stock, not a person/insider.
    """

    def company_key(exchange: str, ticker: str) -> str:
        return f"{(exchange or '').upper()}:{(ticker or '').upper()}"

    watchlist_keys = {company_key(c.get("exchange", ""), c.get("ticker", "")) for c in watchlist}
    watchlist_names = {
        company_key(c.get("exchange", ""), c.get("ticker", "")): c.get("name", "")
        for c in watchlist
    }

    people: dict[str, dict] = {}
    for rec in records:
        if rec.get("is_issuer_buyback"):
            continue
        ckey = company_key(rec.get("exchange", ""), rec.get("ticker", ""))
        if ckey not in watchlist_keys:
            continue

        name = (rec.get("insider_name") or "Unknown").strip()
        pkey = _person_key(name)
        person = people.get(pkey)
        if person is None:
            person = people[pkey] = _empty_person(name, rec.get("entity_type", "unknown"))

        role = (rec.get("insider_role") or "").strip()
        if role and role not in person["roles"]:
            person["roles"].append(role)

        comp = person["companies_by_key"].get(ckey)
        if comp is None:
            comp = person["companies_by_key"][ckey] = _empty_person_company(
                ckey,
                rec.get("issuer_name") or watchlist_names.get(ckey, ckey),
                (rec.get("exchange") or "").upper(),
                (rec.get("ticker") or "").upper(),
            )
        elif rec.get("issuer_name"):
            comp["issuer_name"] = rec["issuer_name"]

        _accumulate(person, rec)
        _accumulate(comp, rec)

    out_people = []
    for person in people.values():
        companies = list(person.pop("companies_by_key").values())
        for c in companies:
            c["buy_value"] = round(c["buy_value"], 2)
            c["sell_value"] = round(c["sell_value"], 2)
            c["net_value"] = round(c["net_value"], 2)
        # biggest positions first, by gross dollar activity in that company
        companies.sort(key=lambda c: c["buy_value"] + c["sell_value"], reverse=True)
        person["companies"] = companies
        person["company_count"] = len(companies)
        person["buy_value"] = round(person["buy_value"], 2)
        person["sell_value"] = round(person["sell_value"], 2)
        person["net_value"] = round(person["net_value"], 2)
        out_people.append(person)

    # most active insiders first (gross dollars), then alphabetical
    out_people.sort(key=lambda p: (-(p["buy_value"] + p["sell_value"]), p["insider_name"].lower()))
    return {
        "people": out_people,
        "total_people": len(out_people),
        "total_transactions": sum(p["txn_count"] for p in out_people),
        "total_companies": len({c["key"] for p in out_people for c in p["companies"]}),
    }


def _load_records(
    data_dir: Path, config_path: Path, months: int | None
) -> tuple[list[dict], list[dict], Path | None]:
    """Shared loader for the company and people views.

    Returns (records, watchlist, data_file). `months`, when given, keeps only
    transactions dated within the last N calendar months so downstream
    aggregates reflect the selected window.
    """
    watchlist = []
    if config_path.exists():
        cfg = json.loads(config_path.read_text())
        watchlist = [
            c for c in cfg.get("companies", []) if not str(c.get("name", "")).startswith("_")
        ]

    records: list[dict] = []
    data_file = latest_data_file(data_dir)
    if data_file:
        records = json.loads(data_file.read_text())

    if months:
        cutoff = months_ago(date.today(), int(months)).isoformat()
        records = [r for r in records if (r.get("transaction_date") or "") >= cutoff]

    return records, watchlist, data_file


def _stamp(view: dict, data_file: Path | None, months: int | None) -> dict:
    """Attach the shared data-file / date / range metadata to a view."""
    view["data_file"] = data_file.name if data_file else None
    view["data_date"] = data_file.stem.replace("insider_", "") if data_file else None
    view["range_months"] = int(months) if months else None
    return view


def load_view(data_dir: Path, config_path: Path, months: int | None = None) -> dict:
    """Load the newest data file + watchlist and build the company view."""
    records, watchlist, data_file = _load_records(data_dir, config_path, months)
    return _stamp(build_view(records, watchlist), data_file, months)


def load_people_view(data_dir: Path, config_path: Path, months: int | None = None) -> dict:
    """Load the newest data file + watchlist and build the people view."""
    records, watchlist, data_file = _load_records(data_dir, config_path, months)
    return _stamp(build_people_view(records, watchlist), data_file, months)
