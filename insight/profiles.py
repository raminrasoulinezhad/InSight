# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Keeping the two Chromium profiles from quietly eating the disk.

InSight drives Chromium twice: a persistent profile for the SEDI scraper (so a
solved bot-wall challenge survives between runs) and another for the `--window`
app. Both are ordinary browser profiles, so both accumulate browser-sized
caches — on one real installation 362 MB and 164 MB, dwarfing the 49 MB of
actual insider data. Nobody would guess that is where their disk went.

Two halves to the fix:

* **Prevention** — both browsers are now launched with a capped disk cache, and
  the app window additionally skips Chromium's component downloads (ML model
  stores, TTS engines, Safe Browsing lists) that it has no use for. See
  `CHROMIUM_CACHE_ARGS` / `app.py` / `sedi.py`.
* **Reclamation** — `prune_profile` deletes what has already piled up.

Only entries on `DISPOSABLE` are ever removed, and every one of them is a cache
Chromium regenerates on demand. Session state — `Cookies`, `Local Storage`,
`Preferences`, `Local State` — is never touched, because for the SEDI profile
that *is* the solved CAPTCHA: deleting it would mean solving the challenge by
hand again.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Cap the on-disk HTTP cache. Chromium treats this as a soft limit, but it is
# the difference between a 301 MB cache and a bounded one.
DISK_CACHE_BYTES = 50 * 1024 * 1024

# Sentinel returned instead of a file list when a profile is open in a browser.
IN_USE = "<in use>"


def cache_args(*, component_updates: bool = True) -> list[str]:
    """Chromium flags that stop a profile growing without bound.

    `component_updates=False` also suppresses the optimization-guide models,
    TTS engine and Safe Browsing lists Chromium downloads in the background —
    ~110 MB on a real profile, and pointless for a window showing a local page.
    Left enabled for the scraper, whose traffic should look like a normal
    browser's to a bot wall.
    """
    args = [f"--disk-cache-size={DISK_CACHE_BYTES}"]
    if not component_updates:
        args.append("--disable-component-update")
    return args


# Paths, relative to a profile root, that Chromium rebuilds on demand. Anything
# not listed here is left alone — the allowlist is deliberate, so a future
# Chromium directory holding real state is never deleted by accident.
DISPOSABLE = (
    # per-profile
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/DawnWebGPUCache",
    "Default/DawnGraphiteCache",
    "Default/Service Worker/CacheStorage",
    "Default/Service Worker/ScriptCache",
    "Default/Sessions",
    # profile root
    "GPUPersistentCache",
    "GrShaderCache",
    "ShaderCache",
    "GraphiteDawnCache",
    "component_crx_cache",
    "extensions_crx_cache",
    "optimization_guide_model_store",
    "OnDeviceHeadSuggestModel",
    "WasmTtsEngine",
    "ActorSafetyLists",
    "Safe Browsing",
)

# Metrics spool files (BrowserMetrics-*.pma), a few MB apiece.
DISPOSABLE_GLOBS = ("BrowserMetrics*.pma",)

# Never removed, and asserted in the tests — losing any of these costs the user
# a re-solved CAPTCHA or their window preferences.
PROTECTED = (
    "Default/Cookies",
    "Default/Network/Cookies",
    "Default/Local Storage",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Local State",
)


def in_use(profile_dir: Path) -> bool:
    """True if a browser currently has this profile open.

    Chromium marks a live profile with a `SingletonLock` symlink pointing at
    "<hostname>-<pid>". Deleting cache from under a running browser is asking
    for a confusing crash, so callers skip such profiles rather than trusting
    the user to have closed the app first.

    Errs toward "in use": a lock we cannot interpret is treated as live, since
    skipping a cleanup costs disk while a wrong guess costs a broken session.
    A lock left behind by a crash names a dead pid and is correctly ignored.
    """
    try:
        target = os.readlink(profile_dir / "SingletonLock")
    except OSError:
        return False  # no lock, not a symlink, or no profile — nothing running

    _, _, pid_text = target.rpartition("-")
    try:
        pid = int(pid_text)
    except ValueError:
        return True  # unrecognized format — assume the worst
    try:
        os.kill(pid, 0)  # signal 0 only tests for existence
    except ProcessLookupError:
        return False  # stale lock from a crashed browser
    except OSError:
        return True  # alive but not ours, or an OS without signal 0
    return True


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def prune_profile(profile_dir: Path, *, skip_if_running: bool = True) -> tuple[list[str], int]:
    """Delete regenerable caches from one Chromium profile.

    Returns (what was removed, bytes freed). A profile a browser currently has
    open is skipped and reports (["<in use>"], 0) so callers can say why nothing
    happened. Missing profiles and unreadable entries are skipped rather than
    raising — reclaiming disk is housekeeping and must never be the thing that
    breaks a scrape.
    """
    removed: list[str] = []
    freed = 0
    if not profile_dir.is_dir():
        return removed, freed
    if skip_if_running and in_use(profile_dir):
        return [IN_USE], 0

    root = profile_dir.resolve()
    targets = [profile_dir / rel for rel in DISPOSABLE]
    for pattern in DISPOSABLE_GLOBS:
        targets.extend(profile_dir.glob(pattern))

    for target in targets:
        try:
            # Never follow a symlink out of the profile.
            if not target.exists() or root not in target.resolve().parents:
                continue
            if target.is_dir():
                size = _dir_size(target)
                shutil.rmtree(target, ignore_errors=True)
            else:
                size = target.stat().st_size
                target.unlink()
        except OSError:
            continue
        if not target.exists():
            removed.append(str(target.relative_to(profile_dir)))
            freed += size
    return removed, freed
