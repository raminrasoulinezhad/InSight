# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Aggregation math + view building (insight.aggregate)."""

import json
from datetime import date

from insight.aggregate import (
    _avg_price,
    _delisted_keys,
    _load_records,
    _stamp,
    _txn_key,
    build_people_view,
    build_view,
    load_all_records,
    load_people_view,
    load_view,
    months_ago,
)


def rec(ticker, name, ttype, shares, value, d, role="Director", issuer=None, exch="TSE", **kw):
    r = {
        "issuer_name": issuer or (ticker + " Inc"),
        "exchange": exch,
        "ticker": ticker,
        "insider_name": name,
        "insider_role": role,
        "entity_type": kw.get("entity_type", "individual"),
        "transaction_date": d,
        "transaction_type": ttype,
        "shares": shares,
        "avg_price": (value / shares) if shares else None,
        "total_value": value,
        "currency": "CAD",
        "is_issuer_buyback": kw.get("is_issuer_buyback", False),
    }
    return r


class TestMonthsAgo:
    def test_two_years(self):
        assert months_ago(date(2026, 6, 30), 24) == date(2024, 6, 30)

    def test_day_clamped_to_february(self):
        assert months_ago(date(2026, 3, 31), 1) == date(2026, 2, 28)

    def test_year_rollover_backwards(self):
        assert months_ago(date(2026, 1, 15), 3) == date(2025, 10, 15)


class TestAvgPrice:
    def test_weighted(self):
        assert _avg_price(1000.0, 100) == 10.0

    def test_zero_shares_is_none(self):
        assert _avg_price(0.0, 0) is None
        assert _avg_price(50.0, 0) is None

    def test_rounding(self):
        assert _avg_price(100.0, 3) == 33.3333


class TestBuildView:
    def setup_method(self):
        self.watchlist = [
            {"name": "ABC Inc", "exchange": "TSE", "ticker": "ABC"},
            {"name": "Empty Co", "exchange": "TSE", "ticker": "XYZ"},  # no records -> empty card
        ]
        self.records = [
            rec("ABC", "Jane Doe", "Buy", 100, 1000.0, "2026-05-01"),
            rec("ABC", "Jane Doe", "Buy", 50, 600.0, "2026-05-10"),
            rec("ABC", "Jane Doe", "Sell", 30, 450.0, "2026-04-01"),
            rec("ABC", "Bob Roe", "Sell", 200, 2000.0, "2026-03-01", role="CEO"),
            rec("OTHER", "Off List", "Buy", 5, 50.0, "2026-05-01", exch="NYSE"),  # not on watchlist
        ]
        self.view = build_view(self.records, self.watchlist)

    def test_totals_and_coverage(self):
        v = self.view
        assert v["total_companies"] == 2
        assert v["covered_companies"] == 1  # only ABC has activity
        assert v["total_transactions"] == 4  # OTHER skipped

    def test_non_watchlist_record_skipped(self):
        keys = {c["key"] for c in self.view["companies"]}
        assert "NYSE:OTHER" not in keys

    def test_insider_box_math(self):
        abc = next(c for c in self.view["companies"] if c["ticker"] == "ABC")
        assert abc["insider_count"] == 2
        jane = next(b for b in abc["boxes"] if b["insider_name"] == "Jane Doe")
        assert jane["buy_count"] == 2
        assert jane["buy_shares"] == 150
        assert jane["buy_value"] == 1600.0
        assert jane["sell_count"] == 1
        assert jane["sell_shares"] == 30
        assert jane["sell_value"] == 450.0
        assert jane["net_value"] == 1150.0
        assert jane["latest_date"] == "2026-05-10"
        assert jane["avg_buy_price"] == round(1600 / 150, 4)
        assert jane["avg_sell_price"] == 15.0

    def test_avg_none_when_no_side(self):
        abc = next(c for c in self.view["companies"] if c["ticker"] == "ABC")
        bob = next(b for b in abc["boxes"] if b["insider_name"] == "Bob Roe")
        assert bob["avg_buy_price"] is None
        assert bob["avg_sell_price"] == 10.0

    def test_boxes_sorted_by_gross_activity(self):
        abc = next(c for c in self.view["companies"] if c["ticker"] == "ABC")
        assert abc["boxes"][0]["insider_name"] == "Jane Doe"  # gross 2050 > Bob 2000

    def test_empty_company_still_present(self):
        xyz = next(c for c in self.view["companies"] if c["ticker"] == "XYZ")
        assert xyz["boxes"] == []
        assert xyz["txn_count"] == 0


class TestBuildPeopleView:
    def setup_method(self):
        self.records = [
            rec("ABC", "Jane Doe", "Buy", 100, 1000.0, "2026-05-01", role="Director"),
            rec(
                "XYZ", "jane   doe", "Sell", 40, 800.0, "2026-05-02", role="Officer"
            ),  # same person
            rec("ABC", "Acme Fund", "Buy", 10, 100.0, "2026-05-01", entity_type="institution"),
            rec(
                "ABC",
                "ABC Inc",
                "Buy",
                999,
                9990.0,
                "2026-05-03",
                is_issuer_buyback=True,
            ),  # excluded
        ]
        self.view = build_people_view(self.records)

    def test_buyback_excluded_and_totals(self):
        names = {p["insider_name"] for p in self.view["people"]}
        assert "ABC Inc" not in names
        assert self.view["total_people"] == 2  # Jane + Acme Fund
        assert self.view["total_companies"] == 2  # ABC + XYZ

    def test_person_merges_across_companies_case_insensitively(self):
        jane = next(
            p for p in self.view["people"] if p["insider_name"].lower().strip() == "jane doe"
        )
        assert jane["company_count"] == 2
        assert set(jane["roles"]) == {"Director", "Officer"}
        assert jane["buy_shares"] == 100
        assert jane["sell_shares"] == 40
        assert jane["net_value"] == 200.0  # 1000 buy - 800 sell

    def test_per_company_avg_prices(self):
        jane = next(
            p for p in self.view["people"] if p["insider_name"].lower().strip() == "jane doe"
        )
        abc = next(c for c in jane["companies"] if c["ticker"] == "ABC")
        xyz = next(c for c in jane["companies"] if c["ticker"] == "XYZ")
        assert abc["avg_buy_price"] == 10.0
        assert xyz["avg_sell_price"] == 20.0


class TestTxnKeyAndMerge:
    def test_txn_key_stable_and_distinguishing(self):
        a = rec("ABC", "Jane Doe", "Buy", 100, 1000.0, "2026-05-01")
        b = dict(a)  # identical -> same key
        c = rec("ABC", "Jane Doe", "Buy", 101, 1000.0, "2026-05-01")  # diff shares
        assert _txn_key(a) == _txn_key(b)
        assert _txn_key(a) != _txn_key(c)

    def test_load_all_records_dedup_newest_wins(self, tmp_path):
        shared = rec("ABC", "Jane Doe", "Buy", 100, 1000.0, "2026-03-01")
        old_only = rec("ABC", "Jane Doe", "Buy", 10, 90.0, "2026-01-01")
        new_only = rec("ABC", "Jane Doe", "Sell", 5, 75.0, "2026-06-01")
        (tmp_path / "insider_2026-03-01.json").write_text(json.dumps([old_only, shared]))
        (tmp_path / "insider_2026-06-30.json").write_text(json.dumps([shared, new_only]))
        merged = load_all_records(tmp_path)
        assert len(merged) == 3  # shared collapsed
        dates = sorted(r["transaction_date"] for r in merged)
        assert dates == ["2026-01-01", "2026-03-01", "2026-06-01"]

    def test_corrupt_file_skipped(self, tmp_path):
        (tmp_path / "insider_2026-06-30.json").write_text("{ this is not json")
        (tmp_path / "insider_2026-06-29.json").write_text(
            json.dumps([rec("ABC", "Jane", "Buy", 1, 1.0, "2026-06-01")])
        )
        merged = load_all_records(tmp_path)
        assert len(merged) == 1


class TestDelistedAndLoadRecords:
    def _setup(self, tmp_path, delisted=None):
        cfg = tmp_path / "companies.json"
        cfg.write_text(
            json.dumps(
                {
                    "companies": [
                        {"name": "ABC", "exchange": "TSE", "ticker": "ABC"},
                        {"name": "Gone", "exchange": "TSE", "ticker": "IPL"},
                        {"name": "_ignored", "exchange": "TSE", "ticker": "ZZZ"},
                    ]
                }
            )
        )
        (tmp_path / "insider_2026-06-30.json").write_text(
            json.dumps(
                [
                    rec("ABC", "Jane", "Buy", 1, 10.0, "2026-06-01"),
                    rec("IPL", "Old Guy", "Buy", 1, 10.0, "2023-01-01"),
                    rec("ABC", "Jane", "Buy", 1, 10.0, "2020-01-01"),  # old, for months filter
                ]
            )
        )
        if delisted is not None:
            (tmp_path / "delisted.json").write_text(json.dumps(delisted))
        return cfg

    def test_delisted_keys_loading(self, tmp_path):
        cfg = self._setup(tmp_path, delisted=["tse:ipl"])
        assert _delisted_keys(cfg) == {"TSE:IPL"}

    def test_records_and_watchlist_filtered_by_delisted(self, tmp_path):
        cfg = self._setup(tmp_path, delisted=["TSE:IPL"])
        records, watchlist = _load_records(tmp_path, cfg, months=None)
        assert all(r["ticker"] != "IPL" for r in records)
        assert all(c["ticker"] != "IPL" for c in watchlist)

    def test_underscore_watchlist_entries_ignored(self, tmp_path):
        cfg = self._setup(tmp_path)
        _records, watchlist = _load_records(tmp_path, cfg, months=None)
        assert all(c["ticker"] != "ZZZ" for c in watchlist)

    def test_months_filter(self, tmp_path):
        cfg = self._setup(tmp_path)
        records, _ = _load_records(tmp_path, cfg, months=24)
        # the 2020 record is older than 24 months from any 2026 "today"
        assert all(r["transaction_date"] >= "2024" for r in records)

    def test_stamp_history_files(self, tmp_path):
        self._setup(tmp_path)
        (tmp_path / "insider_2026-05-01.json").write_text("[]")
        view = _stamp({}, tmp_path, months=12)
        assert view["history_files"] == 2
        assert view["data_date"] == "2026-06-30"  # newest
        assert view["range_months"] == 12

    def test_load_view_end_to_end_hides_delisted(self, tmp_path):
        cfg = self._setup(tmp_path, delisted=["TSE:IPL"])
        cv = load_view(tmp_path, cfg, months=None)
        pv = load_people_view(tmp_path, cfg, months=None)
        assert "TSE:IPL" not in {c["key"] for c in cv["companies"]}
        assert "IPL" not in {c["ticker"] for p in pv["people"] for c in p["companies"]}


class TestViewCache:
    def _setup(self, tmp_path, records):
        cfg = tmp_path / "companies.json"
        cfg.write_text(
            json.dumps({"companies": [{"name": "ABC", "exchange": "TSE", "ticker": "ABC"}]})
        )
        (tmp_path / "insider_2026-06-30.json").write_text(json.dumps(records))
        return cfg

    def test_repeat_access_is_memoized(self, tmp_path):
        cfg = self._setup(tmp_path, [rec("ABC", "Jane", "Buy", 1, 10.0, "2026-06-01")])
        v1 = load_view(tmp_path, cfg, months=None)
        v2 = load_view(tmp_path, cfg, months=None)
        assert v1 is v2  # served from cache, not rebuilt

    def test_cache_invalidated_when_snapshot_changes(self, tmp_path):
        cfg = self._setup(tmp_path, [rec("ABC", "Jane", "Buy", 1, 10.0, "2026-06-01")])
        v1 = load_view(tmp_path, cfg, months=None)
        assert v1["total_transactions"] == 1
        # add a second transaction -> file size changes -> signature changes
        (tmp_path / "insider_2026-06-30.json").write_text(
            json.dumps(
                [
                    rec("ABC", "Jane", "Buy", 1, 10.0, "2026-06-01"),
                    rec("ABC", "Bob", "Sell", 2, 20.0, "2026-06-02"),
                ]
            )
        )
        v2 = load_view(tmp_path, cfg, months=None)
        assert v2 is not v1
        assert v2["total_transactions"] == 2

    def test_cache_invalidated_when_delisted_changes(self, tmp_path):
        cfg = self._setup(tmp_path, [rec("ABC", "Jane", "Buy", 1, 10.0, "2026-06-01")])
        assert load_view(tmp_path, cfg, months=None)["total_transactions"] == 1
        (tmp_path / "delisted.json").write_text(json.dumps(["TSE:ABC"]))
        v = load_view(tmp_path, cfg, months=None)
        assert v["total_transactions"] == 0  # ABC now filtered out
