# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""SEDI (sedi.ca) insider-transactions scraper — Canada's authoritative source.

Why this exists: MarketBeat (the default source) does not cover small
TSX-Venture issuers, so names like West Red Lake Gold (TSXV:WRLG) show up empty.
SEDI carries *every* Canadian insider filing, including micro-caps.

The catch: SEDI's query controller (SVTItdController) sits behind a ShieldSquare
/ PerfDrive bot wall that serves an hCaptcha to automated / datacenter-IP
traffic. So this scraper runs **headful** with a **persistent browser profile**
(see paths.sedi_profile_dir): you solve the challenge once in the visible window,
the session cookie persists, and subsequent runs reuse it. If the wall is up and
nobody can solve it (headless), the fetch raises BotBlocked and the batch falls
back to cache — exactly like the MarketBeat path.

Two ways in, sharing one form and one parser:
  * `fetch(exchange, ticker)` — search by issuer name (SELECT_TYPE=8). One
    company's report, grouped by insider. This is what a normal scrape uses.
  * `fetch_insider(family_name)` — search by insider family name
    (SELECT_TYPE=5). One person's filings across EVERY issuer, which is the only
    way to discover a company you are not already tracking: the rest of the app
    is company-first, so a person trading off-watchlist is otherwise invisible.
    Records come back without tickers (SEDI reports legal names), so run
    `resolve_tickers` over them.

Design split (mirrors marketbeat.py):
  * Pure, unit-tested parsing — `_parse_report_rows`, `_report_row_to_record`,
    `_transaction_type`, `_parse_sedi_date` — turns the ITD results grid into
    `InsiderTransaction`s. Rows are anchored by their transaction id and grouped
    by "Label: value" header rows, so one parser reads both report shapes.
  * Browser glue — `SediScraper` — drives the public ITD wizard. Its selectors
    are best-effort (SEDI is a legacy Struts app); run once with `capture_dir`
    set to dump the live HTML if a selector needs adjusting.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from . import profiles
from .marketbeat import _UA, BotBlocked
from .models import InsiderTransaction, parse_int, parse_money
from .paths import sedi_page_filename

# ---- pure parsing (unit-tested; no network) -----------------------------------

# SEDI "nature of transaction" is a numeric code + descriptive text, e.g.
# "10 - Acquisition or disposition in the public market" or "50 - Grant of
# options". We trust SEDI's own text (never a hand-kept code map, which is
# error-prone), collapsing only the "Acquisition or disposition …" natures to
# Buy/Sell by direction so the UI's net-buyer/seller filter works.
_MARKET_CODES = {"10", "11"}  # public-market / private acquisition-or-disposition

_DATE_FMTS = ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y")


def _parse_sedi_date(text: str) -> str | None:
    """SEDI dates vary by view; normalize to ISO yyyy-mm-dd or None."""
    s = (text or "").strip()
    if not s:
        return None
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        return m.group(0)
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _nature_code(nature: str) -> str:
    """'10 - Acquisition ...' -> '10'."""
    m = re.match(r"\s*(\d{1,3})", nature or "")
    return m.group(1) if m else ""


_SUFFIX_RE = re.compile(
    r"[\s,]*\b(inc|incorporated|ltd|limited|corp|corporation|co|company|plc|sa|nv|lp)\b\.?$",
    re.I,
)


def _search_query(name: str) -> str:
    """Trim trailing corporate suffixes ('Ltd.', 'Corporation', …) so a SEDI
    'starts with' issuer search matches the stored legal name. Idempotently
    strips stacked suffixes, e.g. 'Foo Holdings Co Ltd.' -> 'Foo Holdings'."""
    q = (name or "").strip()
    prev = None
    while prev != q:
        prev = q
        q = _SUFFIX_RE.sub("", q).strip()
    return q or (name or "").strip()


def _nature_text(nature: str) -> str:
    """'50 - Grant of options' -> 'Grant of options'."""
    return re.sub(r"^\s*\d+\s*-\s*", "", nature or "").strip()


def _transaction_type(nature: str, acquired: int | None, disposed: int | None) -> str:
    """Buy/Sell for market acquisitions-or-dispositions (by direction); otherwise
    SEDI's own nature wording (Grant of options, Exercise of rights, …)."""
    text = _nature_text(nature)
    is_market = (
        text.lower().startswith("acquisition or disposition")
        or _nature_code(nature) in _MARKET_CODES
    )
    if is_market:
        if acquired:
            return "Buy"
        if disposed:
            return "Sell"
    return text or "Other"


# SEDI's ITD report (an Actuate document) is not one clean grid: it's a long,
# in-DOM-order stream of rows where an insider's identity is a *group header*
# ("Insider name: Billan, Jason") that applies to the transaction rows beneath
# it, until the next header. Each transaction row has a fixed shape anchored by
# a numeric Transaction ID, with the columns following it:
#   [ID] [txn date] [filing date] [ownership] [nature] [signed amount] [price] …
# The amount is a single signed field (+acquired / -disposed).

_TXN_ID_RE = re.compile(r"^\d{6,}$")  # SEDI transaction IDs are 7-ish digit numbers
_LABEL_INSIDER = "insider name"
_LABEL_RELATION = "insider's relationship to issuer"
# An issuer search groups by insider; an insider search groups by issuer. Both
# grids use the same "Label: value" header rows, so tracking all three labels
# lets one parser read either report.
_LABEL_ISSUER = "issuer name"


_CA_EXCHANGES = {"TSE", "TSX", "TSXV", "CSE", "NEO", "CNSX"}


def is_canadian(target: dict) -> bool:
    """SEDI only lists Canadian issuers. Use the explicit country when present,
    else infer from the exchange, so US names are skipped (no wasted searches)."""
    country = (target.get("country") or "").upper()
    if country:
        return country == "CA"
    return (target.get("exchange") or "").upper() in _CA_EXCHANGES


def _flip_name(name: str) -> str:
    """SEDI files names 'Last, First'; render 'First Last' to match other sources."""
    parts = [p.strip() for p in (name or "").split(",")]
    if len(parts) == 2 and parts[0] and parts[1]:
        return f"{parts[1]} {parts[0]}"
    return (name or "").strip()


def _clean_relationship(value: str) -> str:
    """'5 - Senior Officer of Issuer' -> 'Senior Officer of Issuer'."""
    return re.sub(r"^\s*\d+\s*-\s*", "", value or "").strip()


def _group_label(row: list[str]) -> tuple[str, str] | None:
    """A 'Label: value' header row -> (label_lower, value); else None."""
    if len(row) >= 2 and row[0].strip().endswith(":"):
        return row[0].strip().rstrip(":").strip().lower(), row[1].strip()
    return None


def _report_row_to_record(
    row: list[str],
    insider: str,
    role: str,
    issuer: str,
    exchange: str,
    ticker: str,
    url: str,
    now: str,
) -> InsiderTransaction | None:
    """Map one transaction row (anchored by its Transaction ID) to a record, or
    None if it isn't a transaction row / has no share movement."""
    j = next((k for k, c in enumerate(row) if _TXN_ID_RE.match((c or "").strip())), -1)
    if j < 0 or j + 5 >= len(row):
        return None
    date_iso = _parse_sedi_date(row[j + 1])
    if not date_iso:
        return None
    nature = (row[j + 4] or "").strip()
    signed = parse_int(row[j + 5])  # parse_int keeps the '-' sign, drops '+'
    if not signed:
        return None  # opening balance / expiry / no movement
    acquired = signed if signed > 0 else None
    disposed = -signed if signed < 0 else None
    price, _ = parse_money(row[j + 6]) if j + 6 < len(row) else (None, "")
    shares = abs(signed)
    total = (shares * price) if price else None
    return InsiderTransaction(
        issuer_name=issuer,
        exchange=exchange.upper(),
        ticker=ticker.upper(),
        insider_name=_flip_name(insider) or ticker.upper(),
        insider_role=role,
        transaction_date=date_iso,
        transaction_type=_transaction_type(nature, acquired, disposed),
        shares=shares,
        avg_price=price,
        total_value=total,
        currency="CAD",  # SEDI is a Canadian system
        source="sedi",
        source_url=url,
        scraped_at=now,
    )


def _parse_report_rows(
    rows: list[list[str]],
    issuer: str,
    exchange: str,
    ticker: str,
    url: str,
    now: str,
) -> list[InsiderTransaction]:
    """Walk the report's rows in order, carrying the current issuer/insider/role
    from group-header rows onto the transaction rows beneath them.

    `issuer`/`exchange`/`ticker` are *fallbacks*, used until (or unless) the grid
    names an issuer itself. That is what lets one parser read both reports:

      * an **issuer search** returns one company grouped by insider, and names no
        issuer in the body — so the passed-in company stands for every row;
      * an **insider search** returns one person across many companies, grouped by
        issuer — so each "Issuer name:" header switches the company mid-report.

    A row's exchange/ticker are only carried over while the issuer still matches
    the one passed in. Once the grid moves to a different company, they are
    cleared rather than guessed: SEDI reports issuer *names*, not tickers, and
    stamping company A's ticker onto company B's trades would silently corrupt
    the data. `resolve_tickers` fills them in afterwards.
    """
    insider = ""
    role = ""
    current_issuer = issuer
    out: list[InsiderTransaction] = []
    for row in rows:
        label = _group_label(row)
        if label:
            key, value = label
            if key == _LABEL_INSIDER:
                insider = value
            elif key == _LABEL_RELATION:
                role = _clean_relationship(value)
            elif key == _LABEL_ISSUER and value:
                current_issuer = value
            continue
        same = _same_issuer(current_issuer, issuer)
        rec = _report_row_to_record(
            row,
            insider,
            role,
            current_issuer,
            exchange if same else "",
            ticker if same else "",
            url,
            now,
        )
        if rec:
            out.append(rec.classify())
    return out


def resolve_tickers(
    records: list[InsiderTransaction],
    resolver: Callable[[str], list[dict[str, Any]]] | None = None,
) -> tuple[list[InsiderTransaction], list[str]]:
    """Fill in exchange/ticker on records that only carry an issuer name.

    An insider search gives legal names ('West Red Lake Gold Mines Ltd.') where
    the rest of the app is keyed on EXCH:TICKER, so each distinct name is looked
    up once through the same resolver the "Add a company by name" box uses.

    Returns `(records, unresolved_names)`. Records whose issuer could not be
    resolved are **returned unchanged, not dropped** — a name that TradingView
    does not carry is exactly the obscure venture company this whole feature
    exists to surface, and silently discarding it would defeat the point. The
    caller reports them so they can be added by hand.

    Only Canadian listings are accepted: SEDI is a Canadian system, and a
    same-named US issuer would be the wrong company.
    """
    if resolver is None:
        from .issuers import search_issuers  # imported late: it does network I/O

        resolver = search_issuers

    resolved: dict[str, tuple[str, str]] = {}
    unresolved: list[str] = []
    for name in dict.fromkeys(r.issuer_name for r in records if r.issuer_name and not r.ticker):
        try:
            candidates = resolver(name)
        except Exception:
            candidates = []
        pick = next((c for c in candidates if (c.get("country") or "").upper() == "CA"), None)
        if pick and pick.get("ticker") and pick.get("exchange"):
            resolved[name] = (str(pick["exchange"]).upper(), str(pick["ticker"]).upper())
        else:
            unresolved.append(name)

    for rec in records:
        if rec.ticker or rec.issuer_name not in resolved:
            continue
        rec.exchange, rec.ticker = resolved[rec.issuer_name]
    return records, unresolved


def _same_issuer(a: str, b: str) -> bool:
    """Loose issuer-name equality, for deciding whether a passed-in ticker still
    applies. SEDI writes the full legal name ('Athabasca Oil Corporation') where
    the watchlist may hold a shorter one ('Athabasca Oil'), so compare on the
    normalized search form and accept either being a prefix of the other."""
    x, y = _search_query(a).lower(), _search_query(b).lower()
    if not x or not y:
        return False
    return x.startswith(y) or y.startswith(x)


# ---- browser glue (best-effort selectors; confirm live with capture_dir) ------

_ACCESS_URL = "https://www.sedi.ca/sedi/SVTReportsAccessController?locale=en_CA"
_ITD_URL = "https://www.sedi.ca/sedi/SVTItdController?locale=en_CA"

# SELECT_TYPE values, read off the live ITD form. "5 = Insider family name" is
# what makes a person-first search possible at all: it spans every issuer the
# person has filed against, including the TSX-V/CSE micro-caps MarketBeat does
# not list. (The form also offers "4 = Insider company name", unused here.)
_SELECT_TYPE_ISSUER = "8"
_SELECT_TYPE_INSIDER = "5"

# SEDI sits behind Radware Bot Manager (formerly ShieldSquare / PerfDrive). When
# it decides the browser is a bot it serves a hard "403 Forbidden" block page
# carrying a hex "Transaction ID:" reference — NOT a solvable hCaptcha. We match
# the block by page *title* (the 403) and by the Radware block-copy in the
# *body*, so it is recognized as a wall instead of being mistaken for a normal
# page (which made the scraper barrel into the form, fail to find it, and return
# an empty result silently). Body markers are Radware-specific phrasing so they
# don't collide with SEDI's own "Transaction ..." result columns.
_WALL_TITLE_MARKERS = (
    "shieldsquare",
    "captcha",
    "just a moment",
    "access denied",
    "403 forbidden",
    "forbidden",
)
_WALL_BODY_MARKERS = (
    "access to this page has been denied",
    "you are using automation tools",
    "why did this happen",
    "radware",
)


def _is_bot_wall(title: str, url: str, body: str = "") -> bool:
    """True if the current page is the Radware/ShieldSquare bot wall. Pure so it
    can be unit-tested; `_walled()` supplies the live title/url/body."""
    t = (title or "").lower()
    u = (url or "").lower()
    b = (body or "").lower()
    if "perfdrive" in u:
        return True
    if any(m in t for m in _WALL_TITLE_MARKERS):
        return True
    return any(m in b for m in _WALL_BODY_MARKERS)


# SEDI's report is table-soup (hundreds of nested tables). Collect every leaf
# row (a <tr> with no nested table) in DOM order, so the group-header rows and
# transaction rows arrive in the same sequence the report presents them.
_SEDI_ROWS_JS = r"""
() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const tr of document.querySelectorAll('tr')) {
    if (tr.querySelector('table')) continue;   // skip layout rows wrapping nested tables
    const cells = Array.from(tr.children)
      .filter(c => c.tagName === 'TD' || c.tagName === 'TH')
      .map(c => norm(c.innerText));
    if (cells.some(Boolean)) out.push(cells);
  }
  return out;
}
"""


class SediScraper:
    """Headful, persistent-profile session driving SEDI's public ITD wizard."""

    def __init__(
        self,
        headless: bool = False,
        profile_dir: Path | None = None,
        months: int = 24,
        capture_dir: Path | None = None,
        pages_dir: Path | None = None,
        manual_wait_s: int = 240,
        channel: str | None = "chrome",
        start_minimized: bool = True,
    ):
        self._headless = headless
        self._profile_dir = profile_dir
        self._months = months
        self._capture_dir = capture_dir
        self._pages_dir = pages_dir  # snapshot each company's report HTML here
        self._manual_wait_s = manual_wait_s
        # Prefer real Google Chrome over bundled Chromium: its fingerprint is far
        # less bot-like, which is often the difference between passing Radware's
        # check and getting a hard 403. Falls back to Chromium if Chrome isn't
        # installed (see __enter__).
        self._channel = channel
        # The window is only worth looking at when a CAPTCHA is up, and a scrape
        # takes minutes — so it gets out of the way and comes back on its own.
        self._start_minimized = start_minimized
        self._pw = None
        self._ctx = None
        self._page = None
        self._window = None  # cached (CDP session, windowId)

    def _launch(self, channel: str | None):
        return self._pw.chromium.launch_persistent_context(
            str(self._profile_dir) if self._profile_dir else "",
            headless=self._headless,
            channel=channel,
            user_agent=_UA,
            locale="en-CA",
            viewport={"width": 1366, "height": 900},
            # The cache cap keeps this persistent profile from growing without
            # bound (it reached 301 MB before). Component updates are left on:
            # this profile faces a bot wall, so it should look ordinary.
            args=[
                "--disable-blink-features=AutomationControlled",
                # A minimized window is a backgrounded one, and Chrome throttles
                # those hard — background timers drop to about one tick a minute.
                # Radware's challenge and SEDI's own scripts run on timers, so
                # without these the hidden window would crawl or stall outright.
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                *profiles.cache_args(),
            ],
        )

    def __enter__(self) -> SediScraper:
        self._stealth_cm = Stealth().use_sync(sync_playwright())
        self._pw = self._stealth_cm.__enter__()
        # A persistent context keeps the solved-challenge cookie between runs.
        try:
            self._ctx = self._launch(self._channel)
        except Exception as e:
            if self._channel:
                print(
                    f"  SEDI: '{self._channel}' channel unavailable ({e}); using bundled Chromium."
                )
                self._ctx = self._launch(None)
            else:
                raise
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.hide_window()
        return self

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            self._stealth_cm.__exit__(*exc)

    # ------------------------------------------------------------------
    # Window state. Asking Chrome to minimize itself, rather than asking the
    # desktop to minimize Chrome: there is no portable way to move another
    # program's window. X11 needs wmctrl or xdotool installed, Wayland forbids
    # it outright, and macOS and Windows each want their own API. Chrome moving
    # its own window works the same way everywhere.
    def _set_window_state(self, state: str) -> None:
        """Best-effort `minimized` / `normal`. A window that will not move is
        never a reason to fail a scrape, so every failure here is swallowed."""
        if self._headless or not self._ctx or not self._page:
            return
        with contextlib.suppress(Exception):
            if self._window is None:
                cdp = self._ctx.new_cdp_session(self._page)
                self._window = (cdp, cdp.send("Browser.getWindowForTarget")["windowId"])
            cdp, window_id = self._window
            cdp.send(
                "Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": state}}
            )

    def hide_window(self) -> None:
        """Drop the browser out of the way. It comes back if a human is needed."""
        if self._start_minimized:
            self._set_window_state("minimized")

    def show_window(self) -> None:
        """Put the browser back in front — something needs a pair of hands."""
        self._set_window_state("normal")
        if self._page:
            with contextlib.suppress(Exception):
                self._page.bring_to_front()

    def _hidden(self) -> bool:
        """True when the window is deliberately out of sight."""
        return self._start_minimized and not self._headless

    def _click(self, target: Any, timeout: int = 8000) -> None:
        """Click without dragging the window back to the front.

        Chrome restores a minimized window the moment it receives an Input-domain
        event, and a real `click()` is one. The scrape clicks up to twice per
        company, so the window was popping up again and again for a whole batch.

        Dispatching the event on the element never touches that domain. Measured:
        it still follows an `<a href>` and still submits a form, with the window
        staying minimized throughout. `select_option`, `fill`, `press`,
        `screenshot` and `evaluate` were measured too and none of them raise the
        window, so clicks are the only thing that needs this.

        When the window is on show anyway (`--sedi-window`, or headless where
        there is no window at all) a real click costs nothing and is the more
        faithful input, so that path is left alone.
        """
        if self._hidden():
            target.dispatch_event("click", timeout=timeout)
        else:
            target.click(timeout=timeout)

    # ------------------------------------------------------------------
    def _walled(self) -> bool:
        title = self._page.title() or ""
        url = self._page.url or ""
        body = ""
        with contextlib.suppress(Exception):
            body = self._page.inner_text("body")
        return _is_bot_wall(title, url, body)

    def _clear_wall_or_raise(self, ctx: str) -> None:
        """If the bot wall is up: headless -> BotBlocked; headful -> wait for a
        human to solve it in the visible window (poll until it clears)."""
        if not self._walled():
            return
        if self._headless:
            raise BotBlocked(f"SEDI bot wall while {ctx} (headless — cannot solve)")
        # The window has been sitting minimized. This is the one thing it exists
        # for, so un-minimize it — otherwise the scrape waits four minutes for a
        # CAPTCHA nobody can see, then fails.
        self.show_window()
        print(
            f"  SEDI: Radware bot wall while {ctx}. If there's a CAPTCHA, solve it in the "
            "browser window (re-opened for you). A bare '403 Forbidden / Transaction ID' "
            "page is a hard IP/fingerprint block with nothing to solve — reloading "
            "periodically in case it re-validates…"
        )
        waited = 0
        step = 3000
        reload_every = 15000  # nudge Radware to re-run its JS check after cookies settle
        while waited < self._manual_wait_s * 1000:
            self._page.wait_for_timeout(step)
            waited += step
            if not self._walled():
                print("  SEDI: wall cleared, continuing.")
                self.hide_window()  # thanks — back out of the way
                return
            if waited % reload_every < step:
                with contextlib.suppress(Exception):
                    self._page.reload(wait_until="domcontentloaded", timeout=30000)
        raise BotBlocked(f"SEDI bot wall not cleared within {self._manual_wait_s}s while {ctx}")

    def _dump(self, label: str) -> None:
        if not self._capture_dir:
            return
        self._capture_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label)
        (self._capture_dir / f"{safe}.html").write_text(self._page.content(), encoding="utf-8")
        with contextlib.suppress(Exception):
            # Bounded: a minimized window has no on-screen surface to capture, so
            # this can sit on the default 30s timeout for every dumped page.
            self._page.screenshot(
                path=str(self._capture_dir / f"{safe}.png"), full_page=True, timeout=10000
            )

    def _save_page(self, exchange: str, ticker: str) -> None:
        """Snapshot the currently-rendered report HTML so the app can serve it as
        this company's SEDI page. Best-effort — never let it break a scrape."""
        if not self._pages_dir:
            return
        with contextlib.suppress(Exception):
            self._pages_dir.mkdir(parents=True, exist_ok=True)
            path = self._pages_dir / sedi_page_filename(exchange, ticker)
            path.write_text(self._page.content(), encoding="utf-8")

    # ------------------------------------------------------------------
    def fetch(self, exchange: str, ticker: str, issuer_hint: str = "") -> list[InsiderTransaction]:
        """Run the ITD 'issuer name' search for one company and parse results.

        On a parse of zero rows the results HTML is dumped (when capture_dir is
        set) and any SEDI error banner is logged, so an empty result is
        diagnosable rather than silent. Failures never abort the batch.
        """
        issuer = issuer_hint or ticker
        page = self._page

        page.goto(_ITD_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        self._clear_wall_or_raise("opening ITD search")

        try:
            self._fill_issuer_search(issuer)
        except Exception as e:
            print(f"  SEDI [{exchange}:{ticker}] could not drive the search form: {e}")
            self._dump(f"itd_form_error_{ticker}")
            return []

        page.wait_for_timeout(2500)
        self._clear_wall_or_raise("running ITD search")

        # An issuer-name search lands on a "Select issuer" picker; click through
        # to the matching issuer's actual transaction report.
        self._select_issuer_if_needed(issuer)

        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = self._extract_rows()  # settles the report DOM before we snapshot it
        self._save_page(exchange, ticker)  # snapshot the report page for the UI
        records = _parse_report_rows(rows, issuer, exchange, ticker, _ITD_URL, now)
        if not records:
            self._log_page_error(exchange, ticker)
            self._dump(f"itd_results_{ticker}")  # leave HTML so parsing can be fixed
        return records

    # ------------------------------------------------------------------
    def fetch_insider(self, family_name: str) -> list[InsiderTransaction]:
        """Every filing by one person, across every issuer — the company-first
        model turned around.

        This is the search that finds companies you are not already tracking. The
        rest of the app can only surface an insider because a company they traded
        in was scraped first, so a person trading somewhere off your watchlist is
        invisible — there is no signal to act on. SEDI's family-name search is the
        one place that signal exists.

        Records come back with `issuer_name` set but **no exchange/ticker**: SEDI
        reports legal names, not tickers. Run `resolve_tickers` over the result to
        fill those in before writing a snapshot.
        """
        page = self._page
        slug = re.sub(r"[^A-Za-z0-9]+", "_", family_name).strip("_")[:40] or "insider"

        page.goto(_ITD_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        self._clear_wall_or_raise("opening ITD search")

        try:
            self._fill_insider_search(family_name)
        except Exception as e:
            print(f"  SEDI [insider:{family_name}] could not drive the search form: {e}")
            self._dump(f"itd_insider_form_error_{slug}")
            return []

        page.wait_for_timeout(2500)
        self._clear_wall_or_raise("running ITD insider search")

        # A family-name search can match several people (and SEDI lists one row
        # per insider); pick the one whose name we asked for.
        self._select_insider_if_needed(family_name)

        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = self._extract_rows()
        # No issuer/exchange/ticker fallback: every company must come from the
        # grid's own "Issuer name:" headers. Passing one in would stamp it on
        # rows belonging to other companies.
        records = _parse_report_rows(rows, "", "", "", _ITD_URL, now)
        if not records:
            self._log_page_error("insider", family_name)
        # Always dump an insider search: the results grid for this search type is
        # not yet confirmed against a live page, so the HTML is the evidence
        # needed to fix the parser if the shape differs.
        self._dump(f"itd_insider_results_{slug}")
        return records

    def _select_insider_if_needed(self, family_name: str) -> None:
        """A family-name search lands on a 'Select insider' picker when more than
        one person matches. Click the best match (else the first); a no-op when
        SEDI went straight to the report."""
        page = self._page
        views = page.locator("a[href*='SVTItdSelectInsider']")
        n = views.count()
        if n == 0:
            return
        target = views.first
        q = (family_name or "").strip().lower()
        for i in range(n):
            v = views.nth(i)
            with contextlib.suppress(Exception):
                row = v.locator("xpath=ancestor::tr[1]").inner_text()
                if q and q in row.lower():
                    target = v
                    break
        with contextlib.suppress(Exception):
            self._click(target)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            self._clear_wall_or_raise("opening insider report")

    def _extract_rows(self) -> list[list[str]]:
        """Read the report's rows, tolerating a still-settling navigation (the
        large reports can destroy the JS context mid-evaluate)."""
        for _ in range(3):
            with contextlib.suppress(Exception):
                self._page.wait_for_load_state("domcontentloaded", timeout=15000)
            try:
                return self._page.evaluate(_SEDI_ROWS_JS)
            except Exception:
                self._page.wait_for_timeout(1500)
        return []

    def _select_issuer_if_needed(self, issuer: str) -> None:
        """SEDI's issuer search returns a picker page with a 'View' link per
        matching issuer. Click the one matching our name (else the first) to
        open the transaction report. A no-op if there's no picker."""
        page = self._page
        views = page.locator("a[href*='SVTItdSelectIssuer']")
        n = views.count()
        if n == 0:
            return  # already on the report (or no matches)
        target = views.first
        q = _search_query(issuer).lower()
        for i in range(n):
            v = views.nth(i)
            with contextlib.suppress(Exception):
                row = v.locator("xpath=ancestor::tr[1]").inner_text()
                if q and q in row.lower():
                    target = v
                    break
        with contextlib.suppress(Exception):
            self._click(target)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            self._clear_wall_or_raise("opening issuer report")

    def _log_page_error(self, exchange: str, ticker: str) -> None:
        """Surface a SEDI validation banner (e.g. 'Date cannot be future dated')."""
        with contextlib.suppress(Exception):
            body = self._page.inner_text("body")
            m = re.search(r"Error:\s*(.+)", body)
            if m:
                print(f"  SEDI [{exchange}:{ticker}] page error: {m.group(1).strip()[:80]}")

    def _fill_issuer_search(self, issuer: str) -> None:
        """ITD search by issuer name — one company, grouped by insider."""
        self._fill_search(_SELECT_TYPE_ISSUER, _search_query(issuer))

    def _fill_insider_search(self, family_name: str) -> None:
        """ITD search by insider family name — one person, across every issuer
        they have filed against. This is the only cross-issuer person search in
        Canadian public data; MarketBeat has no equivalent."""
        self._fill_search(_SELECT_TYPE_INSIDER, family_name.strip())

    def _fill_search(self, select_type: str, value: str) -> None:
        """Drive SEDI's public ITD form over a wide date window, then submit.

        The form is the same for every search type — only SELECT_TYPE and the
        value box change. Real field names from the live form: SELECT_TYPE_VALUE
        is the shared name box, DATE_RANGE_TYPE=0 is transaction date, months are
        0-indexed, and the day <option>s carry no value attribute (so they're
        chosen by label). The "to" date is pinned to today — SEDI rejects any
        future end date.
        """
        page = self._page
        # Use SEDI's OWN current date as the end of the range. The server runs on
        # Eastern time and rejects any future date; deriving "today" from the
        # local/UTC clock can be a day ahead and trip "Date cannot be future
        # dated". At least 2 years back (more if a larger --months was requested).
        today = self._sedi_today()
        years_back = max(2, (self._months + 11) // 12)

        page.select_option("select[name='SELECT_TYPE']", select_type, timeout=8000)
        page.wait_for_timeout(300)  # onchange enables the value box
        page.fill("input[name='SELECT_TYPE_VALUE']", value, timeout=8000)
        # "Starts with" rather than exact: SEDI stores full legal names, so an
        # exact match fails on the shortened names people actually type.
        with contextlib.suppress(Exception):
            page.select_option("select[name='SELECT_TYPE_VALUE_SEARCH_TYPE']", "3", timeout=4000)

        # Mandatory date range: Jan 1 (>= 2 years back) .. today, by transaction
        # date. The day <option>s carry no value attribute, so they must be
        # chosen by their visible label, not by value/index (index didn't stick,
        # leaving the day empty -> SEDI defaulted to month-end -> "future dated").
        page.select_option("select[name='DATE_RANGE_TYPE']", "0")  # date of transaction
        page.select_option("select[name='MONTH_FROM_PUBLIC']", "0")  # January (0-indexed)
        page.fill("input[name='YEAR_FROM_PUBLIC']", str(today.year - years_back))
        page.select_option("select[name='DAY_FROM_PUBLIC']", label="1")
        page.select_option("select[name='MONTH_TO_PUBLIC']", str(today.month - 1))
        page.fill("input[name='YEAR_TO_PUBLIC']", str(today.year))
        page.select_option("select[name='DAY_TO_PUBLIC']", label=str(today.day))

        self._click(page.locator("input[name='Search']"))

    def _sedi_today(self) -> date:
        """SEDI prints its current (Eastern-time) date on every page, e.g.
        'July 1, 2026'. Use it as the range end so we never request a future
        date; fall back to the local clock if it can't be read."""
        with contextlib.suppress(Exception):
            body = self._page.inner_text("body")
            m = re.search(
                r"(?:January|February|March|April|May|June|July|August|September|"
                r"October|November|December)\s+\d{1,2},\s+\d{4}",
                body,
            )
            if m:
                return datetime.strptime(m.group(0), "%B %d, %Y").date()
        return datetime.now().date()
