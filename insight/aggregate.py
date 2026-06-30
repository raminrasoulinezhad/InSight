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

import json
from glob import glob
from pathlib import Path


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

    `watchlist` (companies.json entries) seeds the company list so issuers
    with zero scraped transactions still appear (as empty cards), making
    coverage gaps visible instead of silently absent.
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
            comp = companies[key] = {
                "key": key,
                "issuer_name": rec.get("issuer_name", key),
                "exchange": (rec.get("exchange") or "").upper(),
                "ticker": (rec.get("ticker") or "").upper(),
                "confirmed": True,
                "boxes_by_insider": {},
            }
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


def load_view(data_dir: Path, config_path: Path) -> dict:
    """Load the newest data file + watchlist and build the UI view."""
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

    view = build_view(records, watchlist)
    view["data_file"] = data_file.name if data_file else None
    view["data_date"] = data_file.stem.replace("insider_", "") if data_file else None
    return view
