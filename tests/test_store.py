# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""The consolidated record store: folding, incrementality, and safe pruning."""

from __future__ import annotations

import json
from pathlib import Path

from insight import store


# Same shape aggregate uses: identity is the whole trade, so only genuine
# re-statements of one filing collapse together.
def key_fn(r):
    return (r.get("ticker"), r.get("insider_name"), r.get("transaction_date"), r.get("shares"))


def rec(ticker="ATH", name="A Person", d="2026-06-01", shares=100, **extra):
    return {
        "ticker": ticker,
        "insider_name": name,
        "transaction_date": d,
        "shares": shares,
        **extra,
    }


def snap(data_dir: Path, date: str, records: list[dict], source: str = "") -> Path:
    tag = f"{source}_" if source else ""
    p = data_dir / f"insider_{tag}{date}.json"
    p.write_text(json.dumps(records), encoding="utf-8")
    return p


class TestOrdering:
    """Source-tagged names (`insider_sedi_…`) must not sort ahead of plain ones."""

    def test_ordered_by_embedded_date_not_filename(self, tmp_path: Path):
        snap(tmp_path, "2026-08-03", [rec()], source="sedi")
        snap(tmp_path, "2026-08-08", [rec(name="B")])
        assert [p.name for p in store.snapshot_paths(tmp_path)] == [
            "insider_sedi_2026-08-03.json",
            "insider_2026-08-08.json",
        ]

    def test_newest_by_date_wins_a_collision(self, tmp_path: Path):
        # the SEDI file is older but sorts last by filename
        snap(tmp_path, "2026-08-08", [rec(source="marketbeat-newer")])
        snap(tmp_path, "2026-08-03", [rec(source="sedi-older")], source="sedi")
        records, _ = store.sync(tmp_path, key_fn)
        assert [r["source"] for r in records] == ["marketbeat-newer"]

    def test_prune_keeps_the_newest_by_date(self, tmp_path: Path):
        snap(tmp_path, "2026-08-01", [rec(d="2026-08-01")])
        snap(tmp_path, "2026-08-03", [rec(d="2026-08-03")], source="sedi")
        snap(tmp_path, "2026-08-08", [rec(d="2026-08-08")])
        removed, _ = store.prune_folded(tmp_path, key_fn, keep=1)
        assert removed == ["insider_2026-08-01.json", "insider_sedi_2026-08-03.json"]
        assert [p.name for p in store.snapshot_paths(tmp_path)] == ["insider_2026-08-08.json"]

    def test_undated_name_sorts_first_without_raising(self, tmp_path: Path):
        (tmp_path / "insider_backup.json").write_text(json.dumps([rec()]), encoding="utf-8")
        snap(tmp_path, "2026-08-08", [rec(name="B")])
        assert [p.name for p in store.snapshot_paths(tmp_path)] == [
            "insider_backup.json",
            "insider_2026-08-08.json",
        ]


class TestSync:
    def test_folds_snapshots_and_dedups(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec(), rec(name="B")])
        snap(tmp_path, "2026-06-02", [rec(), rec(name="C")])  # repeats the first
        records, manifest = store.sync(tmp_path, key_fn)
        assert len(records) == 3
        assert set(manifest) == {"insider_2026-06-01.json", "insider_2026-06-02.json"}

    def test_newer_snapshot_wins_a_collision(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec(source="old")])
        snap(tmp_path, "2026-06-02", [rec(source="new")])
        records, _ = store.sync(tmp_path, key_fn)
        assert [r["source"] for r in records] == ["new"]

    def test_creates_the_store_file(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        store.sync(tmp_path, key_fn)
        assert store.store_path(tmp_path).exists()

    def test_second_sync_reads_no_snapshots(self, tmp_path: Path, monkeypatch):
        snap(tmp_path, "2026-06-01", [rec()])
        store.sync(tmp_path, key_fn)

        # Any snapshot re-read after the first sync would be a regression: the
        # whole point is that steady-state startup touches only the store.
        opened: list[str] = []
        real = Path.read_text

        def spy(self, *a, **kw):
            opened.append(self.name)
            return real(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", spy)
        records, _ = store.sync(tmp_path, key_fn)
        assert len(records) == 1
        assert opened == [store.STORE_NAME]

    def test_only_the_new_snapshot_is_parsed(self, tmp_path: Path, monkeypatch):
        snap(tmp_path, "2026-06-01", [rec()])
        store.sync(tmp_path, key_fn)
        snap(tmp_path, "2026-06-02", [rec(name="B")])

        opened: list[str] = []
        real = Path.read_text

        def spy(self, *a, **kw):
            opened.append(self.name)
            return real(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", spy)
        records, _ = store.sync(tmp_path, key_fn)
        assert len(records) == 2
        assert opened == [store.STORE_NAME, "insider_2026-06-02.json"]

    def test_rewritten_snapshot_is_refolded(self, tmp_path: Path):
        p = snap(tmp_path, "2026-06-01", [rec(source="first")])
        store.sync(tmp_path, key_fn)
        p.write_text(json.dumps([rec(source="corrected")]), encoding="utf-8")
        records, _ = store.sync(tmp_path, key_fn)
        assert [r["source"] for r in records] == ["corrected"]

    def test_empty_dir_yields_nothing_and_writes_nothing(self, tmp_path: Path):
        records, manifest = store.sync(tmp_path, key_fn)
        assert records == [] and manifest == {}
        assert not store.store_path(tmp_path).exists()

    def test_corrupt_snapshot_is_skipped(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        (tmp_path / "insider_2026-06-02.json").write_text("{not json", encoding="utf-8")
        records, manifest = store.sync(tmp_path, key_fn)
        assert len(records) == 1
        assert "insider_2026-06-02.json" not in manifest

    def test_corrupt_store_rebuilds_from_snapshots(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        store.sync(tmp_path, key_fn)
        store.store_path(tmp_path).write_text("garbage", encoding="utf-8")
        records, _ = store.sync(tmp_path, key_fn)
        assert len(records) == 1

    def test_store_from_a_future_version_is_ignored(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        store.store_path(tmp_path).write_text(
            json.dumps({"version": store.STORE_VERSION + 1, "folded": {}, "records": [{"x": 1}]}),
            encoding="utf-8",
        )
        records, _ = store.sync(tmp_path, key_fn)
        assert records == [rec()]

    def test_leaves_no_temp_file_behind(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        store.sync(tmp_path, key_fn)
        assert not any(f.name.endswith(".tmp") for f in tmp_path.iterdir())


class TestFoldedNames:
    def test_reports_every_folded_snapshot(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        snap(tmp_path, "2026-06-02", [rec(name="B")])
        store.sync(tmp_path, key_fn)
        assert store.folded_names(tmp_path) == [
            "insider_2026-06-01.json",
            "insider_2026-06-02.json",
        ]

    def test_empty_without_a_store(self, tmp_path: Path):
        assert store.folded_names(tmp_path) == []


class TestPrune:
    def test_keeps_the_newest_snapshots(self, tmp_path: Path):
        for d in ("2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"):
            snap(tmp_path, d, [rec(d=d)])
        removed, freed = store.prune_folded(tmp_path, key_fn, keep=2)
        assert removed == ["insider_2026-06-01.json", "insider_2026-06-02.json"]
        assert freed > 0
        left = sorted(p.name for p in store.snapshot_paths(tmp_path))
        assert left == ["insider_2026-06-03.json", "insider_2026-06-04.json"]

    def test_pruning_does_not_lose_records(self, tmp_path: Path):
        for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
            snap(tmp_path, d, [rec(d=d)])
        before, _ = store.sync(tmp_path, key_fn)
        store.prune_folded(tmp_path, key_fn, keep=1)
        after, _ = store.sync(tmp_path, key_fn)
        assert sorted(map(key_fn, after)) == sorted(map(key_fn, before))

    def test_history_count_survives_pruning(self, tmp_path: Path):
        for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
            snap(tmp_path, d, [rec(d=d)])
        store.prune_folded(tmp_path, key_fn, keep=1)
        assert len(store.folded_names(tmp_path)) == 3

    def test_never_prunes_an_unfolded_snapshot(self, tmp_path: Path):
        snap(tmp_path, "2026-06-01", [rec()])
        (tmp_path / "insider_2026-06-02.json").write_text("{not json", encoding="utf-8")
        snap(tmp_path, "2026-06-03", [rec(name="C")])
        removed, _ = store.prune_folded(tmp_path, key_fn, keep=0)
        assert "insider_2026-06-02.json" not in removed
        assert (tmp_path / "insider_2026-06-02.json").exists()

    def test_no_store_means_no_deletion(self, tmp_path: Path):
        removed, freed = store.prune_folded(tmp_path, key_fn, keep=0)
        assert removed == [] and freed == 0
