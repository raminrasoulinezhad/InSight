# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""App preferences — currently just the chosen theme.

Kept server-side rather than only in the browser so the choice survives a
cleared cache and follows the user between a browser tab and the `--window`
app, which run under different profiles. The UI still mirrors it into
localStorage, but only as a cache that lets the right theme paint before the
first request completes; this file is the source of truth.

Deliberately separate from notify.json: that holds notification credentials and
alarm state, this holds harmless display preferences, and mixing the two would
put a UI toggle in the same file as an SMTP password.

THEMES must stay in step with the [data-theme="…"] blocks in webui/index.html —
an id here with no stylesheet block yields an app painted with default colours.
The UI test suite asserts the two lists match.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

THEMES = ("dark", "light", "terminal", "newsprint", "midnight", "canadian")
DEFAULT_THEME = "dark"

# Same reasoning as notes.py: read-modify-write from a threaded server.
_WRITE_LOCK = threading.Lock()


def load_settings(path: Path) -> dict[str, Any]:
    """Current preferences, falling back to defaults for anything unset.

    Never raises: an unreadable or nonsensical file means the user gets default
    styling, which is a far better failure than an app that won't load.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    theme = raw.get("theme")
    return {"theme": theme if theme in THEMES else DEFAULT_THEME}


def save_settings(path: Path, incoming: dict[str, Any]) -> tuple[bool, str]:
    """Merge `incoming` over the stored preferences. Returns (saved, message).

    Unknown themes are rejected rather than stored: a value with no matching
    stylesheet block would leave the app looking default with no explanation.
    """
    if not isinstance(incoming, dict):
        return False, "Expected a settings object."

    theme = incoming.get("theme")
    if theme is not None and theme not in THEMES:
        return False, f"Unknown theme {theme!r}. Choose one of: {', '.join(THEMES)}."

    with _WRITE_LOCK:
        current = load_settings(path)
        if theme is not None:
            current["theme"] = theme
        _write(path, current)
    return True, "Saved."


def _write(path: Path, settings: dict[str, Any]) -> None:
    """Atomically replace the settings file (unique temp, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
