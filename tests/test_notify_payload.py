# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""What alarms publish, and how much they remember.

Two coupled problems these guard against: `/api/notify/config` used to ship every
alarm's `seen` key set to the browser (2.8 MB against 56 KB of actual data), and
`seen` itself was re-baselined to an alarm's entire matching history on every
fire, so notify.json grew without bound.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from insight import notify
from test_notify import rec, write_snapshot


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def enable_ntfy(p: Path) -> None:
    cfg = notify.load_config(p)
    cfg["ntfy"] = {"enabled": True, "server": "https://ntfy.sh", "topic": "t"}
    notify.save_config(p, cfg)


class TestPublicConfigStaysSmall:
    def test_seen_keys_are_never_published(self, tmp_path: Path):
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(
            data,
            "2026-06-30",
            [rec("ATH", f"Insider {i}", d=days_ago(5), shares=100 + i) for i in range(50)],
        )
        p = tmp_path / "notify.json"
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        stored = notify.load_config(p)["alarms"][0]
        assert stored["seen"], "the alarm should have a baseline to publish-or-not"

        pub = notify.public_config(p)
        assert "seen" not in pub["alarms"][0]
        assert "seen" not in json.dumps(pub)

    def test_the_published_payload_is_a_fraction_of_the_stored_one(self, tmp_path: Path):
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(
            data,
            "2026-06-30",
            [rec("ATH", f"Insider {i}", d=days_ago(3), shares=1000 + i) for i in range(200)],
        )
        p = tmp_path / "notify.json"
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        on_disk = len(p.read_text(encoding="utf-8"))
        published = len(json.dumps(notify.public_config(p)))
        assert published * 10 < on_disk, (
            f"published {published} B vs {on_disk} B stored — the bookkeeping is leaking again"
        )

    def test_the_fields_the_ui_renders_survive(self, tmp_path: Path):
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [rec("ATH", "Insider A", d=days_ago(2))])
        p = tmp_path / "notify.json"
        notify.add_alarm(
            p, {"type": "company", "exchange": "TSE", "ticker": "ATH", "label": "Athabasca"}, data
        )
        notify.add_alarm(p, {"type": "person", "name": "Eric Sprott"}, data)

        company, person = notify.public_config(p)["alarms"]
        # the UI needs these to list the alarm and to light the right bell
        for field in ("id", "type", "label", "created"):
            assert field in company and field in person
        assert (company["exchange"], company["ticker"]) == ("TSE", "ATH")
        assert person["name"] == "Eric Sprott"

    def test_the_password_is_still_masked(self, tmp_path: Path):
        p = tmp_path / "notify.json"
        cfg = notify.load_config(p)
        cfg["email"]["password"] = "supersecret"
        notify.save_config(p, cfg)
        assert "supersecret" not in json.dumps(notify.public_config(p))


class TestSeenSetStaysBounded:
    def _alarm(self, p: Path):
        return notify.load_config(p)["alarms"][0]

    def test_baseline_ignores_trades_outside_the_horizon(self, tmp_path: Path):
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(
            data,
            "2026-06-30",
            [
                rec("ATH", "Recent", d=days_ago(5), shares=1),
                rec("ATH", "Ancient", d=days_ago(notify.ALERT_HORIZON_DAYS + 30), shares=2),
            ],
        )
        p = tmp_path / "notify.json"
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)
        seen = self._alarm(p)["seen"]
        assert len(seen) == 1
        assert any("Recent".lower() in k.lower() for k in seen)

    def test_an_old_trade_never_fires(self, tmp_path: Path, monkeypatch):
        sent = []
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, m: sent.append(t))
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-29", [rec("ATH", "Recent", d=days_ago(5))])
        p = tmp_path / "notify.json"
        enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        # a later scrape backfills an old filing — interesting, but not news
        write_snapshot(
            data,
            "2026-06-30",
            [
                rec("ATH", "Recent", d=days_ago(5)),
                rec("ATH", "Backfilled", d=days_ago(notify.ALERT_HORIZON_DAYS + 10)),
            ],
        )
        assert notify.evaluate_and_notify(p, data)["sent"] == 0
        assert sent == []

    def test_a_recent_trade_still_fires(self, tmp_path: Path, monkeypatch):
        sent = []
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, m: sent.append(t))
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-29", [rec("ATH", "Old hand", d=days_ago(20))])
        p = tmp_path / "notify.json"
        enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        write_snapshot(
            data,
            "2026-06-30",
            [rec("ATH", "Old hand", d=days_ago(20)), rec("ATH", "Fresh", d=days_ago(1))],
        )
        assert notify.evaluate_and_notify(p, data)["sent"] == 1
        assert sent, "a trade inside the horizon must still alert"

    def test_seen_is_pruned_as_trades_age_out(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, m: None)
        data = tmp_path / "data"
        data.mkdir()
        p = tmp_path / "notify.json"
        enable_ntfy(p)

        # baseline with a trade near the edge of the horizon
        write_snapshot(data, "2026-06-29", [rec("ATH", "Edge", d=days_ago(10))])
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)
        assert len(self._alarm(p)["seen"]) == 1

        # it slides out of the window; the key is no longer needed
        monkeypatch.setattr(notify, "ALERT_HORIZON_DAYS", 5)
        notify.evaluate_and_notify(p, data)
        assert self._alarm(p)["seen"] == []

    def test_an_alarm_bloated_by_an_older_version_is_migrated(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, m: None)
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [rec("ATH", "Recent", d=days_ago(2))])
        p = tmp_path / "notify.json"
        enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        # simulate the old behaviour: `seen` holding the whole history
        cfg = notify.load_config(p)
        cfg["alarms"][0]["seen"] = sorted(
            set(cfg["alarms"][0]["seen"]) | {f"stale-key-{i}" for i in range(5000)}
        )
        notify.save_config(p, cfg)
        assert len(self._alarm(p)["seen"]) == 5001

        notify.evaluate_and_notify(p, data)
        assert len(self._alarm(p)["seen"]) == 1, "stale keys should be dropped on the next scrape"

    def test_pruning_does_not_resurrect_old_alerts(self, tmp_path: Path, monkeypatch):
        # The danger of forgetting a key: the same trade alerting twice.
        sent = []
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, m: sent.append(t))
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-29", [rec("ATH", "A", d=days_ago(30))])
        p = tmp_path / "notify.json"
        enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        write_snapshot(
            data, "2026-06-30", [rec("ATH", "A", d=days_ago(30)), rec("ATH", "B", d=days_ago(1))]
        )
        assert notify.evaluate_and_notify(p, data)["sent"] == 1
        # repeated scrapes with no new data must stay silent
        for _ in range(3):
            assert notify.evaluate_and_notify(p, data)["sent"] == 0
        assert len(sent) == 1

    def test_the_stored_file_stays_small_under_heavy_activity(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, t, m: None)
        data = tmp_path / "data"
        data.mkdir()
        p = tmp_path / "notify.json"
        enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        # two years of daily trades, scraped as they arrive
        for day in range(0, 700, 7):
            write_snapshot(
                data,
                f"2026-06-{(day % 28) + 1:02d}",
                [rec("ATH", "A", d=days_ago(day), shares=100 + day)],
            )
            notify.evaluate_and_notify(p, data)

        seen = self._alarm(p)["seen"]
        assert len(seen) < 40, f"seen grew to {len(seen)} keys — the horizon is not bounding it"
