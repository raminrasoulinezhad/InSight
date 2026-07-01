# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Watchlist add/remove + helpers (insight.issuers)."""

import json

from insight.issuers import (
    add_to_watchlist,
    candidate_key,
    in_watchlist,
    map_exchange,
    remove_from_watchlist,
)


def make_config(tmp_path, companies=None):
    p = tmp_path / "companies.json"
    p.write_text(json.dumps({"companies": companies or []}))
    return p


def test_candidate_key_normalizes_case():
    assert candidate_key("tse", "abx") == "TSE:ABX"


def test_map_exchange_known_and_passthrough():
    assert map_exchange("TSX") == "TSE"
    assert map_exchange("nasdaq") == "NASDAQ"
    assert map_exchange("WEIRD") == "WEIRD"


def test_in_watchlist():
    cfg = {"companies": [{"exchange": "TSE", "ticker": "ABX"}]}
    assert in_watchlist(cfg, "tse", "abx") is True
    assert in_watchlist(cfg, "TSE", "FNV") is False


class TestAddToWatchlist:
    def test_adds_candidate(self, tmp_path):
        cfg = make_config(tmp_path)
        added, _msg = add_to_watchlist(
            cfg,
            {"legal_name": "New Found Gold", "exchange": "TSXV", "ticker": "NFG", "country": "CA"},
        )
        assert added is True
        data = json.loads(cfg.read_text())
        entry = data["companies"][0]
        assert entry["name"] == "New Found Gold"
        assert entry["exchange"] == "TSXV"
        assert entry["ticker"] == "NFG"
        assert entry["confirmed"] is True

    def test_rejects_duplicate(self, tmp_path):
        cfg = make_config(tmp_path, [{"name": "X", "exchange": "TSE", "ticker": "ABX"}])
        added, msg = add_to_watchlist(
            cfg, {"legal_name": "Barrick", "exchange": "TSE", "ticker": "abx"}
        )
        assert added is False
        assert "already" in msg.lower()

    def test_rejects_missing_fields(self, tmp_path):
        cfg = make_config(tmp_path)
        added, _ = add_to_watchlist(cfg, {"legal_name": "No Ticker", "exchange": "TSE"})
        assert added is False

    def test_uppercases_exchange_ticker(self, tmp_path):
        cfg = make_config(tmp_path)
        add_to_watchlist(cfg, {"name": "Lower Co", "exchange": "tse", "ticker": "low"})
        entry = json.loads(cfg.read_text())["companies"][0]
        assert entry["exchange"] == "TSE"
        assert entry["ticker"] == "LOW"


class TestRemoveFromWatchlist:
    def test_removes_existing(self, tmp_path):
        cfg = make_config(
            tmp_path,
            [
                {"name": "Barrick", "exchange": "TSE", "ticker": "ABX"},
                {"name": "Franco", "exchange": "TSE", "ticker": "FNV"},
            ],
        )
        removed, _msg = remove_from_watchlist(cfg, "tse", "abx")
        assert removed is True
        remaining = [c["ticker"] for c in json.loads(cfg.read_text())["companies"]]
        assert remaining == ["FNV"]

    def test_not_found(self, tmp_path):
        cfg = make_config(tmp_path, [{"name": "Barrick", "exchange": "TSE", "ticker": "ABX"}])
        removed, msg = remove_from_watchlist(cfg, "TSE", "NOPE")
        assert removed is False
        assert "not on the watchlist" in msg.lower()

    def test_missing_fields(self, tmp_path):
        cfg = make_config(tmp_path, [])
        removed, _ = remove_from_watchlist(cfg, "", "")
        assert removed is False
