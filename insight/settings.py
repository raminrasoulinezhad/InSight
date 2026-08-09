# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

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

# Split by brightness, because "follow my system" needs to know which theme to
# use for each. The two tuples together are the full set, and the UI shelves the
# picker the same way — tests assert both halves match the stylesheet.
DARK_THEMES = ("dark", "midnight", "terminal", "caramel", "chic")
LIGHT_THEMES = ("light", "newsprint", "sage", "lemon", "canadian")
THEMES = DARK_THEMES + LIGHT_THEMES

DEFAULT_THEME = "dark"
DEFAULT_AUTO_DARK = "dark"
DEFAULT_AUTO_LIGHT = "light"

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

    def pick(key: str, allowed: tuple[str, ...], fallback: str) -> str:
        value = raw.get(key)
        return value if value in allowed else fallback

    return {
        "theme": pick("theme", THEMES, DEFAULT_THEME),
        # When auto is on the browser decides between the two below by asking the
        # OS; `theme` is kept untouched so turning auto back off restores the
        # theme the user last picked by hand.
        "auto": bool(raw.get("auto", False)),
        "auto_dark": pick("auto_dark", DARK_THEMES, DEFAULT_AUTO_DARK),
        "auto_light": pick("auto_light", LIGHT_THEMES, DEFAULT_AUTO_LIGHT),
    }


def save_settings(path: Path, incoming: dict[str, Any]) -> tuple[bool, str]:
    """Merge `incoming` over the stored preferences. Returns (saved, message).

    Unknown themes are rejected rather than stored: a value with no matching
    stylesheet block would leave the app looking default with no explanation.
    """
    if not isinstance(incoming, dict):
        return False, "Expected a settings object."

    # Each theme field is validated against the set it is allowed to hold, so
    # "follow my system" can never end up with a light theme filed as the dark
    # one — the app would then flip to a brighter palette when the OS goes dark.
    fields: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("theme", THEMES),
        ("auto_dark", DARK_THEMES),
        ("auto_light", LIGHT_THEMES),
    )
    for key, allowed in fields:
        value = incoming.get(key)
        if value is not None and value not in allowed:
            return False, f"Unknown {key} {value!r}. Choose one of: {', '.join(allowed)}."

    auto = incoming.get("auto")
    if auto is not None and not isinstance(auto, bool):
        return False, "'auto' must be true or false."

    with _WRITE_LOCK:
        current = load_settings(path)
        for key, _allowed in fields:
            if incoming.get(key) is not None:
                current[key] = incoming[key]
        if auto is not None:
            current["auto"] = auto
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
