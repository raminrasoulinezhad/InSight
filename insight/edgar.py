# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""SEC EDGAR: insider trades for US companies, the US counterpart of SEDI.

Every US insider trade is filed with the SEC as a Form 4, as XML, on a free
public API. Unlike SEDI there is no CAPTCHA, no bot wall, no account and no
key. The SEC asks two things of an automated client: name itself with a
contact email in the User-Agent, and stay under 10 requests a second. The
contact lives in `.env` as INSIGHT_SEC_CONTACT (never in the repo), and this
client holds itself to 5 a second. See docs/us-insider-data-research.md for the
evaluation behind this module.

Two ways in, mirroring sedi.py:

  * `EdgarScraper.fetch(exchange, ticker, name)`: one company's Forms 4/5, for
    `marketbeat.scrape_many` (which handles the per-company cache, progress and
    the outcome tally).
  * `EdgarScraper.fetch_insider(name)`: every filing by one person or
    organisation, across every US company, for followed insiders.

A filing never changes once filed (a correction is a new filing), so each
one's XML is cached for good; a rerun reads each company's filing list and
fetches only what is new. Only the non-derivative table is read: buys, sales,
grants, exercises into shares, tax withholding, gifts. The derivative table
describes the options and warrants behind those, which would show each event
twice.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import InsiderTransaction

# ---- configuration ---------------------------------------------------------------

_REPO_ENV = Path(__file__).resolve().parent.parent / ".env"
_MAX_PER_SECOND = 5.0  # the SEC's ceiling is 10; half of it is a comfortable margin

US_EXCHANGES = frozenset(
    {"NYSE", "NASDAQ", "NYSEAMERICAN", "AMEX", "NYSEARCA", "ARCA", "BATS", "CBOE", "OTC"}
)

# SEC's exchange names (company_tickers_exchange.json) in the app's codes.
_SEC_EXCHANGES = {"nyse": "NYSE", "nasdaq": "NASDAQ", "otc": "OTC", "cboe": "CBOE"}


class EdgarNotConfigured(RuntimeError):
    """No contact email, so the SEC would refuse every request."""


def contact(env_path: Path = _REPO_ENV) -> str:
    """The contact email for the User-Agent: the environment, else `.env`."""
    value = os.environ.get("INSIGHT_SEC_CONTACT", "").strip()
    if not value:
        from .llm import load_dotenv  # the same tiny .env reader the LLM router uses

        value = load_dotenv(env_path).get("INSIGHT_SEC_CONTACT", "").strip()
    return value


def is_us(target: dict[str, Any]) -> bool:
    """True for a watchlist entry listed on a US exchange."""
    return str(target.get("exchange") or "").upper() in US_EXCHANGES


# ---- pure parsing (unit-tested; no network) ----------------------------------------

_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})

# Transaction codes (Form 4 instructions, section 8) as the app words them. The
# words matter beyond display: alarms skip anything mentioning a grant, an
# exercise or withholding (see notify._ALERT_SKIP_WORDS), since those are
# compensation, not market trades.
_CODES: dict[str, str] = {
    "P": "Buy",
    "S": "Sell",
    "A": "Grant or award",
    "M": "Exercise of options",
    "X": "Exercise of options",
    "O": "Exercise of options",
    "C": "Conversion of derivative",
    "F": "Tax withholding on vesting",
    "G": "Gift",
    "W": "Inheritance",
    "Z": "Deposit into or withdrawal from trust",
    "D": "Disposition to the issuer",
    "I": "Discretionary transaction",
    "L": "Small acquisition",
    "U": "Tender of shares",
    "E": "Expiration of short derivative",
    "H": "Expiration of long derivative",
    "K": "Equity swap",
    "V": "Voluntarily reported",
}


def transaction_type(code: str, acquired_disposed: str = "") -> str:
    """'P' -> 'Buy'. 'J' (other) says which way it went, since nothing else does."""
    code = (code or "").strip().upper()
    if code == "J":
        return "Other disposition" if acquired_disposed.upper() == "D" else "Other acquisition"
    return _CODES.get(code, f"Code {code}" if code else "Unknown")


def display_name(sec_name: str) -> str:
    """EDGAR's "SPROTT ERIC" / "Viljoen Natascha" as the app shows people,
    "Eric Sprott". Organisations ("2176423 Ontario Ltd.") are left as filed."""
    from .sedi import is_organisation

    name = " ".join((sec_name or "").split())
    if not name or is_organisation(name) or any(ch.isdigit() for ch in name):
        return name
    words = name.split(" ")
    suffix: list[str] = []
    while len(words) > 2 and words[-1].strip(".").lower() in _NAME_SUFFIXES:
        suffix.insert(0, words.pop())
    if len(words) < 2:
        return name
    ordered = words[1:] + words[:1] + suffix
    if name.isupper():
        ordered = [w if len(w) == 1 else w.title() for w in ordered]  # keep initials
    return " ".join(ordered)


def issuer_display(sec_name: str) -> str:
    """'NEWMONT Corp /DE/' -> 'Newmont Corp'. Drops the state-of-incorporation
    tag EDGAR appends and tames all-caps; everything else is kept as filed."""
    name = re.sub(r"\s*/[A-Z]{2,3}/\s*$", "", (sec_name or "").strip()).strip()
    if name.isupper():
        name = name.title()
    return name


def _text(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _number(text: str) -> float | None:
    try:
        return float(text) if text else None
    except ValueError:
        return None


def owner_role(relationship: ET.Element | None) -> str:
    """'CEO, Director' from a reportingOwnerRelationship block."""
    if relationship is None:
        return ""

    def flag(tag: str) -> bool:
        return _text(relationship, tag).lower() in ("1", "true")

    parts = []
    if flag("isOfficer"):
        parts.append(_text(relationship, "officerTitle") or "Officer")
    if flag("isDirector"):
        parts.append("Director")
    if flag("isTenPercentOwner"):
        parts.append("10% Owner")
    if flag("isOther"):
        parts.append(_text(relationship, "otherText") or "Other")
    return ", ".join(parts)


@dataclass
class ParsedFiling:
    """One Form 4/5: who filed it, about which company, and its trades."""

    issuer_cik: int
    issuer_name: str
    issuer_ticker: str
    owner_cik: int
    owner_name: str
    records: list[InsiderTransaction]


def parse_form4(xml: bytes | str, *, source_url: str = "", scraped_at: str = "") -> ParsedFiling:
    """Read an ownership document (Forms 3/4/5 share the schema).

    A filing can name several reporting owners (a fund and its manager filing
    together); the first is the one recorded, as every filer reports the same
    transactions and listing each would show every trade more than once.
    """
    root = ET.fromstring(xml)
    issuer = root.find("issuer")
    owner = root.find("reportingOwner")
    owner_id = owner.find("reportingOwnerId") if owner is not None else None
    issuer_name = issuer_display(_text(issuer, "issuerName"))
    ticker = _text(issuer, "issuerTradingSymbol").upper()
    insider = display_name(_text(owner_id, "rptOwnerName"))
    role = owner_role(owner.find("reportingOwnerRelationship") if owner is not None else None)

    records: list[InsiderTransaction] = []
    for tr in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = _number(_text(tr, "transactionAmounts/transactionShares/value"))
        price = _number(_text(tr, "transactionAmounts/transactionPricePerShare/value"))
        rec = InsiderTransaction(
            issuer_name=issuer_name,
            exchange="",
            ticker=ticker,
            insider_name=insider,
            insider_role=role,
            transaction_date=_text(tr, "transactionDate/value")[:10] or None,
            transaction_type=transaction_type(
                _text(tr, "transactionCoding/transactionCode"),
                _text(tr, "transactionAmounts/transactionAcquiredDisposedCode/value"),
            ),
            shares=round(shares) if shares is not None else None,
            avg_price=price,
            total_value=round(shares * price, 2) if shares is not None and price else None,
            currency="USD",
            source="edgar",
            source_url=source_url,
            scraped_at=scraped_at,
        )
        records.append(rec.classify())
    return ParsedFiling(
        issuer_cik=int(_text(issuer, "issuerCik") or 0),
        issuer_name=issuer_name,
        issuer_ticker=ticker,
        owner_cik=int(_text(owner_id, "rptOwnerCik") or 0),
        owner_name=insider,
        records=records,
    )


_OWNERSHIP_FORMS = frozenset({"4", "4/A", "5", "5/A"})


def ownership_filings(submissions: dict[str, Any], since: str) -> list[tuple[str, str, str]]:
    """(filing_date, accession, document) for each Form 4/5 filed on or after
    `since`, from a data.sec.gov submissions file or one of its extra pages. The
    document name loses its xslF345X0n/ prefix: that path is the rendered page,
    the bare one the XML."""
    recent = (submissions.get("filings") or {}).get("recent") or submissions
    rows = zip(
        recent.get("form") or [],
        recent.get("filingDate") or [],
        recent.get("accessionNumber") or [],
        recent.get("primaryDocument") or [],
        strict=False,
    )
    return [
        (filed, accession, doc.split("/")[-1])
        for form, filed, accession, doc in rows
        if form in _OWNERSHIP_FORMS and filed >= since
    ]


def _letters(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def pick_owner(hits: Iterable[tuple[int, str]], name: str) -> int | None:
    """The EDGAR entity (CIK, "LAST FIRST MIDDLE") that is this person or
    organisation, or None. Strict for the same reason as sedi.pick_insider_row:
    a wrong pick files a stranger's trades under someone the user follows.

    People need family and given name to agree (nicknames allowed); a middle
    initial breaks a tie ("Robert Martin Friedland" is FRIEDLAND ROBERT M, not
    FRIEDLAND ROBERT J). A tie that survives that is ambiguous, so None.
    """
    from .sedi import (
        _same_given_name,
        is_organisation,
        person_parts,
        same_listing_name,
    )

    hits = list(hits)
    if is_organisation(name):
        orgs = [cik for cik, entity in hits if same_listing_name(name, entity)]
        return orgs[0] if len(orgs) == 1 else None
    family, given = person_parts(name)
    words = name.split()
    middle = _letters(words[1])[:1] if len(words) > 2 else ""
    matches = []
    for cik, entity in hits:
        parts = re.sub(r"/[A-Z]{2}/", " ", entity).split()
        if len(parts) < 2 or _letters(parts[0]) != _letters(family):
            continue
        if given and not _same_given_name(given, parts[1]):
            continue
        matches.append((cik, _letters(parts[2])[:1] if len(parts) > 2 else ""))
    if len(matches) > 1 and middle:
        matches = [m for m in matches if m[1] == middle] or matches
    return matches[0][0] if len(matches) == 1 else None


def entity_query(name: str) -> str:
    """What to type into EDGAR's name lookup. It requires every word to match,
    and files people "FAMILY GIVEN MIDDLE-INITIAL", so "Robert Martin Friedland"
    finds nobody while "Friedland Robert" finds both Robert Friedlands, for
    `pick_owner` to tell apart."""
    from .sedi import _search_query, is_organisation, person_parts

    if is_organisation(name):
        return _search_query(name)
    family, given = person_parts(name)
    return f"{family} {given}".strip()


# ---- the network side --------------------------------------------------------------


class _RateLimiter:
    """At most `per_second` calls, across threads."""

    def __init__(self, per_second: float) -> None:
        self._gap = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._gap
        if delay > 0:
            time.sleep(delay)


_LIMITER = _RateLimiter(_MAX_PER_SECOND)  # one budget for every client in the process

Opener = Callable[[urllib.request.Request, float], bytes]


def _urlopen(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: bytes = resp.read()
        return data


class EdgarClient:
    """Talks to sec.gov politely, and remembers what it has read."""

    def __init__(
        self,
        contact_email: str,
        cache_dir: Path,
        *,
        opener: Opener = _urlopen,
        limiter: _RateLimiter = _LIMITER,
    ) -> None:
        if not contact_email:
            raise EdgarNotConfigured(
                "the SEC needs a contact email: set INSIGHT_SEC_CONTACT in .env"
            )
        self._ua = f"InSight personal research {contact_email}"
        self._cache = cache_dir
        self._open = opener
        self._limiter = limiter
        self.requests = 0  # for reporting how much a run cost

    def get(self, url: str) -> bytes:
        """GET with the declared User-Agent. Backs off and retries when the SEC
        says slow down (429) or is briefly unwell (5xx); anything else raises."""
        for attempt in range(4):
            self._limiter.wait()
            self.requests += 1
            req = urllib.request.Request(url, headers={"User-Agent": self._ua})
            try:
                return self._open(req, 30.0)
            except urllib.error.HTTPError as e:
                if e.code == 429 or 500 <= e.code < 600:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        raise RuntimeError(f"SEC kept refusing {url} (rate limit or outage)")

    def get_json(self, url: str) -> Any:
        return json.loads(self.get(url))

    # -- reference files, kept for a day ------------------------------------------
    def _daily(self, name: str, url: str) -> Any:
        path = self._cache / name
        if path.exists() and time.time() - path.stat().st_mtime < 86400:
            try:
                return json.loads(path.read_text())
            except ValueError:
                pass
        data = self.get_json(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def listings(self) -> dict[str, tuple[int, str, str]]:
        """TICKER -> (CIK, exchange code, name) for every SEC-registered listing."""
        raw = self._daily(
            "company_tickers_exchange.json",
            "https://www.sec.gov/files/company_tickers_exchange.json",
        )
        out: dict[str, tuple[int, str, str]] = {}
        for cik, name, ticker, exchange in raw.get("data") or []:
            if ticker:
                code = _SEC_EXCHANGES.get(str(exchange or "").lower(), str(exchange or "").upper())
                out.setdefault(str(ticker).upper(), (int(cik), code, str(name or "")))
        return out

    # -- filings ----------------------------------------------------------------------
    def filings_for(self, cik: int, since: str) -> list[tuple[str, str, str]]:
        """Form 4/5 filings since a date. The main file holds the latest ~1,000
        filings; for a company busy enough that those end inside the window,
        the older pages it lists are read too."""
        sub = self.get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        found = ownership_filings(sub, since)
        oldest = min((sub.get("filings") or {}).get("recent", {}).get("filingDate") or [since])
        for page in (sub.get("filings") or {}).get("files") or []:
            if oldest < since or str(page.get("filingTo") or "") < since:
                break
            extra = self.get_json(f"https://data.sec.gov/submissions/{page['name']}")
            found += ownership_filings(extra, since)
            oldest = str(page.get("filingFrom") or since)
        return found

    def document(self, folder_cik: int, accession: str, doc: str) -> bytes:
        """A filing's XML, read once and cached for good (filings never change)."""
        path = self._cache / "filings" / f"{accession}.xml"
        if path.exists():
            return path.read_bytes()
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{folder_cik}/"
            f"{accession.replace('-', '')}/{doc}"
        )
        data = self.get(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def find_entities(self, name: str) -> list[tuple[int, str]]:
        """EDGAR's own name lookup (the one its search box uses): (CIK, name)."""
        q = urllib.parse.quote(" ".join(name.replace(",", " ").split()))
        raw = self.get_json(f"https://efts.sec.gov/LATEST/search-index?keysTyped={q}")
        hits = (raw.get("hits") or {}).get("hits") or []
        return [(int(h["_id"]), str((h.get("_source") or {}).get("entity") or "")) for h in hits]


def align_to_watchlist(
    records: list[InsiderTransaction], watchlist: Iterable[dict[str, Any]]
) -> list[InsiderTransaction]:
    """Give a found US company the exchange code and name it has on the
    watchlist, so its trades file under the card the user already has.

    A US listing of the same ticker is the same company. So is a listing
    elsewhere with the same ticker *and* name: Ivanhoe Electric trades as IE in
    New York and Toronto, and the watchlist holds it as TSE:IE. Without the
    name check, the same symbol on two boards can be two unrelated companies.
    """
    from .sedi import same_listing_name

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for c in watchlist:
        by_ticker.setdefault(str(c.get("ticker") or "").upper(), []).append(c)
    for r in records:
        for c in by_ticker.get(r.ticker.upper(), []) if r.ticker else []:
            us = str(c.get("exchange") or "").upper() in US_EXCHANGES
            if us or same_listing_name(r.issuer_name, str(c.get("name") or "")):
                r.exchange = str(c["exchange"]).upper()
                r.issuer_name = str(c.get("name") or r.issuer_name)
                break
    return records


def sec_registrants(
    targets: Iterable[dict[str, Any]],
    listings: dict[str, tuple[int, str, str]],
    on_sedi: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[dict[str, Any]]:
    """The watchlist companies whose insiders file with the SEC: every US
    listing, plus a company listed elsewhere whose ticker the SEC also lists
    under the same name. Ivanhoe Electric is a US company that also trades in
    Toronto; on the watchlist as TSE:IE, its insiders still file Form 4s, not
    SEDI reports. A Canadian firm cross-listed in New York files no Form 4s,
    which costs one request to find out.

    One regulator per company: a non-US listing that SEDI already covers
    (`on_sedi`, EXCH:TICKER keys) stays SEDI's. Dual filers such as Waste
    Connections report the same vesting events to both, worded and split
    differently enough that the two copies cannot be matched, so fetching both
    would show each event twice."""
    from .sedi import same_listing_name

    out = []
    for t in targets:
        if is_us(t):
            out.append(t)
            continue
        key = f"{str(t.get('exchange') or '').upper()}:{str(t.get('ticker') or '').upper()}"
        hit = listings.get(str(t.get("ticker") or "").upper())
        if hit and key not in on_sedi and same_listing_name(str(t.get("name") or ""), hit[2]):
            out.append(t)
    return out


def never_fetched(cache_dir: Path, targets: Iterable[dict[str, Any]]) -> set[str]:
    """EXCH:TICKER keys with no EDGAR company cache yet. Their first fetch
    brings in two years of filings at once, which the caller folds into the
    alarms' baselines rather than announcing as new trades."""
    out = set()
    for t in targets:
        key = f"{str(t.get('exchange') or '').upper()}:{str(t.get('ticker') or '').upper()}"
        if not (cache_dir / (key.replace(":", "_") + ".json")).exists():
            out.add(key)
    return out


def _since(months: int) -> str:
    return (date.today() - timedelta(days=round(months * 30.44))).isoformat()


class EdgarScraper:
    """A scrape_many-compatible session over EDGAR (no browser to open)."""

    def __init__(self, cache_dir: Path, months: int = 24, client: EdgarClient | None = None):
        self._cache_dir = cache_dir
        self._months = months
        self._client = client

    def __enter__(self) -> EdgarScraper:
        if self._client is None:
            self._client = EdgarClient(contact(), self._cache_dir)
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    @property
    def client(self) -> EdgarClient:
        assert self._client is not None, "use EdgarScraper as a context manager"
        return self._client

    def fetch(self, exchange: str, ticker: str, name: str = "") -> list[InsiderTransaction]:
        """Every Form 4/5 trade in one company over the window. Stamped with the
        watchlist's own exchange, ticker and name, so it files exactly where the
        rest of the app looks for it."""
        listing = self.client.listings().get(ticker.upper())
        if listing is None:
            return []  # not an SEC registrant: a foreign issuer files no Form 4
        cik = listing[0]
        now = datetime.now(UTC).isoformat(timespec="seconds")
        out: list[InsiderTransaction] = []
        for _filed, accession, doc in self.client.filings_for(cik, _since(self._months)):
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
            try:
                parsed = parse_form4(
                    self.client.document(cik, accession, doc), source_url=url, scraped_at=now
                )
            except ET.ParseError:
                continue  # a paper-era or malformed filing; the rest still count
            for r in parsed.records:
                r.exchange, r.ticker = exchange.upper(), ticker.upper()
                r.issuer_name = name or r.issuer_name
                out.append(r)
        return out

    def fetch_insider(self, name: str) -> list[InsiderTransaction]:
        """Every Form 4/5 one person or organisation filed, in any US company.

        Records carry the ticker each filing names and the exchange SEC lists for
        it; none if EDGAR has no confident match for the name (most Canadian
        insiders have never filed in the US)."""
        owner = pick_owner(self.client.find_entities(entity_query(name)), name)
        if owner is None:
            return []
        listings = self.client.listings()
        now = datetime.now(UTC).isoformat(timespec="seconds")
        out: list[InsiderTransaction] = []
        for _filed, accession, doc in self.client.filings_for(owner, _since(self._months)):
            url = f"https://www.sec.gov/Archives/edgar/data/{owner}/{accession.replace('-', '')}/"
            try:
                parsed = parse_form4(
                    self.client.document(owner, accession, doc), source_url=url, scraped_at=now
                )
            except ET.ParseError:
                continue
            exchange = (listings.get(parsed.issuer_ticker) or (0, "", ""))[1]
            for r in parsed.records:
                r.exchange = exchange if r.ticker else ""
                out.append(r)
        return out
