# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""What followed insiders trade, company by company.

The app is company-first: an insider only appears once a company they traded in
has been fetched. Following a person turns that around. A SEDI insider search
returns every company they filed against, and the ones not on the watchlist
are exactly the companies the user may want to add. This module keeps the last
search per insider and turns them into one list of suggestions.

State is a single JSON object keyed by the insider's name as the app shows it
(see paths.insider_finds_file()). Whether a company is on the watchlist is not
stored: it changes the moment the user clicks + Add, so it is worked out when
the suggestions are read.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import InsiderTransaction

_WRITE_LOCK = threading.Lock()


def summarize(
    records: Iterable[InsiderTransaction], unresolved: Iterable[str] = ()
) -> dict[str, Any]:
    """One insider's search as {searched_at, companies, unresolved}.

    `companies` holds one row per issuer, most active first. A row whose issuer
    could not be matched to a ticker keeps its name and has `resolved` False:
    those obscure venture names are exactly what the search exists to surface.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for r in records:
        issuer = r.issuer_name or "(unnamed issuer)"
        row = grouped.get(issuer)
        if row is None:
            row = grouped[issuer] = {
                "issuer_name": issuer,
                "exchange": r.exchange,
                "ticker": r.ticker,
                "txn_count": 0,
                "latest_date": "",
                "resolved": bool(r.ticker),
            }
        row["txn_count"] += 1
        row["latest_date"] = max(row["latest_date"], r.transaction_date or "")
    return {
        "searched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "companies": sorted(grouped.values(), key=lambda c: -int(c["txn_count"])),
        "unresolved": list(dict.fromkeys(unresolved)),
    }


def load(path: Path) -> dict[str, dict[str, Any]]:
    """Every saved search; {} when unset or unreadable (never blocks the app)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return (
        {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    )


def save(path: Path, name: str, result: dict[str, Any]) -> None:
    """Replace one insider's saved search. Locked read-modify-write, then an
    atomic rename with a unique temp name, since the server is threaded."""
    with _WRITE_LOCK:
        finds = load(path)
        finds[name] = result
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(finds, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def age_hours(result: dict[str, Any], now: datetime | None = None) -> float:
    """How long ago a saved search ran; infinite when it never did."""
    try:
        then = datetime.fromisoformat(str(result.get("searched_at")))
    except (TypeError, ValueError):
        return float("inf")
    return ((now or datetime.now(UTC)) - then).total_seconds() / 3600


def suggestions(
    finds: dict[str, dict[str, Any]], on_watchlist: Callable[[str, str], bool]
) -> list[dict[str, Any]]:
    """Companies followed insiders trade that are not on the watchlist.

    One row per company, naming which of the followed insiders trade it, most
    recently active first: a company two followed insiders both bought into last
    week says more than one with a single filing from last year.
    """
    rows: dict[str, dict[str, Any]] = {}
    for name, result in finds.items():
        for c in result.get("companies") or []:
            if not c.get("resolved"):
                continue
            exchange, ticker = str(c.get("exchange") or ""), str(c.get("ticker") or "")
            if on_watchlist(exchange, ticker):
                continue
            key = f"{exchange.upper()}:{ticker.upper()}"
            row = rows.setdefault(
                key,
                {
                    "issuer_name": c.get("issuer_name") or ticker,
                    "exchange": exchange,
                    "ticker": ticker,
                    "insiders": [],
                    "txn_count": 0,
                    "latest_date": "",
                },
            )
            if name not in row["insiders"]:
                row["insiders"].append(name)
            row["txn_count"] += int(c.get("txn_count") or 0)
            row["latest_date"] = max(row["latest_date"], str(c.get("latest_date") or ""))
    return sorted(
        rows.values(),
        key=lambda r: (r["latest_date"], len(r["insiders"]), r["txn_count"]),
        reverse=True,
    )


def unmatched(finds: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Companies followed insiders trade that no listing could be matched to.

    Mostly renamed companies (SEDI keeps the name a company filed under:
    "Discovery Silver" is now "Discovery Mining") or unlisted ones. They cannot
    be added with one click, but they are still worth knowing about.
    """
    rows: dict[str, dict[str, Any]] = {}
    for name, result in finds.items():
        for c in result.get("companies") or []:
            if c.get("resolved"):
                continue
            issuer = str(c.get("issuer_name") or "")
            row = rows.setdefault(issuer, {"issuer_name": issuer, "insiders": []})
            if name not in row["insiders"]:
                row["insiders"].append(name)
    return sorted(rows.values(), key=lambda r: r["issuer_name"].lower())
