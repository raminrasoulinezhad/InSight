# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""The app in a real browser.

Everything else in `tests/webui/` runs the page inside a `node:vm` with a DOM
stub, which is fast and covers the logic but has no rendering, no focus model
and no keyboard. That leaves a real gap: a keydown handler can look perfect and
still never fire, and a layout can overflow on a narrow screen with every unit
test passing.

So this file covers only what the harness cannot — genuine key presses, genuine
focus movement, computed styles and viewport geometry — against the real server
and the real page. Playwright already ships with the project for the scraper, so
this adds no dependency; it skips when the browser binary hasn't been installed.

Kept deliberately small: these are slow next to the 300-odd tests around them,
so anything provable without a browser belongs in one of those instead.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from insight import app, paths

playwright_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed")


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    """One Chromium for the whole module; skip cleanly if it isn't installed."""
    with playwright_api.sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(
                f"chromium unavailable ({str(e).splitlines()[0]}); "
                "run: uv run playwright install chromium"
            )
        try:
            yield instance
        finally:
            instance.close()


def _rec(name="Jane Doe", ticker="ABC", d="2026-06-01", shares=100):
    return {
        "exchange": "TSE",
        "ticker": ticker,
        "issuer_name": f"{ticker} Corp",
        "insider_name": name,
        "insider_role": "CEO",
        "entity_type": "individual",
        "transaction_type": "Buy",
        "transaction_date": d,
        "shares": shares,
        "avg_price": 10.0,
        "total_value": 1000.0,
        "currency": "CAD",
        "is_issuer_buyback": False,
    }


@pytest.fixture
def served(tmp_path: Path, monkeypatch) -> Iterator[str]:
    """The real app, on an ephemeral port, with all state under tmp_path."""
    data = tmp_path / "data"
    data.mkdir()
    from datetime import date, timedelta

    recent = (date.today() - timedelta(days=2)).isoformat()
    (data / "insider_2026-06-30.json").write_text(
        json.dumps([_rec(d=recent), _rec(name="Bob Roe", d=recent, shares=200)]), encoding="utf-8"
    )
    config = tmp_path / "companies.json"
    config.write_text(
        json.dumps({"companies": [{"name": "ABC Corp", "exchange": "TSE", "ticker": "ABC"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(app, "DATA_DIR", data)
    monkeypatch.setattr(app, "CONFIG", config)
    monkeypatch.setattr(paths, "notes_file", lambda: tmp_path / "notes.json")
    monkeypatch.setattr(paths, "settings_file", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "notify_file", lambda: tmp_path / "notify.json")
    monkeypatch.setattr(paths, "sedi_pages_dir", lambda: tmp_path / "sedi-pages")

    from insight import aggregate

    with aggregate._CACHE_LOCK:
        aggregate._records_cache.clear()
        aggregate._view_cache.clear()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def page(browser, served: str) -> Iterator[object]:
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    p = ctx.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.goto(served)
    p.wait_for_selector(".company", timeout=15_000)
    try:
        yield p
    finally:
        assert not errors, f"uncaught page errors: {errors}"
        ctx.close()


class TestKeyboardInTheNoteEditor:
    """Real key presses — the vm harness can only dispatch synthetic events."""

    def _open_editor(self, page):
        page.click(".note-btn")
        page.wait_for_selector(".note-ta")

    def test_the_editor_opens_with_a_bullet_and_the_caret_at_the_end(self, page):
        self._open_editor(page)
        assert page.input_value(".note-ta") == "• "
        assert page.evaluate("document.activeElement.className") == "note-ta"

    def test_enter_starts_the_next_bullet(self, page):
        # The behaviour I could not confirm without a real browser: a keydown
        # handler that preventDefaults and inserts text.
        self._open_editor(page)
        page.keyboard.type("first point")
        page.keyboard.press("Enter")
        page.keyboard.type("second point")
        assert page.input_value(".note-ta") == "• first point\n• second point"

    def test_backspace_into_an_empty_bullet_removes_it(self, page):
        self._open_editor(page)
        page.keyboard.type("only point")
        page.keyboard.press("Enter")
        assert page.input_value(".note-ta").endswith("\n• ")
        page.keyboard.press("Backspace")
        assert page.input_value(".note-ta") == "• only point"

    def test_ctrl_enter_saves_and_closes(self, page):
        self._open_editor(page)
        page.keyboard.type("saved by keyboard")
        page.keyboard.press("Control+Enter")
        page.wait_for_selector(".note-view li")
        assert page.text_content(".note-view li").strip() == "saved by keyboard"
        assert page.locator(".note-ta").count() == 0

    def test_escape_discards(self, page):
        self._open_editor(page)
        page.keyboard.type("never mind")
        page.keyboard.press("Escape")
        assert page.locator(".note-ta").count() == 0
        assert page.locator(".note-view").count() == 0

    def test_a_saved_note_survives_a_reload(self, page, served: str):
        self._open_editor(page)
        page.keyboard.type("durable")
        page.keyboard.press("Control+Enter")
        page.wait_for_selector(".note-view li")
        page.goto(served)
        page.wait_for_selector(".note-view li")
        assert page.text_content(".note-view li").strip() == "durable"


class TestDialogFocus:
    """Focus is a browser concept; the stub has none."""

    def _open(self, page):
        page.click("#settings-open")
        page.wait_for_selector("#settings:not(.hidden)")

    def test_a_real_click_opens_the_dialog(self, page):
        self._open(page)
        assert page.is_visible("#settings .modal-card")

    def test_tab_cycles_inside_the_dialog(self, page):
        self._open(page)
        inside = "document.querySelector('#settings .modal-card').contains(document.activeElement)"
        for _ in range(25):  # more presses than the dialog has controls
            page.keyboard.press("Tab")
            assert page.evaluate(inside), "focus escaped the dialog"

    def test_shift_tab_also_stays_inside(self, page):
        self._open(page)
        inside = "document.querySelector('#settings .modal-card').contains(document.activeElement)"
        for _ in range(25):
            page.keyboard.press("Shift+Tab")
            assert page.evaluate(inside), "focus escaped the dialog backwards"

    def test_escape_closes_and_returns_focus(self, page):
        self._open(page)
        page.keyboard.press("Escape")
        # wait_for_selector waits for visibility, which a hidden dialog never is
        page.wait_for_function(
            "() => document.getElementById('settings').classList.contains('hidden')"
        )
        assert page.evaluate("document.activeElement.id") == "settings-open"

    def test_alt_left_does_not_navigate_behind_the_dialog(self, page):
        page.click('#tabs button[data-view="insiders"]')
        page.wait_for_function("() => STATE.view === 'insiders'")
        self._open(page)
        page.keyboard.press("Alt+ArrowLeft")
        assert page.evaluate("STATE.view") == "insiders"
        assert page.is_visible("#settings .modal-card")


class TestKeyboardNavigation:
    def test_alt_left_steps_back(self, page):
        page.click('#tabs button[data-view="insiders"]')
        page.wait_for_function("() => STATE.view === 'insiders'")
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_function("() => STATE.view === 'companies'")
        assert page.evaluate("STATE.nav.length") == 0

    def test_the_back_button_is_disabled_with_nowhere_to_go(self, page):
        assert page.is_disabled("#back")


class TestRenderingAndLayout:
    """Computed styles and geometry — neither exists without a renderer."""

    def test_choosing_a_theme_repaints_the_page(self, page):
        before = page.evaluate("getComputedStyle(document.body).backgroundColor")
        page.click("#settings-open")
        page.wait_for_selector(".theme-card")
        page.click('.theme-card[data-theme-id="lemon"]')
        page.wait_for_function("() => document.documentElement.dataset.theme === 'lemon'")
        after = page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert after != before
        assert after == "rgb(255, 252, 230)", "the Lemon paper colour should actually be painted"

    def test_the_terminal_theme_really_changes_the_typeface(self, page):
        page.click("#settings-open")
        page.wait_for_selector(".theme-card")
        page.click('.theme-card[data-theme-id="terminal"]')
        page.wait_for_function("() => document.documentElement.dataset.theme === 'terminal'")
        font = page.evaluate("getComputedStyle(document.body).fontFamily")
        assert "mono" in font.lower(), font

    def test_the_page_does_not_scroll_sideways_on_a_phone(self, page):
        page.set_viewport_size({"width": 375, "height": 812})
        page.wait_for_timeout(200)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"{overflow}px of horizontal overflow"

    def test_the_dialog_fits_a_phone_screen(self, page):
        page.set_viewport_size({"width": 375, "height": 812})
        page.click("#settings-open")
        page.wait_for_selector("#settings .modal-card")
        box = page.locator("#settings .modal-card").bounding_box()
        assert box["width"] <= 375 and box["height"] <= 812, box

    def test_the_timeline_strip_stays_one_thin_row(self, page):
        # Its whole point is being compact; a regression here is invisible to a
        # geometry test on the SVG source but obvious on screen.
        height = page.evaluate(
            "document.querySelector('.chart svg')?.getBoundingClientRect().height ?? 0"
        )
        assert 0 < height <= 60, height

    def test_the_transaction_table_scrolls_rather_than_bursting_its_card(self, page):
        page.set_viewport_size({"width": 375, "height": 812})
        page.wait_for_timeout(200)
        wrap = page.locator(".txn-table-wrap").first
        assert wrap.evaluate("el => getComputedStyle(el).overflowX") == "auto"


class TestTheScrapeProgressBar:
    """A SEDI fetch is minutes of a browser window doing nothing this page can
    see, so the bar is the only evidence the app has not hung.

    The vm harness proves the percentage maths; only a real browser proves the
    element is actually visible, actually animated, and actually goes away.
    """

    @pytest.fixture
    def gate(self, monkeypatch):
        """Replace the scrape with one the test can hold open at a known point.

        Progress goes through the real `app._progress`, so this exercises the
        genuine status route and the genuine polling loop — only the browser and
        the network are faked out.
        """
        release = threading.Event()

        def fake_refresh(discover=False, source="marketbeat"):
            app._progress(1, 3, "Beta Inc")  # one of three done -> 33%
            release.wait(timeout=15)
            app._progress(3, 3, "")
            with app._refresh_lock:
                app._refresh.update(
                    running=False, finished=True, ok=True, message="Done — test", done=3, label=""
                )

        with app._refresh_lock:  # never inherit another test's job state
            app._refresh.update(
                running=False, finished=False, ok=False, message="", done=0, total=0, label=""
            )
        monkeypatch.setattr(app, "_do_refresh", fake_refresh)
        try:
            yield release
        finally:
            release.set()

    def test_it_is_hidden_until_something_starts(self, page, gate):
        assert page.locator("#progress").is_visible() is False

    def test_it_appears_immediately_and_animates_before_the_count_is_known(self, page, gate):
        # Between the click and the first status poll the app knows only that
        # work started. A determinate bar frozen at 0% for the minute it takes
        # to open a browser is what users read as a crash.
        page.click("#sedi")
        page.wait_for_selector("#progress:not(.hidden)", timeout=5_000)
        assert page.locator("#progress").get_attribute("class").find("indeterminate") != -1
        assert page.locator("#progress").get_attribute("aria-valuenow") is None

    def test_it_fills_to_the_real_percentage_once_counting_starts(self, page, gate):
        page.click("#sedi")
        page.wait_for_function(
            "document.querySelector('#progress')?.getAttribute('aria-valuenow') === '33'",
            timeout=10_000,
        )
        width = page.evaluate("getComputedStyle(document.querySelector('#progress-fill')).width")
        track = page.evaluate("getComputedStyle(document.querySelector('#progress')).width")
        assert 0.3 < float(width[:-2]) / float(track[:-2]) < 0.36, (width, track)

    def test_it_says_which_company_is_being_fetched(self, page, gate):
        # "Fetching 2 of 3" alone still leaves the user watching a silent window.
        page.click("#sedi")
        page.wait_for_function(
            "document.getElementById('refreshmsg').textContent.includes('Beta Inc')",
            timeout=10_000,
        )
        assert "2 of 3" in page.text_content("#refreshmsg")

    def test_it_goes_away_once_the_fetch_is_done(self, page, gate):
        page.click("#sedi")
        page.wait_for_selector("#progress:not(.hidden)", timeout=5_000)
        gate.set()
        page.wait_for_selector("#progress.hidden", state="attached", timeout=10_000)
        page.wait_for_function(
            "document.querySelector('#progress').classList.contains('hidden')", timeout=10_000
        )
        assert page.locator("#refresh").is_disabled() is False, "the buttons must come back"
