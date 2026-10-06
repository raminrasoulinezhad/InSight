# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Alarms + notification logic (insight.notify), senders monkeypatched."""

import json
from datetime import date, timedelta

from insight import notify


def days_ago(n: int) -> str:
    """Alerting is bounded to a 90-day horizon (ALERT_HORIZON_DAYS), so a test
    trade must be dated relative to today. Fixed dates here were a time bomb:
    they aged out of the window and the whole evaluation suite went quiet."""
    return (date.today() - timedelta(days=n)).isoformat()


# A month back: inside the alerting horizon, far enough from its edge that a
# test dating a trade relative to this one stays inside it too.
_DEFAULT_TRADE_DATE = days_ago(30)


def rec(ticker, name, ttype="Buy", shares=100, value=1000.0, d=_DEFAULT_TRADE_DATE, exch="TSE"):
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
        write_snapshot(data, "2026-06-30", [rec("ATH", "Insider A", d=days_ago(60))])
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
        monkeypatch.setattr(
            notify, "send_ntfy", lambda cfg, title, msg, links=None: sent.append((title, msg))
        )

        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [rec("ATH", "Insider A", d=days_ago(60))])
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
            [rec("ATH", "Insider A", d=days_ago(60)), rec("ATH", "Insider B", d=days_ago(12))],
        )
        res = notify.evaluate_and_notify(p, data)
        assert res["sent"] == 1
        assert len(sent) == 1
        assert "Insider B" in sent[0][1]

        # re-evaluating does not re-fire (seen advanced)
        sent.clear()
        assert notify.evaluate_and_notify(p, data)["sent"] == 0
        assert sent == []

    def test_grants_and_exercises_do_not_alert(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(
            notify, "send_ntfy", lambda cfg, title, msg, links=None: sent.append(msg)
        )
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        # only compensation events arrive -> no alert, but baseline advances
        write_snapshot(
            data,
            "2026-07-01",
            [
                rec("ATH", "Insider A", ttype="Grant of options", d=days_ago(20)),
                rec("ATH", "Insider A", ttype="Exercise of rights", d=days_ago(19)),
            ],
        )
        assert notify.evaluate_and_notify(p, data)["sent"] == 0
        assert sent == []

        # a real Buy arrives alongside another grant -> only the Buy is reported
        write_snapshot(
            data,
            "2026-07-02",
            [
                rec("ATH", "Insider A", ttype="Grant of options", d=days_ago(20)),
                rec("ATH", "Insider A", ttype="Exercise of rights", d=days_ago(19)),
                rec("ATH", "Insider B", ttype="Grant of rights", d=days_ago(11)),
                rec("ATH", "Insider C", ttype="Buy", d=days_ago(10)),
            ],
        )
        res = notify.evaluate_and_notify(p, data)
        assert res["sent"] == 1
        assert len(sent) == 1
        assert "Insider C" in sent[0]
        assert "Insider B" not in sent[0]  # the grant is not mentioned

    def test_person_alarm_across_companies(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(
            notify, "send_ntfy", lambda cfg, title, msg, links=None: sent.append(msg)
        )
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        notify.add_alarm(p, {"type": "person", "name": "Eric Sprott"}, data)
        write_snapshot(
            data, "2026-07-01", [rec("HYMC", "Eric Sprott", d=days_ago(20), exch="NASDAQ")]
        )
        assert notify.evaluate_and_notify(p, data)["sent"] == 1
        assert "Eric Sprott" in sent[0]

    def test_failed_send_keeps_baseline_for_retry(self, tmp_path, monkeypatch):
        def boom(cfg, title, msg, links=None):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(notify, "send_ntfy", boom)
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)
        write_snapshot(data, "2026-07-01", [rec("ATH", "Insider B", d=days_ago(12))])
        res = notify.evaluate_and_notify(p, data)
        assert res["sent"] == 0
        assert res["errors"]  # error recorded
        # baseline NOT advanced -> would retry
        alarm = notify.load_config(p)["alarms"][0]
        assert notify._key(rec("ATH", "Insider B", d=days_ago(12))) not in alarm["seen"]


class TestTitleAndLog:
    def _enable_ntfy(self, p):
        cfg = notify.load_config(p)
        cfg["ntfy"] = {"enabled": True, "server": "https://ntfy.sh", "topic": "t"}
        notify.save_config(p, cfg)

    def _read_log(self, p):
        log = notify._log_file(p)
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    def test_title_names_target_and_index_is_in_body(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(
            notify, "send_ntfy", lambda cfg, title, msg, links=None: sent.append((title, msg))
        )
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        # UI passes the company name as the label
        notify.add_alarm(
            p,
            {"type": "company", "exchange": "TSE", "ticker": "ATH", "label": "Athabasca Oil"},
            data,
        )
        # two new trades arrive at once -> still names the company, not "2 trades"
        write_snapshot(
            data,
            "2026-07-01",
            [rec("ATH", "Insider A", d=days_ago(13)), rec("ATH", "Insider B", d=days_ago(12))],
        )
        assert notify.evaluate_and_notify(p, data)["sent"] == 1
        title, body = sent[0]
        # title names the company: no index, no count
        assert title == "InSight: Athabasca Oil"
        assert "#" not in title and "trades" not in title
        # the reference index lives in the body instead
        assert "#1" in body

    def test_index_increments_and_persists_in_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, title, msg, links=None: None)
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)

        write_snapshot(data, "2026-07-01", [rec("ATH", "Insider A", d=days_ago(13))])
        notify.evaluate_and_notify(p, data)
        write_snapshot(
            data,
            "2026-07-02",
            [rec("ATH", "Insider A", d=days_ago(13)), rec("ATH", "Insider B", d=days_ago(12))],
        )
        notify.evaluate_and_notify(p, data)

        log = self._read_log(p)
        assert [e["index"] for e in log] == [1, 2]  # monotonic, one per notification
        assert log[0]["delivered"] is True
        assert log[0]["label"] and log[0]["channels"][0]["channel"] == "ntfy"
        assert "Insider B" in " ".join(log[1]["lines"])

    def test_failed_delivery_is_logged(self, tmp_path, monkeypatch):
        def boom(cfg, title, msg, links=None):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(notify, "send_ntfy", boom)
        data = tmp_path / "data"
        data.mkdir()
        write_snapshot(data, "2026-06-30", [])
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        notify.add_alarm(p, {"type": "company", "exchange": "TSE", "ticker": "ATH"}, data)
        write_snapshot(data, "2026-07-01", [rec("ATH", "Insider B", d=days_ago(12))])
        notify.evaluate_and_notify(p, data)

        log = self._read_log(p)
        assert len(log) == 1
        assert log[0]["delivered"] is False
        assert log[0]["channels"][0]["ok"] is False
        assert "smtp down" in log[0]["channels"][0]["error"]

    def test_test_notification_is_indexed_and_logged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(notify, "send_ntfy", lambda cfg, title, msg, links=None: None)
        p = tmp_path / "notify.json"
        self._enable_ntfy(p)
        ok, msg = notify.send_test(p)
        assert ok is True
        assert "#1" in msg
        log = self._read_log(p)
        assert log[0]["index"] == 1 and log[0]["kind"] == "test"


class TestDescribeAndConfig:
    def test_describe_sentence(self):
        # Brief, to the point: who + action + shares + issuer + per-share price + date.
        # The verb is upper-cased: this text goes to phone push and the log, and
        # capitals are the only emphasis plain text has.
        s = notify._describe(rec("HYMC", "Eric Sprott", "Buy", 24000, 200000.0, "2026-06-16"))
        assert s == "Eric Sprott BOUGHT 24,000 shares of HYMC Inc (~8.33 CAD each) on 2026-06-16"
        # the terse format drops the exchange:ticker but always keeps the buy/sell date
        assert "TSE:HYMC" not in s
        assert "2026-06-16" in s

    def test_describe_without_date(self):
        # No transaction_date -> no trailing " on ..." clause.
        s = notify._describe(rec("HYMC", "Eric Sprott", "Buy", 24000, 200000.0, None))
        assert s == "Eric Sprott BOUGHT 24,000 shares of HYMC Inc (~8.33 CAD each)"
        assert " on " not in s


class TestTheVerbIsEmphasised:
    """Buy vs sell is what an alert is actually about, so it must be what the eye
    lands on — in whatever way each medium allows."""

    def test_the_email_bolds_the_verb_in_normal_case(self):
        # Real emphasis is available here, so shouting would be redundant.
        r = rec("HYMC", "Eric Sprott", "Buy", 24000, 200000.0, "2026-06-16")
        html = notify._describe_html(r)
        assert "<b>bought</b>" in html
        assert "BOUGHT" not in html

    def test_a_sell_is_emphasised_in_both_media(self):
        r = rec("HYMC", "Eric Sprott", "Sell", 5000, 40000.0, "2026-06-16")
        assert "<b>sold</b>" in notify._describe_html(r)
        assert "SOLD" in notify._describe(r)

    def test_an_unusual_transaction_type_still_gets_emphasised(self):
        # Anything that isn't a plain buy/sell falls back to the raw type; it
        # must not quietly lose its emphasis.
        r = rec("HYMC", "Eric Sprott", "Redemption", 100, 500.0, "2026-06-16")
        assert "<b>Redemption</b>" in notify._describe_html(r)

    def test_the_html_stays_escaped_around_the_bold(self):
        # Bolding must not become an injection hole: an issuer or person named
        # with a tag has to stay inert.
        r = rec("HYMC", "<script>alert(1)</script>", "Buy", 10, 100.0, "2026-06-16")
        html = notify._describe_html(r)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>bought</b>" in html, "escaping must not eat the emphasis"

    def test_the_email_body_carries_the_markup_through(self):
        html = notify._email_html("Eric Sprott", ["plain"], 7, ["Eric Sprott <b>bought</b> x"])
        assert "<b>bought</b>" in html

    def test_the_email_falls_back_to_escaping_when_given_no_markup(self):
        # The test alert has no records behind it, so it sends plain lines only.
        html = notify._email_html("Eric Sprott", ["a <tag> & more"], 7)
        assert "a &lt;tag&gt; &amp; more" in html

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


class TestTheCompanyNameIsALink:
    """An alert that names a company should be one tap from its price, on both
    channels — looking it up by hand is the friction that gets alerts ignored."""

    def test_the_url_is_a_stock_price_search_for_the_company(self):
        url = notify.company_url(rec("ATH", "Eric Sprott"))
        assert url.startswith("https://www.google.com/search?q=")
        assert "ATH+Inc+stock+price" in url

    def test_an_awkward_name_is_escaped_into_the_query(self):
        r = rec("ATH", "X")
        r["issuer_name"] = "Smith & Co. (Canada)"
        url = notify.company_url(r)
        assert " " not in url and "&amp;" not in url
        assert "%26" in url, "a bare & would start another query parameter"

    def test_the_ticker_stands_in_for_a_missing_name(self):
        r = rec("ATH", "X")
        r["issuer_name"] = ""
        assert "ATH+stock+price" in notify.company_url(r)

    def test_an_unidentified_company_gets_no_link(self):
        r = rec("ATH", "X")
        r["issuer_name"] = ""
        r["ticker"] = ""
        assert notify.company_url(r) == ""

    def test_the_email_links_the_company_name_only(self):
        html = notify._describe_html(rec("ATH", "Eric Sprott", "Buy", 100, 1000.0, "2026-06-16"))
        assert '<a href="https://www.google.com/search?q=ATH+Inc+stock+price"' in html
        assert ">ATH Inc</a>" in html
        # the rest of the sentence stays plain text, and the verb keeps its bold
        assert "<b>bought</b>" in html
        assert html.count("<a ") == 1

    def test_the_email_link_cannot_carry_markup(self):
        r = rec("ATH", "X")
        r["issuer_name"] = '"><script>alert(1)</script>'
        html = notify._describe_html(r)
        assert "<script>" not in html
        assert '"><script' not in html

    def test_a_nameless_company_is_not_wrapped_in_an_empty_link(self):
        r = rec("ATH", "Eric Sprott")
        r["issuer_name"] = ""
        r["ticker"] = ""
        assert "<a " not in notify._describe_html(r)

    def test_the_plain_sentence_is_unchanged_by_linking(self):
        # The push body and the log stay clean text; the link rides in headers.
        s = notify._describe(rec("HYMC", "Eric Sprott", "Buy", 24000, 200000.0, "2026-06-16"))
        assert s == "Eric Sprott BOUGHT 24,000 shares of HYMC Inc (~8.33 CAD each) on 2026-06-16"
        assert "http" not in s

    def test_one_link_per_company_in_first_seen_order(self):
        links = notify.company_links(
            [rec("ATH", "A"), rec("FNV", "A"), rec("ATH", "A", shares=200)]
        )
        assert [name for name, _ in links] == ["ATH Inc", "FNV Inc"]

    def test_the_push_opens_the_company_on_tap(self, monkeypatch):
        sent = {}

        def fake_urlopen(req, timeout=0):
            sent["headers"] = req.headers

            class R:
                def read(self):
                    return b""

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

        monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
        notify.send_ntfy(
            {"topic": "t"}, "InSight: ATH", "body", notify.company_links([rec("ATH", "A")])
        )
        assert sent["headers"]["Click"].endswith("ATH+Inc+stock+price")
        assert "Actions" not in sent["headers"], "one company needs no buttons"

    def test_several_companies_become_buttons(self):
        links = notify.company_links(
            [rec(t, "Eric Sprott") for t in ("ATH", "FNV", "HYMC", "WRLG")]
        )
        header = notify._ntfy_actions(links)
        assert header.count("view,") == 3, "ntfy accepts at most three"
        assert '"ATH Inc"' in header and '"FNV Inc"' in header
        assert "WRLG" not in header

    def test_a_button_label_cannot_break_the_header(self):
        r = rec("ATH", "X")
        r["issuer_name"] = 'Smith, Jones; "Co" Ünlimited'
        header = notify._ntfy_actions(notify.company_links([r]))
        # the label is quoted, so its commas and semicolons stay inside the quotes
        label = header.split('"')[1]
        assert '"' not in label
        assert header.count('"') == 2
        assert header.isascii(), "ntfy headers are ASCII"

    def test_a_person_alarm_pushes_every_company_they_traded(self, tmp_path, monkeypatch):
        got = {}
        monkeypatch.setattr(
            notify, "send_ntfy", lambda cfg, title, msg, links=None: got.update(links=links)
        )
        data = tmp_path / "data"
        data.mkdir()
        p = tmp_path / "notify.json"
        notify.add_alarm(p, {"type": "person", "name": "Eric Sprott"}, data)
        cfg = notify.load_config(p)
        cfg["ntfy"] = {"enabled": True, "server": "https://ntfy.sh", "topic": "t"}
        notify.save_config(p, cfg)
        write_snapshot(
            data,
            "new",
            [
                rec("ATH", "Eric Sprott", d=days_ago(2)),
                rec("FNV", "Eric Sprott", d=days_ago(1)),
            ],
        )
        notify.evaluate_and_notify(p, data)
        assert [name for name, _ in got["links"]] == ["ATH Inc", "FNV Inc"]
