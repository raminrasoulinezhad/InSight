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
