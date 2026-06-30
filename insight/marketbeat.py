# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

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

import time
from collections.abc import Iterable
from datetime import UTC, datetime

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


def scrape_many(
    targets: Iterable[dict], headless: bool = True
) -> dict[str, list[InsiderTransaction]]:
    """targets: iterable of {name, exchange, ticker}. Returns {ticker: [...]}.

    One target failing (block / not covered) never aborts the rest.
    """
    results: dict[str, list[InsiderTransaction]] = {}
    with MarketBeatScraper(headless=headless) as mb:
        for t in targets:
            key = f"{t['exchange'].upper()}:{t['ticker'].upper()}"
            try:
                recs = mb.fetch(t["exchange"], t["ticker"], t.get("name", ""))
                results[key] = recs
                status = f"{len(recs)} transactions" if recs else "NOT COVERED"
                print(f"  [{key}] {t.get('name', '')}: {status}")
            except BotBlocked as e:
                results[key] = []
                print(f"  [{key}] BLOCKED: {e}")
            except Exception as e:  # keep the batch alive
                results[key] = []
                print(f"  [{key}] ERROR: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(1.0)  # be polite between requests
    return results
