# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Housekeeping that runs itself after a scrape.

A cleanup command nobody discovers is the same as no cleanup, so folding and
pruning happen automatically. The property that matters is that automatic
deletion never costs a record.
"""

from __future__ import annotations

import json
from pathlib import Path

from insight import store
from insight.aggregate import _txn_key, load_all_records
from insight.scrape import _autoprune_snapshots


def rec(ticker="ABC", name="Jane Doe", d="2026-06-01", shares=100):
    return {
        "exchange": "TSE",
        "ticker": ticker,
        "issuer_name": f"{ticker} Corp",
        "insider_name": name,
        "insider_role": "CEO",
        "transaction_type": "Buy",
        "transaction_date": d,
        "shares": shares,
        "total_value": 1000.0,
    }


def snap(d: Path, date: str, records):
    (d / f"insider_{date}.json").write_text(json.dumps(records), encoding="utf-8")


class TestAutoPrune:
    def test_keeps_the_newest_n_and_folds_the_rest(self, tmp_path: Path):
        for i in range(1, 7):
            snap(tmp_path, f"2026-06-0{i}", [rec(d=f"2026-06-0{i}")])
        _autoprune_snapshots(tmp_path, keep=3)

        left = sorted(p.name for p in tmp_path.glob("insider_*.json"))
        assert left == [
            "insider_2026-06-04.json",
            "insider_2026-06-05.json",
            "insider_2026-06-06.json",
        ]
        assert store.store_path(tmp_path).exists()

    def test_no_record_is_lost(self, tmp_path: Path):
        for i in range(1, 7):
            snap(tmp_path, f"2026-06-0{i}", [rec(d=f"2026-06-0{i}", shares=100 + i)])
        before = sorted(map(_txn_key, load_all_records(tmp_path)))

        _autoprune_snapshots(tmp_path, keep=1)

        from insight import aggregate

        with aggregate._CACHE_LOCK:  # simulate a restart
            aggregate._records_cache.clear()
            aggregate._view_cache.clear()
        assert sorted(map(_txn_key, load_all_records(tmp_path))) == before

    def test_the_history_count_is_preserved(self, tmp_path: Path):
        for i in range(1, 6):
            snap(tmp_path, f"2026-06-0{i}", [rec(d=f"2026-06-0{i}")])
        _autoprune_snapshots(tmp_path, keep=2)
        assert len(store.folded_names(tmp_path)) == 5

    def test_a_large_keep_removes_nothing(self, tmp_path: Path):
        for i in range(1, 4):
            snap(tmp_path, f"2026-06-0{i}", [rec(d=f"2026-06-0{i}")])
        _autoprune_snapshots(tmp_path, keep=9999)
        assert len(list(tmp_path.glob("insider_*.json"))) == 3

    def test_it_still_folds_when_nothing_is_pruned(self, tmp_path: Path):
        # The fold is the other half of the job: it leaves the store warm so the
        # app's next cold start is already fast.
        snap(tmp_path, "2026-06-01", [rec()])
        _autoprune_snapshots(tmp_path, keep=9999)
        assert store.store_path(tmp_path).exists()
        assert len(store.folded_names(tmp_path)) == 1

    def test_an_empty_data_dir_is_harmless(self, tmp_path: Path):
        _autoprune_snapshots(tmp_path, keep=3)
        assert not store.store_path(tmp_path).exists()

    def test_a_failure_is_reported_not_raised(self, tmp_path: Path, monkeypatch, capsys):
        # A scrape that collected data must not be reported as failed because
        # housekeeping tripped.
        snap(tmp_path, "2026-06-01", [rec()])
        monkeypatch.setattr(
            store, "prune_folded", lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone"))
        )
        _autoprune_snapshots(tmp_path, keep=3)  # must not raise
        assert "skipped" in capsys.readouterr().err.lower()
