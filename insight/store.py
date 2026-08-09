# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""One consolidated, deduplicated record store folded from the dated snapshots.

Each scrape writes a full `insider_YYYY-MM-DD.json` snapshot, and most of every
snapshot repeats what earlier ones already said — the history is re-stated, not
appended to. Left alone that means the app re-reads and re-merges the entire
pile on every cold start (and again after each scrape, since a new file
invalidates the in-memory cache), paying seconds of JSON parsing to recover a
record set that is comparatively tiny.

So snapshots are folded once, in date order, into a single `store.json`:

    {"version": 1,
     "folded": {"insider_2026-06-30.json": [mtime_ns, size], ...},
     "records": [ ...deduplicated InsiderTransaction records... ]}

`sync()` reads that store and folds in only the snapshots whose name+mtime+size
are not already in the manifest, so a fresh scrape costs one small file parse
instead of a full re-merge. Snapshots stay on disk untouched — the store is a
derived cache and can be deleted at any time to rebuild from scratch.

The manifest also *is* the history: it remembers every snapshot ever folded, so
counts and the newest data date survive `prune_folded()` deleting the originals.

Dedup keying is supplied by the caller (aggregate._txn_key) so this module never
has to know the record schema, and folding runs oldest → newest so a later
scrape wins any collision — matching the behaviour of the plain merge it
replaces.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable
from glob import glob
from pathlib import Path
from typing import Any

Rec = dict[str, Any]
KeyFn = Callable[[Rec], tuple[Any, ...]]
FileSig = tuple[int, int]  # (mtime_ns, size)

STORE_NAME = "store.json"
STORE_VERSION = 1

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# The app serves on a thread pool, so concurrent requests can each miss the
# record cache and try to fold at the same time. Folding is idempotent, so the
# lock is about not doing the same expensive parse twice (and not having two
# writers race), rather than correctness of the result.
_SYNC_LOCK = threading.Lock()


def store_path(data_dir: Path) -> Path:
    return data_dir / STORE_NAME


def snapshot_date(name: str) -> str:
    """The YYYY-MM-DD embedded in a snapshot filename ('' if none)."""
    m = _DATE_RE.search(name)
    return m.group(1) if m else ""


def snapshot_paths(data_dir: Path) -> list[Path]:
    """Every dated snapshot, oldest first — ordered by the date *inside* the name.

    Snapshots carry a source tag (`insider_YYYY-MM-DD.json` for MarketBeat,
    `insider_sedi_YYYY-MM-DD.json` for SEDI), so a plain filename sort puts every
    SEDI file after every MarketBeat one regardless of when each was taken. That
    would both fold snapshots out of chronological order and make "keep the
    newest N" keep the wrong N.
    """
    paths = [Path(p) for p in glob(str(data_dir / "insider_*.json"))]
    return sorted(paths, key=lambda p: (snapshot_date(p.name), p.name))


def _sig(path: Path) -> FileSig | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _read_store(path: Path) -> tuple[list[Rec], dict[str, FileSig]]:
    """Existing (records, manifest); empty on a missing, corrupt or stale-version
    store — a bad store must only cost a rebuild, never an error."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return [], {}
    if not isinstance(raw, dict) or raw.get("version") != STORE_VERSION:
        return [], {}
    records = raw.get("records")
    folded = raw.get("folded")
    if not isinstance(records, list) or not isinstance(folded, dict):
        return [], {}
    manifest: dict[str, FileSig] = {}
    for name, sig in folded.items():
        if isinstance(sig, list) and len(sig) == 2:
            manifest[str(name)] = (int(sig[0]), int(sig[1]))
    return records, manifest


def _write_store(path: Path, records: list[Rec], manifest: dict[str, FileSig]) -> None:
    """Atomically replace the store (unique temp in the same dir, then rename).

    An interrupted write leaves the previous store intact rather than a stub, and
    the per-write temp name keeps two concurrent writers from clobbering each
    other's half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STORE_VERSION,
        "folded": {name: list(sig) for name, sig in sorted(manifest.items())},
        "records": records,
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _fold(records: Iterable[Rec], incoming: Iterable[Rec], key_fn: KeyFn) -> list[Rec]:
    """Merge `incoming` over `records`, later occurrences winning on a key clash."""
    merged: dict[tuple[Any, ...], Rec] = {key_fn(r): r for r in records}
    for r in incoming:
        merged[key_fn(r)] = r
    return list(merged.values())


def sync(data_dir: Path, key_fn: KeyFn) -> tuple[list[Rec], dict[str, FileSig]]:
    """Return (all deduplicated records, manifest), folding in any new snapshots.

    Only snapshots whose name+mtime+size differ from the manifest are parsed, so
    the steady-state cost is one small file rather than the whole history. The
    store is rewritten only when something actually changed.
    """
    with _SYNC_LOCK:
        return _sync_locked(data_dir, key_fn)


def _sync_locked(data_dir: Path, key_fn: KeyFn) -> tuple[list[Rec], dict[str, FileSig]]:
    path = store_path(data_dir)
    records, manifest = _read_store(path)

    pending: list[tuple[Path, FileSig]] = []
    for snap in snapshot_paths(data_dir):
        sig = _sig(snap)
        if sig is not None and manifest.get(snap.name) != sig:
            pending.append((snap, sig))

    if not pending:
        return records, manifest

    for snap, sig in pending:  # oldest → newest, so a later scrape wins
        try:
            incoming = json.loads(snap.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue  # skip a corrupt/unreadable snapshot rather than fail
        if not isinstance(incoming, list):
            continue
        records = _fold(records, incoming, key_fn)
        manifest[snap.name] = sig

    _write_store(path, records, manifest)
    return records, manifest


def folded_names(data_dir: Path) -> list[str]:
    """Every snapshot ever folded into the store, oldest first.

    Survives the originals being pruned, so it — not a directory listing — is
    what the app should count when reporting how deep its history goes.
    """
    _records, manifest = _read_store(store_path(data_dir))
    return sorted(manifest)


def prune_folded(data_dir: Path, key_fn: KeyFn, keep: int = 2) -> tuple[list[str], int]:
    """Delete snapshots already folded into the store. Returns (names, bytes freed).

    The store is synced and re-read from disk first, so nothing is deleted until
    its contents are provably durable. The newest `keep` snapshots are always
    left in place as a hand-inspectable tail, and the manifest still counts the
    deleted ones so the app's history figures don't change.

    A negative `keep` is clamped to 0 rather than wrapping into a slice that
    would quietly delete everything — "keep -1" is a typo, not an instruction.
    """
    keep = max(0, keep)
    sync(data_dir, key_fn)
    records, manifest = _read_store(store_path(data_dir))
    if not records:
        return [], 0  # nothing durable to fall back on — never prune

    snaps = snapshot_paths(data_dir)
    candidates = snaps[:-keep] if keep > 0 else snaps

    removed: list[str] = []
    freed = 0
    for snap in candidates:
        if manifest.get(snap.name) != _sig(snap):
            continue  # not (or no longer) folded — leave it for the next sync
        size = snap.stat().st_size
        try:
            snap.unlink()
        except OSError:
            continue
        removed.append(snap.name)
        freed += size
    return removed, freed
