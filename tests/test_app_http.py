# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""The HTTP layer, driven through a real server.

`app.py` is the app's least-covered file: the pure modules behind it are well
tested, but nothing exercised the routing, status codes or JSON shapes the UI
actually depends on. These bind a real ThreadingHTTPServer on an ephemeral port
and talk to it over the loopback — no browser, no outside network, and every
path redirected into tmp_path so a test can never touch the user's own data.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from insight import app, paths


def rec(ticker="ABC", name="Jane Doe", d="2026-06-01", shares=100):
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


class Client:
    """Minimal HTTP client returning (status, parsed-or-raw body)."""

    def __init__(self, base: str):
        self.base = base

    def request(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, self._parse(r.headers.get("Content-Type", ""), r.read())
        except urllib.error.HTTPError as e:
            return e.code, self._parse(e.headers.get("Content-Type", ""), e.read())

    @staticmethod
    def _parse(ctype: str, raw: bytes):
        return json.loads(raw) if "json" in ctype else raw

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None):
        return self.request("POST", path, body if body is not None else {})

    def delete(self, path):
        return self.request("DELETE", path)


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> Iterator[Client]:
    data = tmp_path / "data"
    data.mkdir()
    (data / "insider_2026-06-30.json").write_text(
        json.dumps([rec(), rec(name="Bob Roe", shares=200)]), encoding="utf-8"
    )
    config = tmp_path / "companies.json"
    config.write_text(
        json.dumps({"companies": [{"name": "ABC Corp", "exchange": "TSE", "ticker": "ABC"}]}),
        encoding="utf-8",
    )

    # Every path the handler touches is redirected into tmp_path, so a test can
    # never read or write the developer's real app folder.
    monkeypatch.setattr(app, "DATA_DIR", data)
    monkeypatch.setattr(app, "CONFIG", config)
    monkeypatch.setattr(paths, "notes_file", lambda: tmp_path / "notes.json")
    monkeypatch.setattr(paths, "settings_file", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "notify_file", lambda: tmp_path / "notify.json")
    monkeypatch.setattr(paths, "sedi_pages_dir", lambda: tmp_path / "sedi-pages")
    monkeypatch.setattr(paths, "interviews_file", lambda: tmp_path / "interviews.json")

    from insight import aggregate

    with aggregate._CACHE_LOCK:  # no view cached from another test's fixture
        aggregate._records_cache.clear()
        aggregate._view_cache.clear()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    # serve_forever polls at 0.5 s by default, and shutdown() waits for the next
    # tick — which would put half a second on every teardown in this file.
    serve = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    serve.start()
    try:
        yield Client(f"http://127.0.0.1:{httpd.server_address[1]}")
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestPageAndAssets:
    def test_serves_the_ui(self, client: Client):
        status, body = client.get("/")
        assert status == 200
        assert b"<title>InSight" in body

    def test_index_html_is_the_same_page(self, client: Client):
        assert client.get("/index.html")[0] == 200

    def test_serves_the_icon(self, client: Client):
        status, body = client.get("/favicon.ico")
        assert status == 200
        assert body[:4] == b"\x89PNG"

    def test_unknown_paths_404(self, client: Client):
        assert client.get("/nope")[0] == 404
        assert client.get("/api/nope")[0] == 404


class TestDataEndpoints:
    def test_data_returns_the_company_view(self, client: Client):
        status, d = client.get("/api/data?days=3650")
        assert status == 200
        assert d["total_transactions"] == 2
        assert [c["ticker"] for c in d["companies"]] == ["ABC"]

    def test_insiders_returns_the_insider_view(self, client: Client):
        status, d = client.get("/api/insiders?days=3650")
        assert status == 200
        assert sorted(p["insider_name"] for p in d["insiders"]) == ["Bob Roe", "Jane Doe"]

    def test_the_old_people_route_still_works(self, client: Client):
        # Kept so a bookmark or a script written against the pre-rename name
        # does not break.
        _, old = client.get("/api/people?days=3650")
        _, new = client.get("/api/insiders?days=3650")
        assert old == new

    def test_the_window_is_honoured(self, client: Client):
        # the fixture's trades are older than a fortnight
        _, wide = client.get("/api/data?days=3650")
        _, narrow = client.get("/api/data?days=14")
        assert wide["total_transactions"] == 2
        assert narrow["total_transactions"] == 0

    def test_days_beats_months_when_both_are_given(self, client: Client):
        _, d = client.get("/api/data?days=14&months=240")
        assert d["range_days"] == 14
        assert d["range_months"] is None

    def test_a_nonsense_window_falls_back_to_everything(self, client: Client):
        # Better to show all history than to 500 on a stray query string.
        for qs in ("days=abc", "days=-5", "months=0", "days="):
            status, d = client.get(f"/api/data?{qs}")
            assert status == 200, qs
            assert d["total_transactions"] == 2, qs

    def test_data_carries_the_sedi_page_list(self, client: Client):
        _, d = client.get("/api/data?days=3650")
        assert isinstance(d["sedi_pages"], list)


class TestNotes:
    def test_starts_empty(self, client: Client):
        assert client.get("/api/notes") == (200, {"notes": {}})

    def test_round_trip(self, client: Client):
        status, d = client.post(
            "/api/notes", {"exchange": "TSE", "ticker": "ABC", "text": "• watch this"}
        )
        assert (status, d["saved"]) == (200, True)
        assert client.get("/api/notes")[1]["notes"] == {"TSE:ABC": "• watch this"}

    def test_clearing_removes_it(self, client: Client):
        client.post("/api/notes", {"exchange": "TSE", "ticker": "ABC", "text": "x"})
        client.post("/api/notes", {"exchange": "TSE", "ticker": "ABC", "text": ""})
        assert client.get("/api/notes")[1]["notes"] == {}

    def test_a_note_without_a_company_is_a_400(self, client: Client):
        status, d = client.post("/api/notes", {"text": "orphan"})
        assert status == 400 and d["saved"] is False

    def test_malformed_json_is_a_400_not_a_crash(self, client: Client):
        req = urllib.request.Request(client.base + "/api/notes", data=b"{not json", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("expected an error status")
        except urllib.error.HTTPError as e:
            assert e.code == 400


class TestSettings:
    def test_defaults(self, client: Client):
        status, d = client.get("/api/settings")
        assert status == 200
        assert d["theme"] == "dark" and d["auto"] is False

    def test_round_trip(self, client: Client):
        assert client.post("/api/settings", {"theme": "lemon"})[0] == 200
        assert client.get("/api/settings")[1]["theme"] == "lemon"

    def test_an_unknown_theme_is_rejected(self, client: Client):
        status, d = client.post("/api/settings", {"theme": "neon"})
        assert status == 400 and d["saved"] is False
        assert client.get("/api/settings")[1]["theme"] == "dark", "a bad write changed nothing"

    def test_a_light_theme_cannot_be_the_dark_pick(self, client: Client):
        assert client.post("/api/settings", {"auto_dark": "lemon"})[0] == 400


class TestAutostart:
    @pytest.fixture(autouse=True)
    def _sandbox(self, tmp_path: Path, monkeypatch):
        # Never touch the real autostart directories from a test.
        from insight import autostart

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
        monkeypatch.setattr(autostart.shutil, "which", lambda _: "/usr/local/bin/insight")
        monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: None)

    def test_status_reports_disabled_initially(self, client: Client):
        status, d = client.get("/api/autostart")
        assert status == 200
        assert d["enabled"] is False
        assert d["command"].endswith("--window")

    def test_enable_then_disable(self, client: Client):
        status, d = client.post("/api/autostart", {"enabled": True})
        assert (status, d["ok"], d["enabled"]) == (200, True, True)
        assert Path(d["path"]).exists()

        status, d = client.post("/api/autostart", {"enabled": False})
        assert (status, d["ok"], d["enabled"]) == (200, True, False)
        assert not Path(d["path"]).exists()

    def test_the_response_carries_fresh_status(self, client: Client):
        # So the UI can re-render from the reply instead of re-fetching.
        _, d = client.post("/api/autostart", {"enabled": True})
        assert client.get("/api/autostart")[1]["enabled"] == d["enabled"]


class TestWatchlist:
    def test_removing_a_company(self, client: Client):
        status, d = client.delete("/api/watchlist?exchange=TSE&ticker=ABC")
        assert (status, d["removed"]) == (200, True)
        assert client.get("/api/data?days=3650")[1]["companies"] == []

    def test_removing_something_absent_is_a_404(self, client: Client):
        status, d = client.delete("/api/watchlist?exchange=TSE&ticker=NOPE")
        assert status == 404 and d["removed"] is False

    def test_a_malformed_candidate_is_refused_with_a_reason(self, client: Client):
        # The handler maps every "not added" to 409, so a missing-fields body
        # reports Conflict rather than Bad Request. Imprecise, but the UI branches
        # on `added` and shows `msg`, so that is the contract pinned here rather
        # than a status code no caller reads.
        status, d = client.post("/api/watchlist", {"nonsense": True})
        assert status in (400, 409)
        assert d["added"] is False
        assert "missing" in d["msg"]


class TestAlarmsAndNotify:
    def test_notify_config_starts_empty_and_masks_nothing_secret(self, client: Client):
        status, d = client.get("/api/notify/config")
        assert status == 200
        assert d["alarms"] == []
        assert "password" in d["email"]

    def test_setting_and_listing_an_alarm(self, client: Client):
        status, d = client.post(
            "/api/alarms", {"type": "company", "exchange": "TSE", "ticker": "ABC"}
        )
        assert (status, d["added"]) == (200, True)
        alarms = client.get("/api/notify/config")[1]["alarms"]
        assert len(alarms) == 1
        assert "seen" not in alarms[0], "bookkeeping must not be published"

    def test_a_duplicate_alarm_is_a_409(self, client: Client):
        spec = {"type": "person", "name": "Jane Doe"}
        assert client.post("/api/alarms", spec)[0] == 200
        assert client.post("/api/alarms", spec)[0] == 409

    def test_deleting_an_alarm(self, client: Client):
        client.post("/api/alarms", {"type": "person", "name": "Jane Doe"})
        alarm_id = client.get("/api/notify/config")[1]["alarms"][0]["id"]
        assert client.delete(f"/api/alarms?id={alarm_id}")[0] == 200
        assert client.get("/api/notify/config")[1]["alarms"] == []

    def test_deleting_an_unknown_alarm_is_a_404(self, client: Client):
        assert client.delete("/api/alarms?id=nope")[0] == 404

    def test_saving_notify_settings(self, client: Client):
        status, d = client.post("/api/notify/settings", {"ntfy": {"enabled": True, "topic": "t"}})
        assert (status, d["ok"]) == (200, True)
        assert client.get("/api/notify/config")[1]["ntfy"]["topic"] == "t"


class TestRefresh:
    def test_status_when_idle(self, client: Client):
        status, d = client.get("/api/refresh/status")
        assert status == 200 and d["running"] is False

    def test_starting_a_refresh_reports_accepted(self, client: Client, monkeypatch):
        # The real job launches a browser, so only the handshake is exercised.
        started = threading.Event()

        def fake_refresh(discover, source):
            started.set()
            with app._refresh_lock:
                app._refresh.update(running=False, finished=True, ok=True)

        monkeypatch.setattr(app, "_do_refresh", fake_refresh)
        status, d = client.post("/api/refresh")
        assert (status, d["started"]) == (202, True)
        assert started.wait(timeout=5), "the job should have been dispatched"

    def test_a_second_refresh_while_one_runs_is_a_409(self, client: Client, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(app, "_do_refresh", lambda discover, source: release.wait(timeout=5))
        assert client.post("/api/refresh")[0] == 202
        try:
            status, d = client.post("/api/refresh")
            assert status == 409 and d["started"] is False
        finally:
            release.set()
            with app._refresh_lock:
                app._refresh.update(running=False)


class TestSediPage:
    def test_missing_page_explains_itself(self, client: Client):
        status, body = client.get("/api/sedi-page?exchange=TSE&ticker=ABC")
        assert status == 404
        assert b"No saved SEDI page" in body


class TestSediRefreshWiring:
    """The in-app '⛏ Fetch from SEDI' button must configure the scraper the way
    the CLI does.

    The two call sites build `SediScraper` separately, and they drifted: the
    button omitted `pages_dir`, so it scraped the data but saved no report pages.
    `_sedi_page_keys()` then stayed empty forever and the '⛏ SEDI report' link
    never appeared — a whole UI feature dead for anyone who never runs the CLI,
    which is most people. Nothing failed; the link simply was not there.
    """

    @pytest.fixture
    def spy(self, tmp_path: Path, monkeypatch) -> dict:
        """Run _do_refresh(source="sedi") against stubs, capturing the scraper's
        kwargs. No browser, no network, nothing written outside tmp_path."""
        from insight import marketbeat, scrape, sedi

        config = tmp_path / "companies.json"
        config.write_text(
            json.dumps({"companies": [{"name": "ABC Corp", "exchange": "TSE", "ticker": "ABC"}]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(app, "CONFIG", config)
        monkeypatch.setattr(app, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(paths, "sedi_pages_dir", lambda: tmp_path / "sedi-pages")
        monkeypatch.setattr(paths, "interviews_file", lambda: tmp_path / "interviews.json")
        monkeypatch.setattr(paths, "sedi_profile_dir", lambda: tmp_path / "sedi-profile")
        monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
        monkeypatch.setattr(paths, "app_dir", lambda: tmp_path)

        seen: dict = {}

        class FakeScraper:
            def __init__(self, **kw):
                seen.update(kw)

        monkeypatch.setattr(sedi, "SediScraper", FakeScraper)
        # scrape_many is what actually calls the factory, so invoke it — a
        # factory that is built but never called would prove nothing.
        monkeypatch.setattr(
            marketbeat, "scrape_many", lambda targets, **kw: (kw["scraper_factory"](), {})[1]
        )
        monkeypatch.setattr(scrape, "write_outputs", lambda *a, **k: None)
        monkeypatch.setattr(app, "_finish_refresh", lambda *a, **k: None)

        with app._refresh_lock:
            app._refresh["message"] = ""
        app._do_refresh(source="sedi")
        # _do_refresh swallows exceptions into job state, so an exploding stub
        # would otherwise look exactly like a pass. ("ok" is no use here —
        # _finish_refresh is what sets it, and it is stubbed.)
        assert not app._refresh["message"].startswith("Refresh failed"), app._refresh["message"]
        assert seen, "the scraper factory was never called"
        return seen

    def test_it_snapshots_the_report_pages(self, spy: dict, tmp_path: Path):
        assert spy.get("pages_dir") == tmp_path / "sedi-pages"

    def test_it_uses_the_persistent_profile(self, spy: dict, tmp_path: Path):
        # That cookie jar IS the solved CAPTCHA; a fresh profile would mean
        # solving the bot wall on every fetch.
        assert spy.get("profile_dir") == tmp_path / "sedi-profile"

    def test_it_opens_a_visible_browser(self, spy: dict):
        # Headless cannot get past the wall, and nobody can solve a challenge
        # they cannot see.
        assert spy.get("headless") is False


class TestInsiderSearchRoute:
    """The in-app front end for SEDI's person search.

    It shares the refresh job slot deliberately: both drive the one visible SEDI
    browser against one profile, and two at once would fight over the session.
    """

    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch):
        with app._refresh_lock:
            app._refresh.update(running=False, finished=False, ok=False, message="")
        # Never launch a browser from a test.
        monkeypatch.setattr(app, "_do_insider_search", lambda name: None)

    def test_a_name_starts_a_job(self, client: Client):
        status, d = client.post("/api/insider-search", {"name": "Sprott"})
        assert (status, d["started"], d["name"]) == (202, True, "Sprott")

    def test_a_missing_name_is_refused(self, client: Client):
        status, d = client.post("/api/insider-search", {})
        assert status == 400 and d["started"] is False

    def test_a_blank_name_is_refused(self, client: Client):
        # " " would otherwise open a browser and search SEDI for nothing.
        status, d = client.post("/api/insider-search", {"name": "   "})
        assert status == 400 and d["started"] is False

    def test_it_will_not_run_alongside_a_refresh(self, client: Client, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(app, "_do_refresh", lambda discover, source: release.wait(timeout=5))
        assert client.post("/api/refresh")[0] == 202
        try:
            status, d = client.post("/api/insider-search", {"name": "Sprott"})
            assert status == 409 and d["started"] is False
        finally:
            release.set()
            with app._refresh_lock:
                app._refresh.update(running=False)

    def test_the_result_starts_empty_for_a_new_search(self, client: Client):
        app._insider_result.update(name="Old", companies=[{"issuer_name": "stale"}])
        client.post("/api/insider-search", {"name": "Sprott"})
        status, d = client.get("/api/insider-search")
        assert status == 200
        assert (d["name"], d["companies"]) == ("Sprott", []), "a new search showed the old result"

    def test_the_result_is_readable_after_the_job_ends(self, client: Client):
        # Served separately from the job status so a reload still finds it.
        app._insider_result.update(
            name="Sprott", companies=[{"issuer_name": "West Red Lake", "ticker": "WRLG"}]
        )
        status, d = client.get("/api/insider-search")
        assert status == 200 and d["companies"][0]["ticker"] == "WRLG"


class TestInterviews:
    """The Interviews tab's endpoints. No YouTube, no LLM: only the routing,
    the storage and the two ways an extraction reaches a company's notes."""

    def saved(self, tmp_path, applied=False, **over):
        from insight import interviews

        entry = {
            "video_id": "vid123",
            "url": "https://youtu.be/vid123",
            "title": "Rick Rule on gold",
            "speaker": "Rick Rule",
            "companies": [
                {
                    "name": "ABC Corp",
                    "matched_name": "ABC Corp",
                    "exchange": "TSE",
                    "ticker": "ABC",
                    "on_watchlist": True,
                    "applied": applied,
                    "bullets": ["[Rick Rule] Cheap.", "[Rick Rule] Execution superb."],
                },
                {
                    "name": "New Venture Ltd",
                    "exchange": "TSXV",
                    "ticker": "NEW",
                    "on_watchlist": False,
                    "resolved": True,
                    "applied": False,
                    "bullets": ["[Rick Rule] Worth a look."],
                },
            ],
            **over,
        }
        interviews.save_extraction(entry, tmp_path / "interviews.json")

    def test_the_list_is_empty_before_anything_is_added(self, client):
        assert client.get("/api/interviews") == (200, {"videos": []})

    def test_a_link_that_is_not_a_youtube_video_is_refused(self, client):
        status, body = client.post("/api/interviews", {"url": "https://example.com/x"})
        assert status == 400 and body["started"] is False

    def test_a_saved_run_is_served_back(self, client, tmp_path):
        self.saved(tmp_path)
        status, body = client.get("/api/interviews")
        assert status == 200
        assert [v["video_id"] for v in body["videos"]] == ["vid123"]

    def test_bullets_land_in_the_notes_of_a_followed_company(self, client, tmp_path):
        self.saved(tmp_path)
        assert (
            client.post("/api/interviews/apply", {"video_id": "vid123", "company": "ABC Corp"})[0]
            == 200
        )
        note = client.get("/api/notes")[1]["notes"]["TSE:ABC"]
        assert "[Rick Rule] Cheap." in note and "[Rick Rule] Execution superb." in note

    def test_applying_appends_rather_than_replacing_what_the_user_wrote(self, client, tmp_path):
        # The notes are the user's own writing; an interview is one more voice
        # in them, not the last word.
        self.saved(tmp_path)
        client.post("/api/notes", {"exchange": "TSE", "ticker": "ABC", "text": "My own thesis."})
        client.post("/api/interviews/apply", {"video_id": "vid123", "company": "ABC Corp"})
        note = client.get("/api/notes")[1]["notes"]["TSE:ABC"]
        assert note.startswith("My own thesis.") and "[Rick Rule] Cheap." in note

    def test_applying_twice_does_not_duplicate_the_bullets(self, client, tmp_path):
        self.saved(tmp_path)
        for _ in range(2):
            client.post("/api/interviews/apply", {"video_id": "vid123", "company": "ABC Corp"})
        assert client.get("/api/notes")[1]["notes"]["TSE:ABC"].count("[Rick Rule] Cheap.") == 1

    def test_applying_marks_it_so_the_ui_stops_offering(self, client, tmp_path):
        self.saved(tmp_path)
        client.post("/api/interviews/apply", {"video_id": "vid123", "company": "ABC Corp"})
        companies = client.get("/api/interviews")[1]["videos"][0]["companies"]
        assert companies[0]["applied"] is True

    def test_an_unknown_company_is_a_400_not_a_crash(self, client, tmp_path):
        self.saved(tmp_path)
        status, body = client.post(
            "/api/interviews/apply", {"video_id": "vid123", "company": "Nope"}
        )
        assert status == 400 and body["saved"] is False

    def test_adding_a_suggestion_puts_it_on_the_watchlist_with_its_comments(self, client, tmp_path):
        # The two halves are useless apart, so one call does both.
        self.saved(tmp_path)
        status, body = client.post(
            "/api/interviews/add",
            {
                "video_id": "vid123",
                "company": "New Venture Ltd",
                "name": "New Venture Ltd",
                "exchange": "TSXV",
                "ticker": "NEW",
            },
        )
        assert status == 200 and body["added"] is True
        cfg = json.loads((tmp_path / "companies.json").read_text())
        assert "New Venture Ltd" in [c["name"] for c in cfg["companies"]]
        assert "[Rick Rule] Worth a look." in client.get("/api/notes")[1]["notes"]["TSXV:NEW"]

    def test_an_interview_can_be_forgotten(self, client, tmp_path):
        self.saved(tmp_path)
        assert client.delete("/api/interviews?video=vid123")[0] == 200
        assert client.get("/api/interviews")[1]["videos"] == []

    def test_forgetting_something_that_is_not_there_is_a_404(self, client):
        assert client.delete("/api/interviews?video=nope")[0] == 404
