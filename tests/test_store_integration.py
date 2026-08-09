# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""End-to-end checks that consolidating storage changed nothing users can see.

The store sits underneath every view, and the one thing it must never do is
alter what the app reports. These tests drive the real entry points
(`load_all_records`, `load_view`, `load_insiders_view`) across a scrape, a
restart, and a prune, asserting the output is identical each time.
"""

from __future__ import annotations

import json
from pathlib import Path

from insight import aggregate, store
from insight.aggregate import (
    _txn_key,
    load_all_records,
    load_insiders_view,
    load_view,
)


def rec(ticker="ABC", name="Jane Doe", ttype="Buy", shares=100, value=1000.0, date="2026-06-01"):
    return {
        "exchange": "TSE",
        "ticker": ticker,
        "issuer_name": f"{ticker} Corp",
        "insider_name": name,
        "insider_role": "CEO",
        "transaction_type": ttype,
        "transaction_date": date,
        "shares": shares,
        "total_value": value,
        "currency": "CAD",
        "entity_type": "individual",
    }


def snap(d: Path, date: str, records, source=""):
    tag = f"{source}_" if source else ""
    (d / f"insider_{tag}{date}.json").write_text(json.dumps(records), encoding="utf-8")


def config(d: Path) -> Path:
    cfg = d / "companies.json"
    cfg.write_text(
        json.dumps({"companies": [{"name": "ABC Corp", "exchange": "TSE", "ticker": "ABC"}]}),
        encoding="utf-8",
    )
    return cfg


def clear_caches():
    """Drop the in-memory caches so the next call re-reads from disk (a restart)."""
    with aggregate._CACHE_LOCK:
        aggregate._records_cache.clear()
        aggregate._view_cache.clear()


class TestStoreIsTransparent:
    def test_records_survive_a_simulated_restart(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec(), rec(name="Bob")])
        first = sorted(map(_txn_key, load_all_records(tmp_path)))
        clear_caches()
        assert sorted(map(_txn_key, load_all_records(tmp_path))) == first

    def test_a_new_scrape_is_picked_up(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        assert len(load_all_records(tmp_path)) == 1
        snap(tmp_path, "2026-06-02", [rec(name="Bob")])  # a scrape lands
        assert len(load_all_records(tmp_path)) == 2, "the cache must invalidate on a new snapshot"

    def test_views_are_identical_before_and_after_a_prune(self, tmp_path: Path):
        cfg = config(tmp_path)
        for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
            snap(tmp_path, d, [rec(date=d), rec(name="Bob", date=d)])

        before_co = load_view(tmp_path, cfg, days=3650)
        before_insiders = load_insiders_view(tmp_path, cfg, days=3650)

        removed, _ = store.prune_folded(tmp_path, _txn_key, keep=1)
        assert removed, "expected the older snapshots to be pruned"
        clear_caches()

        after_co = load_view(tmp_path, cfg, days=3650)
        after_insiders = load_insiders_view(tmp_path, cfg, days=3650)

        assert after_co["total_transactions"] == before_co["total_transactions"]
        assert after_co["companies"] == before_co["companies"]
        assert after_insiders["insiders"] == before_insiders["insiders"]

    def test_history_count_and_date_survive_a_prune(self, tmp_path: Path):
        cfg = config(tmp_path)
        for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
            snap(tmp_path, d, [rec(date=d)])

        before = load_view(tmp_path, cfg, days=3650)
        assert before["history_files"] == 3

        store.prune_folded(tmp_path, _txn_key, keep=1)
        clear_caches()
        after = load_view(tmp_path, cfg, days=3650)

        assert after["history_files"] == 3, "pruning must not make the history look shallower"
        assert after["data_date"] == before["data_date"]
        assert after["data_file"] == before["data_file"]

    def test_view_is_correct_with_every_snapshot_pruned(self, tmp_path: Path):
        cfg = config(tmp_path)
        snap(tmp_path, "2026-06-01", [rec()])
        before = load_view(tmp_path, cfg, days=3650)

        store.prune_folded(tmp_path, _txn_key, keep=0)
        assert list(tmp_path.glob("insider_*.json")) == []
        clear_caches()

        after = load_view(tmp_path, cfg, days=3650)
        assert after["total_transactions"] == before["total_transactions"]
        assert after["history_files"] == 1

    def test_deleting_the_store_rebuilds_it_from_snapshots(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        snap(tmp_path, "2026-06-02", [rec(name="Bob")])
        before = sorted(map(_txn_key, load_all_records(tmp_path)))

        store.store_path(tmp_path).unlink()
        clear_caches()

        assert sorted(map(_txn_key, load_all_records(tmp_path))) == before

    def test_a_scrape_after_a_prune_still_merges(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        store.prune_folded(tmp_path, _txn_key, keep=0)
        clear_caches()
        snap(tmp_path, "2026-06-02", [rec(name="Bob")])
        records = load_all_records(tmp_path)
        assert len(records) == 2, "pruned history must still combine with fresh scrapes"

    def test_the_store_is_not_mistaken_for_a_snapshot(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        load_all_records(tmp_path)
        assert store.store_path(tmp_path).exists()
        names = [p.name for p in store.snapshot_paths(tmp_path)]
        assert store.STORE_NAME not in names
        clear_caches()
        assert len(load_all_records(tmp_path)) == 1, "the store must not fold into itself"

    def test_empty_data_dir_yields_an_empty_view(self, tmp_path: Path):
        cfg = config(tmp_path)
        view = load_view(tmp_path, cfg, days=14)
        assert view["total_transactions"] == 0
        assert view["history_files"] == 0
        assert view["data_file"] is None
        assert view["data_date"] is None
