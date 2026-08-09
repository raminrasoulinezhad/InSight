# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Turn flat InsiderTransaction records into the shape the UI renders:

    watchlist company  ->  a list of individual transactions (newest first)

The company view keeps every transaction separate rather than aggregating per
insider: what matters is *who* traded and *when*, so each row stays its own
record (date, insider, role, buy/sell side, shares, price, value). Per-company
running totals (buy/sell counts, shares, dollar amounts, net) are still computed
for the header and for the client-side charts.

Transactions are ordered by transaction_date (the date the trade happened), not
by any filing/reporting date. The app layer never touches raw rows — it asks for
this structure.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import threading
from collections.abc import Callable
from datetime import date, timedelta
from glob import glob
from pathlib import Path
from typing import Any

from . import store

# ---- access cache -------------------------------------------------------------
# Search over names/companies is the app's main job, and every /api/data and
# /api/insiders request would otherwise re-read + re-parse + re-merge every dated
# snapshot from disk and rebuild the whole view. Instead we memoize by a cheap
# signature of the inputs (each snapshot's mtime+size, plus the watchlist and
# delisted files) so a rebuild happens ONLY when the underlying data actually
# changes — turning repeated access into an in-memory dict lookup. This beats an
# embedded DB for a single-user, few-MB dataset (no query/serialization layer);
# SQLite/FTS5 is the escalation path if the data ever outgrows memory.
Rec = dict[str, Any]  # one normalized transaction record (JSON object)
View = dict[str, Any]  # a built company/insiders view
DirSig = tuple[tuple[str, int, int], ...]  # (name, mtime_ns, size) per snapshot

_CACHE_LOCK = threading.Lock()
_records_cache: dict[str, tuple[DirSig, list[Rec]]] = {}  # data_dir -> (sig, records)
_view_cache: dict[tuple[Any, ...], View] = {}  # full signature -> view
_VIEW_CACHE_MAX = 64


def _dir_signature(data_dir: Path) -> DirSig:
    """Cheap fingerprint of the snapshot set: (name, mtime_ns, size) per file."""
    sig: list[tuple[str, int, int]] = []
    for p in sorted(glob(str(data_dir / "insider_*.json"))):
        try:
            st = os.stat(p)
        except OSError:
            continue
        sig.append((os.path.basename(p), st.st_mtime_ns, st.st_size))
    return tuple(sig)


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def months_ago(d: date, n: int) -> date:
    """The calendar date `n` months before `d` (clamped to a valid day)."""
    m = d.month - 1 - n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _file_date(path: str | Path) -> str:
    """The YYYY-MM-DD embedded in a snapshot filename ('' if none).

    Snapshots may carry a source tag (insider_YYYY-MM-DD.json for MarketBeat,
    insider_sedi_YYYY-MM-DD.json for SEDI), so the display/newest date is taken
    from the embedded date rather than raw filename sort order.
    """
    m = _DATE_RE.search(Path(path).name)
    return m.group(1) if m else ""


def latest_data_file(data_dir: Path) -> Path | None:
    """Newest data/insider_*.json by embedded date, or None if none exist yet."""
    files = glob(str(data_dir / "insider_*.json"))
    return Path(max(files, key=lambda f: (_file_date(f), f))) if files else None


def _avg_price(value: float, shares: int) -> float | None:
    """Volume-weighted average cost per share (total $ / total shares), or None
    when there are no shares to divide by."""
    return round(value / shares, 4) if shares else None


def _side(ttype: str) -> str:
    """Normalize a raw transaction type to buy / sell / other (for coloring)."""
    t = (ttype or "").lower()
    if "buy" in t:
        return "buy"
    if "sell" in t:
        return "sell"
    return "other"


def _empty_company(key: str, issuer_name: str, exchange: str, ticker: str, confirmed: bool) -> Rec:
    return {
        "key": key,
        "issuer_name": issuer_name,
        "exchange": exchange,
        "ticker": ticker,
        "confirmed": confirmed,
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
        "_insiders": set(),  # distinct name|role, dropped before serialization
        "transactions": [],
    }


def _add_company_txn(comp: Rec, rec: Rec) -> None:
    """Fold one record into a company's running totals and append it as a row."""
    name = (rec.get("insider_name") or "Unknown").strip()
    role = (rec.get("insider_role") or "").strip()
    comp["_insiders"].add(f"{name}|{role}")

    side = _side(rec.get("transaction_type", ""))
    shares = rec.get("shares") or 0
    value = rec.get("total_value") or 0.0
    comp["txn_count"] += 1
    if side == "buy":
        comp["buy_count"] += 1
        comp["buy_shares"] += shares
        comp["buy_value"] += value
        comp["net_value"] += value
    elif side == "sell":
        comp["sell_count"] += 1
        comp["sell_shares"] += shares
        comp["sell_value"] += value
        comp["net_value"] -= value
    else:
        comp["other_count"] += 1
    if rec.get("currency") and not comp["currency"]:
        comp["currency"] = rec["currency"]
    d = rec.get("transaction_date")
    if d and (comp["latest_date"] is None or d > comp["latest_date"]):
        comp["latest_date"] = d

    comp["transactions"].append(
        {
            "date": d,
            "insider_name": name,
            "insider_role": role,
            "entity_type": rec.get("entity_type", "unknown"),
            "is_issuer_buyback": bool(rec.get("is_issuer_buyback")),
            "type": rec.get("transaction_type", ""),
            "side": side,
            "shares": shares,
            "avg_price": rec.get("avg_price"),
            "total_value": round(value, 2),
            "currency": rec.get("currency", ""),
        }
    )


def build_view(records: list[Rec], watchlist: list[Rec]) -> View:
    """Group records into companies -> a list of individual transactions.

    `watchlist` (companies.json entries) defines which companies appear: issuers
    with zero scraped transactions still show (as empty cards) to make coverage
    gaps visible, and records for issuers not on the watchlist are ignored so a
    removed company disappears instead of lingering from stale scraped data.

    Each company carries its transactions newest-first (by transaction_date) plus
    running buy/sell totals for the header and charts. Transactions are kept
    separate — not aggregated per insider — so every trade shows who and when.
    """
    companies: dict[str, dict[str, Any]] = {}

    def company_key(exchange: str, ticker: str) -> str:
        return f"{(exchange or '').upper()}:{(ticker or '').upper()}"

    # seed from the watchlist so uncovered names still render
    for c in watchlist:
        key = company_key(c.get("exchange", ""), c.get("ticker", ""))
        companies[key] = _empty_company(
            key,
            c.get("name", key),
            (c.get("exchange") or "").upper(),
            (c.get("ticker") or "").upper(),
            c.get("confirmed", True),
        )

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
        _add_company_txn(comp, rec)

    # finalize: sort rows newest-first, round money, drop internal fields
    out_companies = []
    for comp in companies.values():
        txns = comp["transactions"]
        # stable double-sort: name ascending, then transaction_date descending,
        # so rows read newest-first with a tidy name order within a single date.
        txns.sort(key=lambda t: t["insider_name"])
        txns.sort(key=lambda t: t["date"] or "", reverse=True)
        comp["insider_count"] = len(comp.pop("_insiders"))
        comp["buy_value"] = round(comp["buy_value"], 2)
        comp["sell_value"] = round(comp["sell_value"], 2)
        comp["net_value"] = round(comp["net_value"], 2)
        out_companies.append(comp)

    # companies with activity first, then alphabetical
    out_companies.sort(key=lambda c: (-c["txn_count"], c["issuer_name"]))
    return {
        "companies": out_companies,
        "total_companies": len(out_companies),
        "total_transactions": sum(c["txn_count"] for c in out_companies),
        "covered_companies": sum(1 for c in out_companies if c["txn_count"]),
    }


# ---- insiders view: same records, grouped by insider across companies --------
# The company view answers "who traded THIS company?"; the insiders view answers
# "what did THIS person trade, across every watchlist company?". It is a pure
# re-slice of the same records — no extra scraping — so its coverage is exactly
# the companies on the watchlist.


def _person_key(name: str) -> str:
    """Collapse case/whitespace so the same insider merges across companies."""
    return " ".join((name or "").split()).lower()


def _empty_person(name: str, entity_type: str) -> View:
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


def _empty_person_company(key: str, issuer_name: str, exchange: str, ticker: str) -> View:
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


def _accumulate(agg: dict[str, Any], rec: Rec) -> None:
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


def build_insiders_view(records: list[Rec]) -> View:
    """Group records into insiders -> the companies they traded.

    Unlike the company view, this is NOT gated to the watchlist: it spans EVERY
    company present in the scraped data (e.g. the broader universe pulled in by
    `insight-scrape --discover`), because the whole point of the insiders view is
    to follow a person's trades across all companies, watchlist or not. Issuer
    buybacks are excluded — they are the company trading its own stock, not a
    person/insider.
    """

    def company_key(exchange: str, ticker: str) -> str:
        return f"{(exchange or '').upper()}:{(ticker or '').upper()}"

    people: dict[str, dict[str, Any]] = {}
    for rec in records:
        if rec.get("is_issuer_buyback"):
            continue
        ckey = company_key(rec.get("exchange", ""), rec.get("ticker", ""))

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
                rec.get("issuer_name") or ckey,
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
            c["avg_buy_price"] = _avg_price(c["buy_value"], c["buy_shares"])
            c["avg_sell_price"] = _avg_price(c["sell_value"], c["sell_shares"])
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
        "insiders": out_people,
        "total_insiders": len(out_people),
        "total_transactions": sum(p["txn_count"] for p in out_people),
        "total_companies": len({c["key"] for p in out_people for c in p["companies"]}),
    }


def _txn_key(r: Rec) -> tuple[Any, ...]:
    """Stable identity of a single transaction, for cross-file dedup.

    Two scrapes of the same underlying filing normalize to identical fields, so
    keying on all of (issuer, insider, date, type, shares, value) collapses the
    re-fetched rows while preserving genuinely distinct trades. A rare MarketBeat
    revision to an existing row would key differently and appear as an extra row.
    """
    return (
        (r.get("exchange") or "").upper(),
        (r.get("ticker") or "").upper(),
        (r.get("insider_name") or "").strip().lower(),
        (r.get("insider_role") or "").strip().lower(),
        r.get("transaction_date"),
        (r.get("transaction_type") or "").strip().lower(),
        r.get("shares"),
        r.get("total_value"),
    )


def load_all_records(data_dir: Path) -> list[Rec]:
    """Every dated data file as one deduplicated record set.

    Each scrape writes only a snapshot of MarketBeat's most-recent page, so no
    single file spans much history. Merging all of them — oldest → newest so a
    newer scrape wins on an identical-transaction collision — lets the app's
    window deepen over time as the scraper runs. This is the only free way to
    accumulate a multi-year history (MarketBeat serves no deep backfill).

    The merge itself is delegated to `store.sync`, which keeps the result folded
    into a single consolidated file and re-reads only genuinely new snapshots —
    otherwise a cold start would re-parse the entire (heavily self-repeating)
    snapshot pile just to rebuild the same records. On top of that the result is
    memoized by directory signature, so repeated access (search/tab-switch) is an
    in-memory lookup until a scrape changes the files.
    """
    key = str(data_dir)
    sig = _dir_signature(data_dir)
    with _CACHE_LOCK:
        hit = _records_cache.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]

    records, _manifest = store.sync(data_dir, _txn_key)

    with _CACHE_LOCK:
        _records_cache[key] = (sig, records)
    return records


def _company_key(exchange: str | None, ticker: str | None) -> str:
    return f"{(exchange or '').upper()}:{(ticker or '').upper()}"


def _delisted_keys(config_path: Path) -> set[str]:
    """Load EXCH:TICKER keys the scraper flagged as delisted/acquired.

    Lives next to the watchlist (app_dir/delisted.json). These are filtered out
    of both views so acquired/delisted names stop showing stale activity; the
    underlying snapshots are left intact (the flag is reversible)."""
    p = config_path.parent / "delisted.json"
    if not p.exists():
        return set()
    try:
        return {str(k).upper() for k in json.loads(p.read_text())}
    except (ValueError, OSError):
        return set()


def _load_records(
    data_dir: Path, config_path: Path, months: int | None, days: int | None = None
) -> tuple[list[Rec], list[Rec]]:
    """Shared loader for the company and insiders views.

    Returns (records, watchlist). Records are the deduplicated union of ALL
    dated snapshots (see load_all_records), minus anything flagged delisted.
    The window keeps only transactions dated within the selected span: `days`
    (used for the sub-month week options) takes precedence when given, else
    `months` keeps the last N calendar months.
    """
    watchlist = []
    if config_path.exists():
        cfg = json.loads(config_path.read_text())
        watchlist = [
            c for c in cfg.get("companies", []) if not str(c.get("name", "")).startswith("_")
        ]

    records = load_all_records(data_dir)

    delisted = _delisted_keys(config_path)
    if delisted:
        records = [
            r for r in records if _company_key(r.get("exchange"), r.get("ticker")) not in delisted
        ]
        watchlist = [
            c for c in watchlist if _company_key(c.get("exchange"), c.get("ticker")) not in delisted
        ]

    cutoff = None
    if days:
        cutoff = (date.today() - timedelta(days=int(days))).isoformat()
    elif months:
        cutoff = months_ago(date.today(), int(months)).isoformat()
    if cutoff is not None:
        records = [r for r in records if (r.get("transaction_date") or "") >= cutoff]

    return records, watchlist


def _stamp(view: View, data_dir: Path, months: int | None, days: int | None = None) -> View:
    """Attach shared metadata: newest snapshot date + how many were merged.

    Counted from the store's manifest unioned with what's on disk, so the
    reported history doesn't shrink when folded snapshots are pruned away.
    """
    names = set(store.folded_names(data_dir))
    names.update(p.name for p in store.snapshot_paths(data_dir))
    newest = max(names, key=lambda n: (_file_date(n), n)) if names else None
    view["data_file"] = newest
    view["data_date"] = _file_date(newest) if newest else None
    view["range_months"] = int(months) if months and not days else None
    view["range_days"] = int(days) if days else None
    view["history_files"] = len(names)
    return view


def _cached_view(
    kind: str,
    data_dir: Path,
    config_path: Path,
    months: int | None,
    days: int | None,
    builder: Callable[[], View],
) -> View:
    """Return a memoized built view, rebuilding only when an input file changes.

    Keyed by the snapshot-set signature plus the watchlist and delisted file
    signatures, so any scrape / add / remove / delist naturally invalidates it.
    """
    key = (
        kind,
        str(data_dir),
        _dir_signature(data_dir),
        _file_signature(config_path),
        _file_signature(config_path.parent / "delisted.json"),
        months,
        days,
    )
    with _CACHE_LOCK:
        cached = _view_cache.get(key)
        if cached is not None:
            return cached

    view = builder()

    with _CACHE_LOCK:
        if len(_view_cache) >= _VIEW_CACHE_MAX:
            _view_cache.clear()  # bound memory; signatures churn as data updates
        _view_cache[key] = view
    return view


def load_view(
    data_dir: Path, config_path: Path, months: int | None = None, days: int | None = None
) -> View:
    """Build the company view from the merged history + watchlist (cached)."""

    def build() -> View:
        records, watchlist = _load_records(data_dir, config_path, months, days)
        return _stamp(build_view(records, watchlist), data_dir, months, days)

    return _cached_view("company", data_dir, config_path, months, days, build)


def load_insiders_view(
    data_dir: Path, config_path: Path, months: int | None = None, days: int | None = None
) -> View:
    """Build the insiders view from the merged history (spans all scraped
    companies, not just the watchlist). Cached like the company view."""

    def build() -> View:
        records, _watchlist = _load_records(data_dir, config_path, months, days)
        return _stamp(build_insiders_view(records), data_dir, months, days)

    return _cached_view("insiders", data_dir, config_path, months, days, build)
