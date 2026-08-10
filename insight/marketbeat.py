# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""MarketBeat insider-trades scraper.

Why MarketBeat: the authoritative Canadian source (SEDI, sedi.ca) sits behind
a ShieldSquare/PerfDrive bot wall that serves an hCaptcha challenge to
automated / datacenter-IP traffic — verified empirically from this host. The
SEDI-aggregator sites (canadianinsider.com, insidertracking.com) are behind
Cloudflare bot protection. MarketBeat's per-stock "insider-trades" pages are
reachable and carry recent insider activity for TSE-listed names (buys, sells,
issuer buybacks), so it is the pragmatic source for this proof of concept.

Caveats vs. SEDI (see README): MarketBeat normalizes/aggregates the data, may
lag SEDI by days, and does NOT cover small TSX-Venture issuers.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright
from playwright_stealth import Stealth

from .models import (
    InsiderTransaction,
    parse_int,
    parse_money,
    parse_us_date,
)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_BLOCK_MARKERS = ("just a moment", "captcha", "security verification", "perfdrive", "access denied")

# JS run in the page to pull every data table with its header + body rows.
_EXTRACT_JS = r"""
() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const tables = [];
  for (const t of document.querySelectorAll('table')) {
    const head = Array.from(
      t.querySelectorAll('thead th, thead td')
    ).map(c => norm(c.innerText));
    // header row sometimes lives in the first body row
    const headRow = head.length ? head
      : Array.from(t.querySelectorAll('tr')).slice(0, 1)
          .flatMap(r => Array.from(r.children).map(c => norm(c.innerText)));
    const rows = [];
    for (const r of t.querySelectorAll('tbody tr')) {
      const cells = Array.from(r.querySelectorAll('td')).map(c => {
        // keep the name/role split that MarketBeat renders as two lines
        const raw = c.innerText || '';
        return raw.split('\n').map(s => s.trim()).filter(Boolean).join('|');
      });
      if (cells.some(Boolean)) rows.push(cells);
    }
    tables.push({ head: headRow, rows });
  }
  return tables;
}
"""


class MarketBeatScraper:
    """Reusable browser session for scraping multiple tickers."""

    def __init__(self, headless: bool = True, slow_ms: int = 400):
        self._headless = headless
        self._slow_ms = slow_ms
        self._pw = None
        self._browser = None
        self._page: Page | None = None

    def __enter__(self) -> MarketBeatScraper:
        self._stealth_cm = Stealth().use_sync(sync_playwright())
        self._pw = self._stealth_cm.__enter__()
        self._browser = self._pw.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = self._browser.new_context(
            user_agent=_UA,
            locale="en-CA",
            viewport={"width": 1366, "height": 900},
        )
        self._page = ctx.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        finally:
            self._stealth_cm.__exit__(*exc)

    # ------------------------------------------------------------------
    @staticmethod
    def url_for(exchange: str, ticker: str) -> str:
        return (
            f"https://www.marketbeat.com/stocks/{exchange.upper()}/{ticker.upper()}/insider-trades/"
        )

    def fetch(self, exchange: str, ticker: str, issuer_hint: str = "") -> list[InsiderTransaction]:
        """Scrape one ticker's insider-trades page into normalized records."""
        page = self._page
        url = self.url_for(exchange, ticker)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(self._slow_ms + 1800)

        title = (page.title() or "").lower()
        if any(m in title for m in _BLOCK_MARKERS) or "perfdrive" in page.url:
            raise BotBlocked(f"{exchange}:{ticker} blocked by bot protection")

        # If the insider-trades URL redirected away, this ticker has no insider
        # page. Two sub-cases, told apart by the final URL:
        #   /stocks/<EXCH>/<TICKER>/  -> profile kept, insider page gone
        #                                = delisted/acquired -> flag as delisted
        #   /stocks/<EXCH>/           -> ticker dropped to the exchange list
        #                                = unknown/uncovered -> just "no data"
        if "insider-trades" not in (page.url or "").lower():
            if _no_insider_page_kind(page.url, exchange) == "delisted":
                raise CompanyDelisted(
                    f"{exchange}:{ticker} redirected to {page.url} — no insider page"
                )
            return []  # unknown / not covered

        # MarketBeat serves a generic "stock list" page when a ticker is
        # unknown; the real page title contains "Insider Trading Activity".
        if "insider trading activity" not in title:
            return []  # not covered

        issuer = self._issuer_from_title(page.title()) or issuer_hint or ticker
        tables = page.evaluate(_EXTRACT_JS)
        rows = self._pick_insider_table(tables)
        now = datetime.now(UTC).isoformat(timespec="seconds")

        out: list[InsiderTransaction] = []
        for cells in rows:
            rec = self._row_to_record(cells, issuer, exchange, ticker, url, now)
            if rec:
                out.append(rec.classify())
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _issuer_from_title(title: str) -> str:
        # "Franco-Nevada (FNV) Insider Trading Activity 2026" -> "Franco-Nevada"
        if "(" in title:
            return title.split("(")[0].strip()
        return ""

    @staticmethod
    def _pick_insider_table(tables: list[dict]) -> list[list[str]]:
        """Choose the table whose header looks like the insider grid."""
        for t in tables:
            head = " ".join(t.get("head", [])).lower()
            if "insider" in head and ("buy" in head or "shares" in head):
                return t["rows"]
        # fallback: first table that has a BUY/SELL-ish column
        for t in tables:
            if t["rows"] and len(t["rows"][0]) >= 6:
                return t["rows"]
        return []

    @staticmethod
    def _row_to_record(cells, issuer, exchange, ticker, url, now):
        # Expected columns:
        # 0 date | 1 "Name|Role" | 2 Buy/Sell | 3 shares | 4 avg price |
        # 5 total | 6 details
        if len(cells) < 6:
            return None
        date_iso = parse_us_date(cells[0])
        if not date_iso:
            return None  # skip header / non-data rows
        name_role = cells[1].split("|")
        name = name_role[0].strip()
        role = name_role[1].strip() if len(name_role) > 1 else ""
        ttype = cells[2].strip()
        shares = parse_int(cells[3])
        avg_price, cur1 = parse_money(cells[4])
        total, cur2 = parse_money(cells[5])
        return InsiderTransaction(
            issuer_name=issuer,
            exchange=exchange.upper(),
            ticker=ticker.upper(),
            insider_name=name,
            insider_role=role,
            transaction_date=date_iso,
            transaction_type=ttype,
            shares=shares,
            avg_price=avg_price,
            total_value=total,
            currency=cur1 or cur2,
            source="marketbeat",
            source_url=url,
            scraped_at=now,
        )


class BotBlocked(RuntimeError):
    """Raised when a page is intercepted by anti-bot protection."""


class CompanyDelisted(RuntimeError):
    """Raised when a ticker's insider page is gone (delisted/acquired): the URL
    redirects to the company profile with no insider-trades subpage."""


# ---- ticker-universe discovery ------------------------------------------------
# MarketBeat serves a per-exchange stock-list page (e.g. /stocks/TSE/) whose HTML
# links to every covered ticker as /stocks/<EXCH>/<TICKER>/. We enumerate those
# links to build a scrape universe far larger than a hand-kept watchlist, for
# free and with a plain HTTP GET (no browser). Coverage is only as broad as
# MarketBeat itself — it lists mainly large/mid-cap names and does not enumerate
# small-cap TSX-V/CSE issuers — so this is a best-effort free expansion, not the
# full Canadian universe.

_LIST_URL = "https://www.marketbeat.com/stocks/{exch}/"
_TICKER_LINK_RE = re.compile(r"/stocks/([A-Z]+)/([A-Z0-9.]+)/")


def _extract_tickers(html: str, exchange: str) -> list[str]:
    """Sorted, unique ticker symbols MarketBeat links on an exchange index page
    (pure, so it is unit-testable without a network fetch)."""
    exch = exchange.upper()
    found = {
        m.group(2).upper()
        for m in _TICKER_LINK_RE.finditer(html or "")
        if m.group(1).upper() == exch
    }
    return sorted(found)


def _no_insider_page_kind(final_url: str, exchange: str) -> str:
    """Classify why a fetched insider URL lacks `insider-trades`:
    'delisted' if the company profile is still present (…/stocks/EXCH/TICKER/…),
    'nodata'   if it redirected to the bare …/stocks/EXCH/ list or elsewhere."""
    m = re.search(rf"/stocks/{re.escape(exchange.lower())}/([^/?#]+)", (final_url or "").lower())
    return "delisted" if (m and m.group(1)) else "nodata"


def discover_tickers(exchanges: Iterable[str]) -> list[dict]:
    """Return [{name, exchange, ticker}] for every ticker MarketBeat lists on the
    given exchange index pages. Failures on one exchange never abort the rest."""
    out: dict[str, dict] = {}
    for raw in exchanges:
        exch = raw.strip().upper()
        if not exch:
            continue
        url = _LIST_URL.format(exch=exch)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", "replace")
        except Exception as e:  # unreachable / blocked / bad exchange code
            print(f"  discover {exch}: failed ({type(e).__name__}: {e})")
            continue
        tickers = _extract_tickers(html, exch)
        for tk in tickers:
            out[f"{exch}:{tk}"] = {"name": tk, "exchange": exch, "ticker": tk}
        print(f"  discover {exch}: {len(tickers)} tickers")
    return list(out.values())


# ---- per-company cache --------------------------------------------------------
# One JSON file per issuer so a re-run (cron double-fire, retry after a partial
# failure, or the app's day-to-day use) reuses still-fresh data instead of
# hammering MarketBeat again.


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / (key.replace(":", "_") + ".json")


def _read_cache(cache_dir: Path, key: str) -> dict | None:
    p = _cache_path(cache_dir, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return None


def _delete_cache(cache_dir: Path, key: str) -> None:
    """Drop a company's cache file (used when it is found delisted/acquired)."""
    with contextlib.suppress(FileNotFoundError):
        _cache_path(cache_dir, key).unlink()


def _load_delisted(path: Path | None) -> set[str]:
    if not path or not Path(path).exists():
        return set()
    try:
        return {str(k).upper() for k in json.loads(Path(path).read_text())}
    except (ValueError, OSError):
        return set()


def _save_delisted(path: Path | None, keys: set[str]) -> None:
    if path is None:
        return
    Path(path).write_text(json.dumps(sorted(keys), indent=2))


def _cache_age_hours(entry: dict) -> float:
    try:
        ts = datetime.fromisoformat(entry["scraped_at"])
    except (KeyError, ValueError):
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds() / 3600.0


def _write_cache(cache_dir: Path, key: str, recs: list[InsiderTransaction]) -> None:
    entry = {
        "key": key,
        "scraped_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "records": [r.to_dict() for r in recs],
    }
    _cache_path(cache_dir, key).write_text(json.dumps(entry, indent=2))


def _records_from_cache(entry: dict) -> list[InsiderTransaction]:
    return [InsiderTransaction(**r) for r in entry.get("records", [])]


def scrape_many(
    targets: Iterable[dict],
    headless: bool = True,
    cache_dir: Path | None = None,
    max_age_hours: float = 12.0,
    force: bool = False,
    delisted_path: Path | None = None,
    *,
    scraper_factory: Callable[[], Any] | None = None,
    source: str = "marketbeat",
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, list[InsiderTransaction]]:
    """targets: iterable of {name, exchange, ticker}. Returns {key: [...]}.

    When `cache_dir` is given, a company whose cached data is younger than
    `max_age_hours` is served from cache instead of being re-fetched (unless
    `force`). The browser is only launched if at least one company needs
    fetching. One target failing (block / not covered) never aborts the rest,
    and a failed fetch falls back to any stale cache so data is not lost.

    When a fetch finds a ticker delisted/acquired (its insider page is gone),
    its cache file is deleted and it is recorded in `delisted_path` so the app
    can hide its now-meaningless history. The flag is self-healing: a company
    that later returns data (or is served from a valid cache) is un-flagged.

    `scraper_factory` supplies the browser session (default: MarketBeatScraper);
    a compatible object is any context manager exposing
    `.fetch(exchange, ticker, name) -> list[InsiderTransaction]`. `source` names
    the data source: for anything other than the default MarketBeat the cache is
    kept in a per-source sub-folder so keys from different sources never collide.

    `on_progress(done, total, label)` is called before each fetch and once more
    when the batch ends, so a caller can drive a progress bar. It counts only
    companies actually fetched, not cache hits — a run that serves 200 of 215
    from cache does ~15 companies of work, and a bar counting the other 200 as
    progress would sit at 93% before the slow part even began. `label` names the
    company being fetched *now* (empty at the end). Exceptions from the callback
    are never allowed to break a scrape.
    """
    targets = list(targets)
    results: dict[str, list[InsiderTransaction]] = {}
    to_fetch: list[tuple[str, dict, dict | None]] = []
    delisted = _load_delisted(delisted_path)

    # Non-default sources get their own cache namespace (sub-folder) so a
    # company scraped by two sources doesn't overwrite the other's cache file.
    if cache_dir is not None and source != "marketbeat":
        cache_dir = cache_dir / source
        cache_dir.mkdir(parents=True, exist_ok=True)

    for t in targets:
        key = f"{t['exchange'].upper()}:{t['ticker'].upper()}"
        entry = _read_cache(cache_dir, key) if cache_dir else None
        if entry is not None and not force and _cache_age_hours(entry) < max_age_hours:
            recs = _records_from_cache(entry)
            results[key] = recs
            delisted.discard(key)  # a live cache entry means it is not delisted
            age = _cache_age_hours(entry)
            print(f"  [{key}] {t.get('name', '')}: cached ({len(recs)} txns, {age:.1f}h old)")
        else:
            to_fetch.append((key, t, entry))

    def progress(done: int, label: str) -> None:
        """Report, but never let a reporting bug take the scrape down with it."""
        if on_progress is None:
            return
        with contextlib.suppress(Exception):
            on_progress(done, len(to_fetch), label)

    if not to_fetch:
        progress(0, "")
        _save_delisted(delisted_path, delisted)
        return results

    factory = scraper_factory or (lambda: MarketBeatScraper(headless=headless))
    with factory() as mb:
        for done, (key, t, entry) in enumerate(to_fetch):
            # Announced before the fetch, not after: a bar that only moves on
            # completion shows nothing at all for the first (slowest) company,
            # which is exactly when the user is wondering if it hung.
            progress(done, str(t.get("name") or key))
            try:
                recs = mb.fetch(t["exchange"], t["ticker"], t.get("name", ""))
                results[key] = recs
                delisted.discard(key)  # data (or a valid empty page) came back
                if cache_dir:
                    _write_cache(cache_dir, key, recs)
                status = f"{len(recs)} transactions" if recs else "NOT COVERED"
                print(f"  [{key}] {t.get('name', '')}: {status}")
            except CompanyDelisted as e:
                results[key] = []
                delisted.add(key)
                if cache_dir:
                    _delete_cache(cache_dir, key)
                print(f"  [{key}] DELISTED — dropped from cache: {e}")
            except BotBlocked as e:
                results[key] = _records_from_cache(entry) if entry else []
                note = " (using stale cache)" if entry else ""
                print(f"  [{key}] BLOCKED: {e}{note}")
            except Exception as e:  # keep the batch alive
                results[key] = _records_from_cache(entry) if entry else []
                note = " (using stale cache)" if entry else ""
                print(f"  [{key}] ERROR: {type(e).__name__}: {str(e)[:120]}{note}")
            time.sleep(1.0)  # be polite between requests

    progress(len(to_fetch), "")
    _save_delisted(delisted_path, delisted)
    return results
