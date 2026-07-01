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
