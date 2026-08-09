# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Open InSight automatically when the user logs in.

Each desktop has its own convention and none of them is a registry of ours to
invent, so this writes the file the platform already looks for:

    Linux    ~/.config/autostart/insight.desktop      (XDG Desktop Entry)
    macOS    ~/Library/LaunchAgents/<label>.plist     (launchd, RunAtLoad)
    Windows  %APPDATA%\\...\\Startup\\InSight.cmd        (Startup folder)

All three are per-user files in the user's own home — no admin rights, no
system-wide daemon, and removing the file is a complete uninstall. That is
deliberate: something that starts itself at login should be trivially
switchable off, including by a user who no longer has the app to switch it off
with.

The command launched is the installed `insight --window` console script, found
on PATH. Resolving it at install time and writing the absolute path keeps the
entry working when the login shell has a different PATH than the terminal the
user enabled it from — a very common way for autostart entries to silently do
nothing.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# launchd requires a reverse-DNS label, and the plist filename must match it.
MAC_LABEL = "com.raminrasoulinezhad.insight"


class UnsupportedPlatform(RuntimeError):
    """Raised when the OS has no autostart convention we know how to write."""


def _argv() -> list[str]:
    """The command to run at login, as separate arguments.

    A list rather than a joined string on purpose: a home directory with a space
    is ordinary on macOS and Windows, and every one of these formats treats a
    bare space as an argument separator. Joining first and splitting later turned
    "/Users/Jo Smith/.local/bin/insight" into two broken arguments and the entry
    silently never launched.

    Falls back to `python -m insight.app` when the console script isn't on PATH
    (a source checkout, say), so enabling still produces something that runs.
    """
    found = shutil.which("insight")
    if found:
        return [str(Path(found).resolve()), "--window"]
    return [str(Path(sys.executable).resolve()), "-m", "insight.app", "--window"]


def _desktop_exec(argv: list[str]) -> str:
    """Quote argv for a Desktop Entry `Exec=` key.

    The spec reserves space as a separator and gives backslash and double quote
    a meaning inside a quoted argument, so both need escaping before quoting.
    """
    out = []
    for arg in argv:
        if any(c in arg for c in " \t\"'\\`$"):
            escaped = arg.replace("\\", "\\\\").replace('"', '\\"')
            out.append(f'"{escaped}"')
        else:
            out.append(arg)
    return " ".join(out)


def _cmd_line(argv: list[str]) -> str:
    """Quote argv for a Windows .cmd. `start` takes a title first, hence the ""."""
    quoted = " ".join(f'"{a}"' if " " in a else a for a in argv)
    return f'start "" {quoted}'


def entry_path() -> Path:
    """Where this platform expects the autostart entry to live."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise UnsupportedPlatform("APPDATA is not set, so the Startup folder is unknown.")
        return (
            Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        ) / "InSight.cmd"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
    if sys.platform.startswith(("linux", "freebsd")):
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        return Path(base) / "autostart" / "insight.desktop"
    raise UnsupportedPlatform(f"No autostart convention known for {sys.platform!r}.")


def _contents(argv: list[str]) -> str:
    if sys.platform == "win32":
        # `start` returns immediately so no console window lingers for the session.
        return f"@echo off\r\n{_cmd_line(argv)}\r\n"
    if sys.platform == "darwin":
        # Built with plistlib rather than string-formatted: it escapes &, < and >
        # in a path for us, which hand-written XML would silently get wrong.
        return plistlib.dumps(
            {
                "Label": MAC_LABEL,
                "ProgramArguments": argv,
                "RunAtLoad": True,
                # Not KeepAlive: this is an app the user may close, not a daemon
                # to be resurrected every time they quit it.
            }
        ).decode("utf-8")
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=InSight\n"
        "Comment=Insider transactions for your watchlist\n"
        f"Exec={_desktop_exec(argv)}\n"
        "Icon=InSight\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def is_enabled() -> bool:
    """True if an autostart entry is currently installed."""
    try:
        return entry_path().exists()
    except UnsupportedPlatform:
        return False


def enable() -> tuple[bool, str]:
    """Install the autostart entry. Returns (enabled, message)."""
    try:
        path = entry_path()
    except UnsupportedPlatform as e:
        return False, str(e)

    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" disables translation: the .cmd content already carries CRLF, and
    # on Windows the default would rewrite each \n again and yield \r\r\n.
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(_contents(_argv()))
    if sys.platform != "win32":
        path.chmod(0o644 if sys.platform == "darwin" else 0o755)
    if sys.platform == "darwin":
        # launchd only notices a new agent on login otherwise; loading it now
        # means "enabled" is true immediately rather than next time.
        subprocess.run(
            ["launchctl", "load", "-w", str(path)],
            capture_output=True,
            check=False,
        )
    return True, f"InSight will open when you log in ({path})."


def disable() -> tuple[bool, str]:
    """Remove the autostart entry. Returns (disabled, message)."""
    try:
        path = entry_path()
    except UnsupportedPlatform as e:
        return False, str(e)

    if not path.exists():
        return True, "InSight was not set to open at login."
    if sys.platform == "darwin":
        subprocess.run(
            ["launchctl", "unload", "-w", str(path)],
            capture_output=True,
            check=False,
        )
    try:
        path.unlink()
    except OSError as e:
        return False, f"Could not remove {path}: {e}"
    return True, "InSight will no longer open when you log in."


def status() -> dict[str, object]:
    """What the UI needs to render the toggle."""
    try:
        path: Path | None = entry_path()
        supported = True
        reason = ""
    except UnsupportedPlatform as e:
        path, supported, reason = None, False, str(e)
    return {
        "supported": supported,
        "enabled": bool(path and path.exists()),
        "path": str(path) if path else "",
        "command": shlex.join(_argv()),
        "reason": reason,
    }
