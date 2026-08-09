# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Per-company notes: keying, round-trip, clearing, and corrupt-file tolerance."""

from __future__ import annotations

from pathlib import Path

from insight import notes


def test_note_key_normalizes_case_and_whitespace():
    assert notes.note_key(" tse ", "ath ") == "TSE:ATH"


def test_missing_file_reads_as_empty(tmp_path: Path):
    assert notes.load_notes(tmp_path / "nope.json") == {}


def test_save_and_load_round_trip(tmp_path: Path):
    p = tmp_path / "notes.json"
    saved, _ = notes.save_note(p, "TSE", "ATH", "• watching the Q3 filing\n• CEO bought twice")
    assert saved
    assert notes.load_notes(p) == {"TSE:ATH": "• watching the Q3 filing\n• CEO bought twice"}


def test_saving_keeps_other_companies(tmp_path: Path):
    p = tmp_path / "notes.json"
    notes.save_note(p, "TSE", "ATH", "• one")
    notes.save_note(p, "TSX", "NFG", "• two")
    assert set(notes.load_notes(p)) == {"TSE:ATH", "TSX:NFG"}


def test_blank_note_removes_the_entry(tmp_path: Path):
    p = tmp_path / "notes.json"
    notes.save_note(p, "TSE", "ATH", "• something")
    saved, msg = notes.save_note(p, "TSE", "ATH", "   \n  ")
    assert saved and "clear" in msg.lower()
    assert notes.load_notes(p) == {}


def test_note_requires_a_company(tmp_path: Path):
    saved, msg = notes.save_note(tmp_path / "notes.json", "", "", "• orphan")
    assert not saved and "company" in msg.lower()


def test_overlong_note_is_rejected_not_truncated(tmp_path: Path):
    p = tmp_path / "notes.json"
    saved, msg = notes.save_note(p, "TSE", "ATH", "x" * (notes.MAX_NOTE_CHARS + 1))
    assert not saved and "too long" in msg.lower()
    assert notes.load_notes(p) == {}


def test_corrupt_file_reads_as_empty_instead_of_raising(tmp_path: Path):
    p = tmp_path / "notes.json"
    p.write_text("{not json", encoding="utf-8")
    assert notes.load_notes(p) == {}


def test_non_string_and_blank_values_are_ignored(tmp_path: Path):
    p = tmp_path / "notes.json"
    p.write_text('{"TSE:ATH": 42, "TSE:XYZ": "   ", "TSE:OK": "• fine"}', encoding="utf-8")
    assert notes.load_notes(p) == {"TSE:OK": "• fine"}


def test_save_leaves_no_temp_file_behind(tmp_path: Path):
    p = tmp_path / "notes.json"
    notes.save_note(p, "TSE", "ATH", "• one")
    assert [f.name for f in tmp_path.iterdir()] == ["notes.json"]
