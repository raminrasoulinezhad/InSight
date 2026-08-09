# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Where InSight keeps its user data — resolved per-OS so the app works when
installed globally (via `uv tool install`) with the source repo deleted.

Everything lives under one app folder so it is easy to find/back up/delete:

    Windows   %LOCALAPPDATA%\\InSight
    macOS     ~/Library/Application Support/InSight
    Linux     ${XDG_DATA_HOME:-~/.local/share}/InSight

Holds the editable watchlist (companies.json, seeded from the packaged default
on first run), the dated scrape output (data/), and the dedicated Chrome
profile for --window mode.
"""

from __future__ import annotations

import os
import re
import sys
from importlib import resources
from pathlib import Path

_APP = "InSight"


def app_dir() -> Path:
    """The per-user application folder for this OS (created on demand)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        root = Path(base) / _APP
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / _APP
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        root = Path(base) / _APP
    root.mkdir(parents=True, exist_ok=True)
    return root


def data_dir() -> Path:
    """Where dated scrape output (insider_YYYY-MM-DD.json/.csv) is written."""
    d = app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    """The editable watchlist. Seeded from the packaged default on first run."""
    cfg = app_dir() / "companies.json"
    if not cfg.exists():
        default = resources.files("insight").joinpath("companies.default.json")
        cfg.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")
    return cfg


def delisted_file() -> Path:
    """Tickers found to be delisted/acquired during a scrape (no insider page).

    A JSON list of "EXCH:TICKER". The scraper maintains it (adds on detection,
    removes if a company's data returns) and the app filters these out of both
    views so acquired/delisted names stop showing stale, meaningless activity.
    """
    return app_dir() / "delisted.json"


def settings_file() -> Path:
    """App preferences (currently the chosen theme) — a small JSON object.

    Kept apart from notify.json so a display toggle never shares a file with
    SMTP credentials.
    """
    return app_dir() / "settings.json"


def notes_file() -> Path:
    """Free-text notes the user keeps per company (your own research, thesis,
    reminders) — a JSON object keyed "EXCH:TICKER" -> note text.

    Purely user-authored, never touched by the scraper, so it lives beside the
    watchlist in the per-user app folder rather than in the data snapshots.
    """
    return app_dir() / "notes.json"


def notify_file() -> Path:
    """Notification settings + alarms (email/ntfy config, watched companies/people).

    Holds an SMTP app password in plaintext, so it stays in the per-user app
    folder — never the repo. A JSON object: {email, ntfy, alarms}.
    """
    return app_dir() / "notify.json"


def notify_log_file() -> Path:
    """Append-only log of every notification InSight generates (JSONL, one record
    per line).

    Each record carries the notification's index, timestamp, target label and the
    per-channel delivery result, so a delivered alert can be traced back later when
    debugging or reporting an issue. Lives beside notify.json in the per-user app
    folder; it only ever grows (append-only) and is safe to delete.
    """
    return app_dir() / "notifications.log"


def cache_dir() -> Path:
    """Per-company scrape cache (one JSON per issuer) used to avoid re-fetching
    data that is still fresh."""
    c = app_dir() / "cache"
    c.mkdir(parents=True, exist_ok=True)
    return c


def chrome_profile_dir() -> Path:
    """Dedicated Chrome profile for the --window app so it never disturbs the
    user's main browser."""
    p = app_dir() / "chrome-profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sedi_pages_dir() -> Path:
    """Saved SEDI report HTML, one file per company (EXCH_TICKER.html).

    SEDI's ITD report has no stable per-company URL (it's a form/POST wizard
    behind a bot wall), so the scraper snapshots the rendered report page here as
    it scrapes. The app serves these locally (via /api/sedi-page) so the user can
    open the official SEDI report for a company without re-driving the wizard.
    """
    p = app_dir() / "sedi-pages"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sedi_page_filename(exchange: str, ticker: str) -> str:
    """Filename for a company's saved SEDI report snapshot: 'EXCH_TICKER.html',
    sanitized to a single safe path segment (no separators / traversal). Shared
    by the scraper (writing) and the app (reading) so they never diverge."""
    key = f"{(exchange or '').upper()}_{(ticker or '').upper()}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".html"


def sedi_profile_dir() -> Path:
    """Dedicated, persistent browser profile for the SEDI scraper.

    SEDI (sedi.ca) sits behind a ShieldSquare bot wall. Running headful with a
    persistent profile lets a solved challenge / session cookie survive between
    runs, so the CAPTCHA only has to be cleared occasionally rather than every
    scrape. Kept separate from the --window app profile to avoid collisions.
    """
    p = app_dir() / "sedi-profile"
    p.mkdir(parents=True, exist_ok=True)
    return p
