#!/usr/bin/env python3
# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""InSight desktop/web app — view insider transactions in a window.

Runs a tiny local web server and serves a single-page UI: a scrollable feed
grouped by watchlist company, each showing its individual insider transactions
(newest first) as a table plus share-activity charts — who traded, when, on
which side, and for how much.

Usage (installed via `uv tool install`, or `uv run insight` from a checkout):
    insight                 # serve on http://127.0.0.1:8765 + open browser
    insight --window        # open as a standalone desktop window (chromeless)
    insight --port 9000
    insight --no-browser    # don't auto-open a browser

Data comes from the newest data/insider_YYYY-MM-DD.json in the per-user app
folder (see insight.paths), produced by `insight-scrape`. Run the scraper to
refresh; reload the page to see updates.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from . import notes, notify, paths, profiles, settings
from .aggregate import load_insiders_view, load_view
from .issuers import add_to_watchlist, remove_from_watchlist, search_issuers

# The UI lives inside the package (importlib.resources) so it is available when
# installed globally with the source repo deleted.
_WEBUI = resources.files("insight").joinpath("webui")

# Editable watchlist + scrape output live in the per-user app folder.
DATA_DIR = paths.data_dir()
CONFIG = paths.config_file()

# ---- background refresh (re-scrape) job state ----
_refresh_lock = threading.Lock()
_refresh = {"running": False, "message": "", "finished": False, "ok": False, "date": None}


def _sedi_page_keys() -> list[str]:
    """EXCH:TICKER for every company with a saved SEDI report snapshot, so the UI
    can show its 'SEDI report' button only where a cached page actually exists."""
    keys = []
    for f in paths.sedi_pages_dir().glob("*.html"):
        exch, sep, ticker = f.stem.partition("_")
        if sep and exch and ticker:
            keys.append(f"{exch.upper()}:{ticker.upper()}")
    return keys


def _read_sedi_page(exchange: str, ticker: str) -> bytes | None:
    """The saved SEDI report HTML for a company, with a <base> injected so its
    relative CSS/image URLs resolve back to sedi.ca. None if never snapshotted.
    The filename is derived (sanitized) from the ticker, so the query can't be
    used to read arbitrary files."""
    path = paths.sedi_pages_dir() / paths.sedi_page_filename(exchange, ticker)
    if not path.exists():
        return None
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    base = '<base href="https://www.sedi.ca/sedi/">'
    i = html.lower().find("<head")
    j = html.find(">", i) if i != -1 else -1
    html = (html[: j + 1] + base + html[j + 1 :]) if j != -1 else base + html
    return html.encode("utf-8")


def _finish_refresh(results, targets, run_date: str) -> None:
    """Shared refresh tail: fire alarms, then mark the job done with a summary."""
    # Fire alarms for newly-seen transactions (never let this break refresh).
    try:
        from .notify import evaluate_and_notify

        evaluate_and_notify(paths.notify_file(), DATA_DIR)
    except Exception:
        traceback.print_exc()
    covered = sum(1 for v in results.values() if v)
    total = sum(len(v) for v in results.values())
    with _refresh_lock:
        _refresh.update(
            running=False,
            finished=True,
            ok=True,
            date=run_date,
            message=f"Done — {total} transactions across {covered}/{len(targets)} companies.",
        )


def _do_refresh(discover: bool = False, source: str = "marketbeat"):
    """Re-scrape and write a new dated snapshot.

    Runs in a background thread so the HTTP request returns immediately; the
    UI polls /api/refresh/status. Imports are local so a missing scraper dep
    (e.g. Playwright not installed) surfaces as a job error, not an app crash.

    `source="marketbeat"` (default): fast, headless. When `discover` is set,
    MarketBeat's ~215-company TSE universe is merged into the watchlist so
    GUI-only users get the same broad coverage as `insight-scrape --discover`.

    `source="sedi"`: Canada's official SEDI, which covers small TSX-V names
    MarketBeat misses. SEDI is bot-walled, so this opens a VISIBLE browser and
    may need the user to solve a CAPTCHA once; the watchlist (never discover) is
    scraped and written to a source-tagged snapshot that merges into the view.
    """
    try:
        from .marketbeat import discover_tickers, scrape_many
        from .scrape import write_outputs

        targets = [
            c
            for c in json.loads(CONFIG.read_text())["companies"]
            if not str(c.get("name", "")).startswith("_")
        ]

        if source == "sedi":
            from .sedi import SediScraper, is_canadian

            targets = [t for t in targets if is_canadian(t)]  # SEDI is Canada-only
            with _refresh_lock:
                _refresh["message"] = (
                    f"Opening a browser for SEDI — solve the CAPTCHA in that window "
                    f"if it appears. Fetching {len(targets)} Canadian companies…"
                )
            results = scrape_many(
                targets,
                cache_dir=paths.cache_dir(),
                force=True,
                scraper_factory=lambda: SediScraper(
                    headless=False,
                    profile_dir=paths.sedi_profile_dir(),
                    # dump HTML for any company that yields no rows, so an empty
                    # result can be diagnosed rather than being silent.
                    capture_dir=paths.app_dir() / "sedi-debug",
                ),
                source="sedi",
            )
            run_date = date.today().isoformat()
            write_outputs(results, DATA_DIR, run_date, source="sedi")
            _finish_refresh(results, targets, run_date)
            return

        if discover:
            with _refresh_lock:
                _refresh["message"] = "Discovering ticker universe…"
            seen = {
                f"{t.get('exchange', '').upper()}:{t.get('ticker', '').upper()}" for t in targets
            }
            for d in discover_tickers(["TSE"]):
                key = f"{d['exchange']}:{d['ticker']}"
                if key not in seen:
                    targets.append(d)
                    seen.add(key)
        with _refresh_lock:
            _refresh["message"] = f"Scraping {len(targets)} companies…"
        # A watchlist-only refresh is an explicit "get fresh data" action, so it
        # bypasses the cache (force=True). A discover run scrapes ~215 companies,
        # so it relies on the cache (force=False) to stay tractable on repeats.
        results = scrape_many(
            targets,
            headless=True,
            cache_dir=paths.cache_dir(),
            force=not discover,
            delisted_path=paths.delisted_file(),
        )
        run_date = date.today().isoformat()
        write_outputs(results, DATA_DIR, run_date)
        _finish_refresh(results, targets, run_date)
    except Exception as e:
        with _refresh_lock:
            _refresh.update(
                running=False,
                finished=True,
                ok=False,
                message=f"Refresh failed: {type(e).__name__}: {e}",
            )
        traceback.print_exc()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter console
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            try:
                html = _WEBUI.joinpath("index.html").read_bytes()
            except (FileNotFoundError, OSError):
                self._send(500, b"UI file webui/index.html not found", "text/plain; charset=utf-8")
                return
            self._send(200, html, "text/html; charset=utf-8")
        elif path in ("/icon.png", "/favicon.ico", "/favicon.png"):
            try:
                png = _WEBUI.joinpath("icon.png").read_bytes()
            except (FileNotFoundError, OSError):
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, png, "image/png")
        elif path in ("/api/data", "/api/insiders", "/api/people"):
            qs = urllib.parse.parse_qs(parsed.query)
            m = qs.get("months", [""])[0]
            months = int(m) if m.isdigit() and int(m) > 0 else None
            d = qs.get("days", [""])[0]
            days = int(d) if d.isdigit() and int(d) > 0 else None
            # /api/people is the pre-rename name, kept working so a bookmark
            # or a script written against it does not break.
            insiders = path in ("/api/insiders", "/api/people")
            view = (load_insiders_view if insiders else load_view)(
                DATA_DIR, CONFIG, months=months, days=days
            )
            if path == "/api/data":
                view["sedi_pages"] = _sedi_page_keys()
            self._send_json(200, view)
        elif path == "/api/sedi-page":
            qs = urllib.parse.parse_qs(parsed.query)
            body = _read_sedi_page(qs.get("exchange", [""])[0], qs.get("ticker", [""])[0])
            if body is None:
                self._send(
                    404,
                    "<h2>No saved SEDI page for this company yet</h2>"
                    "<p>Use “⛏ Fetch from SEDI” to snapshot it, then try again.</p>".encode(),
                    "text/html; charset=utf-8",
                )
            else:
                self._send(200, body, "text/html; charset=utf-8")
        elif path == "/api/search":
            q = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            try:
                self._send_json(200, {"query": q, "candidates": search_issuers(q)})
            except Exception as e:
                self._send_json(502, {"error": f"resolver failed: {e}"})
        elif path == "/api/refresh/status":
            with _refresh_lock:
                self._send_json(200, dict(_refresh))
        elif path == "/api/notes":
            self._send_json(200, {"notes": notes.load_notes(paths.notes_file())})
        elif path == "/api/settings":
            self._send_json(200, settings.load_settings(paths.settings_file()))
        elif path == "/api/notify/config":
            self._send_json(200, notify.public_config(paths.notify_file()))
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/watchlist":
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                candidate = json.loads(self.rfile.read(length) or b"{}")
                added, msg = add_to_watchlist(CONFIG, candidate)
                self._send_json(200 if added else 409, {"added": added, "msg": msg})
            except Exception as e:
                self._send_json(400, {"added": False, "msg": f"bad request: {e}"})
        elif parsed.path == "/api/refresh":
            qs = urllib.parse.parse_qs(parsed.query)
            discover = qs.get("discover", ["0"])[0].lower() in ("1", "true", "yes")
            source = "sedi" if qs.get("source", [""])[0].lower() == "sedi" else "marketbeat"
            with _refresh_lock:
                if _refresh["running"]:
                    self._send_json(409, {"started": False, **_refresh})
                    return
                _refresh.update(
                    running=True, finished=False, ok=False, message="Starting…", date=None
                )
            threading.Thread(target=_do_refresh, args=(discover, source), daemon=True).start()
            self._send_json(202, {"started": True})
        elif parsed.path == "/api/notes":
            try:
                body = self._read_json()
                saved, msg = notes.save_note(
                    paths.notes_file(),
                    body.get("exchange", ""),
                    body.get("ticker", ""),
                    body.get("text", ""),
                )
                self._send_json(200 if saved else 400, {"saved": saved, "msg": msg})
            except Exception as e:
                self._send_json(400, {"saved": False, "msg": f"bad request: {e}"})
        elif parsed.path == "/api/settings":
            try:
                saved, msg = settings.save_settings(paths.settings_file(), self._read_json())
                self._send_json(200 if saved else 400, {"saved": saved, "msg": msg})
            except Exception as e:
                self._send_json(400, {"saved": False, "msg": f"bad request: {e}"})
        elif parsed.path == "/api/notify/settings":
            try:
                notify.save_settings(paths.notify_file(), self._read_json())
                self._send_json(200, {"ok": True, "msg": "Settings saved."})
            except Exception as e:
                self._send_json(400, {"ok": False, "msg": f"bad request: {e}"})
        elif parsed.path == "/api/notify/test":
            try:
                ok, msg = notify.send_test(paths.notify_file())
                self._send_json(200 if ok else 502, {"ok": ok, "msg": msg})
            except Exception as e:
                self._send_json(502, {"ok": False, "msg": f"{type(e).__name__}: {e}"})
        elif parsed.path == "/api/alarms":
            try:
                added, msg = notify.add_alarm(paths.notify_file(), self._read_json(), DATA_DIR)
                self._send_json(200 if added else 409, {"added": added, "msg": msg})
            except Exception as e:
                self._send_json(400, {"added": False, "msg": f"bad request: {e}"})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/watchlist":
            qs = urllib.parse.parse_qs(parsed.query)
            exchange = qs.get("exchange", [""])[0]
            ticker = qs.get("ticker", [""])[0]
            try:
                removed, msg = remove_from_watchlist(CONFIG, exchange, ticker)
                self._send_json(200 if removed else 404, {"removed": removed, "msg": msg})
            except Exception as e:
                self._send_json(400, {"removed": False, "msg": f"bad request: {e}"})
        elif parsed.path == "/api/alarms":
            alarm_id = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
            try:
                removed, msg = notify.remove_alarm(paths.notify_file(), alarm_id)
                self._send_json(200 if removed else 404, {"removed": removed, "msg": msg})
            except Exception as e:
                self._send_json(400, {"removed": False, "msg": f"bad request: {e}"})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")


# ---- desktop "app window" support -------------------------------------------
# A dedicated Chrome profile (its lifetime controls the server) so the window
# runs as its own browser process and never disturbs the user's main browser.


def _playwright_cache() -> Path:
    """Where Playwright downloads its browsers, per OS."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _find_chrome() -> str | None:
    """Locate a Chromium-family binary for chromeless --app windows, on any OS.

    Prefers a system Chrome/Chromium/Edge; falls back to the Chromium that
    Playwright downloaded for the scraper so no extra install is needed.
    """
    # 1) anything on PATH (covers Linux, and Windows/macOS when installers add it)
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "brave-browser",
        "microsoft-edge",
        "chrome",
        "msedge",
    ):
        found = shutil.which(name)
        if found:
            return found

    # 2) well-known install locations per OS
    candidates: list[Path] = []
    if sys.platform == "win32":
        prog = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")),
        ]
        for p in prog:
            candidates += [
                Path(p) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(p) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(p) / "Chromium" / "Application" / "chrome.exe",
            ]
    elif sys.platform == "darwin":
        candidates += [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
        ]
    for c in candidates:
        if c.exists():
            return str(c)

    # 3) fall back to Playwright's bundled Chromium (per-OS binary layout)
    cache = _playwright_cache()
    if cache.is_dir():
        for pattern in (
            "chromium-*/chrome-linux*/chrome",
            "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
            "chromium-*/chrome-win*/chrome.exe",
        ):
            hits = sorted(cache.glob(pattern))
            if hits:
                return str(hits[-1])
    return None


def _bind_server(host: str, port: int) -> ThreadingHTTPServer:
    """Bind the HTTP server, falling back to an OS-chosen free port if busy."""
    try:
        return ThreadingHTTPServer((host, port), Handler)
    except OSError:
        return ThreadingHTTPServer((host, 0), Handler)


def _wait_until_up(url: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.1)
    return False


def _open_app_window(chrome: str, url: str):
    """Open `url` as a chromeless desktop window and return the process."""
    profile = paths.chrome_profile_dir()
    cmd = [
        chrome,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--class=InSight",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,TranslateUI",
        # This window only ever shows a local page, so it has no use for a large
        # HTTP cache or for Chromium's background component downloads (ML model
        # stores, TTS engine, Safe Browsing lists) — those alone had reached
        # ~110 MB in this profile.
        *profiles.cache_args(component_updates=False),
        "--window-size=1300,880",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _prune_window_profile() -> None:
    """Drop the app window's browser caches. Best-effort — never fails an exit."""
    try:
        removed, freed = profiles.prune_profile(paths.chrome_profile_dir())
        if freed > 1_000_000:
            print(f"Reclaimed {freed / 1e6:.0f} MB of browser cache ({len(removed)} entries).")
    except Exception as e:
        print(f"Browser-cache cleanup skipped: {type(e).__name__}: {e}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="InSight insider-transaction app.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--window",
        action="store_true",
        help="open as a standalone desktop window (chromeless); closing the window quits the app",
    )
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    httpd = _bind_server(args.host, args.port)
    actual_port = httpd.server_address[1]
    url = f"http://{args.host}:{actual_port}/"

    # Desktop "app window" mode: run as a real application whose window owns the
    # server lifecycle — when the window closes, the server stops and we exit.
    if args.window:
        chrome = _find_chrome()
        if not chrome:
            print(
                "No Chrome/Chromium found for --window mode; falling back to the default browser.",
                file=sys.stderr,
            )
            args.window = False
        else:
            print(f"InSight app serving at {url}")
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            if not _wait_until_up(url):
                print("Server did not come up in time.", file=sys.stderr)
                httpd.shutdown()
                return 1
            proc = _open_app_window(chrome, url)
            try:
                proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
            print("Window closed — shutting down.")
            # The browser has exited, so its caches are safe to drop. Doing it
            # here keeps the profile self-managing: nobody has to discover a
            # cleanup command for disk they never knew was being used.
            _prune_window_profile()
            httpd.shutdown()
            return 0

    print(f"InSight app serving at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
