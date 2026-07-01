# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Alarms + notification logic (insight.notify), senders monkeypatched."""

import json

from insight import notify


def rec(ticker, name, ttype="Buy", shares=100, value=1000.0, d="2026-06-01", exch="TSE"):
    return {
        "issuer_name": ticker + " Inc",
        "exchange": exch,
        "ticker": ticker,
        "insider_name": name,
        "insider_role": "Director",
        "entity_type": "individual",
        "transaction_date": d,
        "transaction_type": ttype,
        "shares": shares,
        "avg_price": value / shares if shares else None,
        "total_value": value,
        "currency": "CAD",
        "is_issuer_buyback": False,
    }


def write_snapshot(data_dir, name, records):
    (data_dir / f"insider_{name}.json").write_text(json.dumps(records))


class TestAlarmKeyAndMatch:
    def test_company_key_and_match(self):
        a = {"type": "company", "exchange": "tse", "ticker": "ath"}
        assert notify.alarm_key(a) == "company:TSE:ATH"
        assert notify._matches(a, rec("ATH", "X")) is True
        assert notify._matches(a, rec("FNV", "X")) is False

    def test_person_key_and_match_normalizes(self):
        a = {"type": "person", "name": "Eric  Sprott"}
        assert notify.alarm_key(a) == "person:eric sprott"
        assert notify._matches(a, rec("ATH", "eric sprott")) is True
        assert notify._matches(a, rec("ATH", "Someone Else")) is False


class TestAddRemoveAlarm:
    def test_add_company_baselines_existing(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [rec("ATH", "Insider A", d="2026-06-01")])
        p = tmp_path / "notify.json"
        added, _ = notify.add_alarm(
            p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data
        )
        assert added is True
        alarm = notify.load_config(p)["alarms"][0]
        assert len(alarm["seen"]) == 1  # existing txn baselined, won't re-fire

    def test_dedup(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        p = tmp_path / "notify.json"
        spec = {"type": "company", "exchange": "TSE", "ticker": "ATH"}
        assert notify.add_alarm(p, spec, data)[0] is True
        added, msg = notify.add_alarm(p, spec, data)
        assert added is False
        assert "already" in msg.lower()

    def test_missing_fields(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        p = tmp_path / "notify.json"
        assert notify.add_alarm(p, {"type": "company", "exchange": "TSE"}, data)[0] is False
        assert notify.add_alarm(p, {"type": "person", "name": ""}, data)[0] is False

    def test_remove(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        p = tmp_path / "notify.json"
        notify.add_alarm(p, {"type": "person", "name": "Jane"}, data)
        aid = notify.load_config(p)["alarms"][0]["id"]
        assert notify.remove_alarm(p, aid)[0] is True
        assert notify.load_config(p)["alarms"] == []
        assert notify.remove_alarm(p, "nope")[0] is False


class TestEvaluate:
    def _enable_ntfy(self, p):
        cfg = notify.load_config(p)
        cfg["ntfy"] = {"enabled": True, "server": "https://ntfy.sh", "topic": "t"}
        notify.save_config(p, cfg)

    def test_fires_only_on_new_transactions(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, title, msg: sent.append((title, msg)))

        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [rec("ATH", "Insider A", d="2026-06-01")])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        # nothing new yet
        assert notify.evaluate_and_notify(p, data)["sent"] == 0
        assert sent == []

        # a NEW transaction arrives in a later snapshot
        write_snapshot(
            data,
            "2026-07-01",
            [rec("ATH", "Insider A", d="2026-06-01"), rec("ATH", "Insider B", d="2026-06-28")],
        )
        res = notify.evaluate_and_notify(p, data)
        assert res["sent"] == 1
        assert len(sent) == 1
        assert "Insider B" in sent[0][1]

        # re-evaluating does not re-fire (seen advanced)
        sent.clear()
        assert notify.evaluate_and_notify(p, data)["sent"] == 0
        assert sent == []

    def test_person_alarm_across_companies(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, title, msg: sent.append(msg))
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        notify.add_alarm(p, {"type": "person", "name": "Eric Sprott"}, data)
        write_snapshot(
            data, "2026-07-01", [rec("HYMC", "Eric Sprott", d="2026-06-20", exch="NASDAQ")]
        )
        assert notify.evaluate_and_notify(p, data)["sent"] == 1
        assert "Eric Sprott" in sent[0]

    def test_failed_send_keeps_baseline_for_retry(self, tmp_path, monkeypatch):
        def boom(cfg, title, msg):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(notify, "send_ntfy", boom)
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)
        write_snapshot(data, "2026-07-01", [rec("ATH", "Insider B", d="2026-06-28")])
        res = notify.evaluate_and_notify(p, data)
        assert res["sent"] == 0
        assert res["errors"]  # error recorded
        # baseline NOT advanced -> would retry
        alarm = notify.load_config(p)["alarms"][0]
        assert notify._key(rec("ATH", "Insider B", d="2026-06-28")) not in alarm["seen"]


class TestDescribeAndConfig:
    def test_describe_sentence(self):
        s = notify._describe(rec("HYMC", "Eric Sprott", "Buy", 24000, 200000.0, "2026-06-16"))
        assert "Eric Sprott bought 24,000 shares of" in s
        assert "(TSE:HYMC)" in s
        assert "2026-06-16" in s

    def test_public_config_masks_password(self, tmp_path):
        p = tmp_path / "notify.json"
        cfg = notify.load_config(p)
        cfg["email"]["password"] = "supersecret"
        notify.save_config(p, cfg)
        pub = notify.public_config(p)
        assert pub["email"]["password"] == notify._MASK
        assert "supersecret" not in json.dumps(pub)

    def test_save_settings_keeps_password_on_mask(self, tmp_path):
        p = tmp_path / "notify.json"
        cfg = notify.load_config(p)
        cfg["email"]["password"] = "keepme"
        notify.save_config(p, cfg)
        # UI sends back the mask (user didn't change the password)
        notify.save_settings(p, {"email": {"password": notify._MASK, "to": "me@x.com"}})
        after = notify.load_config(p)["email"]
        assert after["password"] == "keepme"
        assert after["to"] == "me@x.com"

    def test_send_test_no_channel(self, tmp_path):
        p = tmp_path / "notify.json"
        ok, _ = notify.send_test(p)
        assert ok is False
