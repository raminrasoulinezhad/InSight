# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Per-company user notes — your own research kept next to the data.

A note is free text the user writes about one company ("why I'm watching this",
a thesis, a reminder to check the next filing). The UI writes it as a bullet
list, but nothing here parses or validates that: notes are stored verbatim so
the storage format never constrains how the UI chooses to render them.

State is a single JSON object keyed "EXCH:TICKER" (see paths.notes_file()),
living beside the watchlist rather than in the data snapshots — it is
user-authored and must survive any amount of re-scraping. Writes are atomic
(temp file + replace) so an interrupted save can't truncate existing notes, and
saving an empty note deletes the key rather than leaving a blank entry behind.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

# Generous, but bounded: notes are a scratchpad, not a document store. A runaway
# paste would otherwise be re-sent with every view load.
MAX_NOTE_CHARS = 20_000

# Saving a note is read-modify-write, and the app serves requests on a thread
# pool, so two notes saved at once could otherwise each write a file built from
# the state before the other's change — silently dropping one of them.
_WRITE_LOCK = threading.Lock()


def note_key(exchange: str, ticker: str) -> str:
    """Identity of a company note — matches aggregate's company keying."""
    return f"{(exchange or '').strip().upper()}:{(ticker or '').strip().upper()}"


def load_notes(path: Path) -> dict[str, str]:
    """All notes as {"EXCH:TICKER": text}; empty when unset or unreadable.

    A corrupt file yields {} rather than raising — a broken notes file must
    never stop the app from serving data.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k).upper(): str(v) for k, v in raw.items() if isinstance(v, str) and str(v).strip()}


def _write_notes(path: Path, notes: dict[str, str]) -> None:
    """Atomically replace the notes file (unique temp in the same dir, then rename).

    The temp name is unique per write: a shared fixed name would let two
    concurrent writers interleave into the same file before either renamed it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(notes, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)  # never leave a stray temp behind
        raise


def save_note(path: Path, exchange: str, ticker: str, text: str) -> tuple[bool, str]:
    """Set (or clear) one company's note. Returns (saved, message).

    Blank text removes the entry, so clearing a note in the UI leaves no
    residue. Text longer than MAX_NOTE_CHARS is rejected rather than silently
    truncated — losing the tail of someone's own writing is worse than an error.
    """
    key = note_key(exchange, ticker)
    if key == ":":
        return False, "A note needs a company (exchange and ticker)."
    if len(text) > MAX_NOTE_CHARS:
        return False, f"Note is too long ({len(text)} chars; limit {MAX_NOTE_CHARS})."

    body = text.strip()
    with _WRITE_LOCK:  # read-modify-write must be atomic against other requests
        notes = load_notes(path)
        if body:
            notes[key] = body
        else:
            notes.pop(key, None)
        _write_notes(path, notes)
    return True, "Saved." if body else "Note cleared."
