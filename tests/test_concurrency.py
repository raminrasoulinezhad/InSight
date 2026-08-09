# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Concurrent writes — the app serves on a thread pool, so requests overlap.

Both on-disk writers (notes, store) are read-modify-write against a single JSON
file. These tests drive them from many threads at once and assert nothing is
lost, corrupted, or left behind as a stray temp file.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from insight import notes, store


def key_fn(r):
    return (r.get("ticker"), r.get("transaction_date"), r.get("shares"))


class TestConcurrentNoteSaves:
    def test_no_note_is_lost_when_saves_overlap(self, tmp_path: Path):
        path = tmp_path / "notes.json"
        tickers = [f"T{i:03d}" for i in range(60)]

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(lambda t: notes.save_note(path, "TSE", t, f"• note for {t}"), tickers))

        saved = notes.load_notes(path)
        assert len(saved) == len(tickers), "a concurrent save overwrote someone else's note"
        for t in tickers:
            assert saved[f"TSE:{t}"] == f"• note for {t}"

    def test_the_file_stays_valid_json_under_concurrent_writes(self, tmp_path: Path):
        path = tmp_path / "notes.json"
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(lambda i: notes.save_note(path, "TSE", f"T{i}", "• x" * 200), range(40)))
        json.loads(path.read_text(encoding="utf-8"))  # raises if interleaved

    def test_interleaved_saves_and_deletes_settle_consistently(self, tmp_path: Path):
        path = tmp_path / "notes.json"
        notes.save_note(path, "TSE", "KEEP", "• keep me")

        def churn(i):
            notes.save_note(path, "TSE", f"TMP{i}", "• temp")
            notes.save_note(path, "TSE", f"TMP{i}", "")  # clear it again

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(churn, range(30)))

        saved = notes.load_notes(path)
        assert saved == {"TSE:KEEP": "• keep me"}

    def test_no_temp_files_are_left_behind(self, tmp_path: Path):
        path = tmp_path / "notes.json"
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(lambda i: notes.save_note(path, "TSE", f"T{i}", "• x"), range(40)))
        assert [p.name for p in tmp_path.iterdir()] == ["notes.json"]


class TestConcurrentStoreSync:
    def _snapshots(self, d: Path, n: int) -> None:
        for i in range(n):
            recs = [
                {"ticker": "ABC", "transaction_date": f"2026-06-{i + 1:02d}", "shares": 100 + j}
                for j in range(20)
            ]
            (d / f"insider_2026-06-{i + 1:02d}.json").write_text(json.dumps(recs), encoding="utf-8")

    def test_concurrent_syncs_agree_and_leave_a_valid_store(self, tmp_path: Path):
        self._snapshots(tmp_path, 8)

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: store.sync(tmp_path, key_fn), range(12)))

        counts = {len(recs) for recs, _ in results}
        assert len(counts) == 1, f"threads disagreed on the record set: {counts}"

        raw = json.loads(store.store_path(tmp_path).read_text(encoding="utf-8"))
        assert raw["version"] == store.STORE_VERSION
        assert len(raw["records"]) == counts.pop()
        assert len(raw["folded"]) == 8

    def test_no_temp_files_survive_concurrent_syncs(self, tmp_path: Path):
        self._snapshots(tmp_path, 5)
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _: store.sync(tmp_path, key_fn), range(12)))
        assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]

    def test_a_snapshot_landing_mid_read_is_folded_next_time(self, tmp_path: Path):
        self._snapshots(tmp_path, 3)
        first, _ = store.sync(tmp_path, key_fn)
        self._snapshots(tmp_path, 4)  # a fourth scrape lands
        second, manifest = store.sync(tmp_path, key_fn)
        assert len(second) > len(first)
        assert len(manifest) == 4
