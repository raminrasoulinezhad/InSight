# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Output writing + target loading (insight.scrape)."""

import json

from insight.models import InsiderTransaction
from insight.scrape import load_targets, write_outputs


def _txn(ticker="ABC"):
    return InsiderTransaction(
        issuer_name=ticker + " Inc",
        exchange="TSE",
        ticker=ticker,
        insider_name="Jane Doe",
        insider_role="Director",
        transaction_date="2026-05-01",
        transaction_type="Buy",
        shares=100,
        avg_price=10.0,
        total_value=1000.0,
        currency="CAD",
    )


class TestWriteOutputs:
    def test_writes_the_dated_json_snapshot(self, tmp_path):
        results = {"TSE:ABC": [_txn("ABC")], "TSE:EMPTY": []}
        write_outputs(results, tmp_path, "2026-07-01")

        combined = json.loads((tmp_path / "insider_2026-07-01.json").read_text())
        assert len(combined) == 1
        assert combined[0]["ticker"] == "ABC"
        assert combined[0]["insider_name"] == "Jane Doe"
        assert combined[0]["total_value"] == 1000.0

    def test_writes_nothing_but_the_snapshot(self, tmp_path):
        # The CSV and by_ticker/ exports were removed: nothing read them back,
        # and every scrape restated the same rows, so they grew ~5 MB and ~130
        # files per run as pure dead weight.
        write_outputs({"TSE:ABC": [_txn("ABC")], "TSE:EMPTY": []}, tmp_path, "2026-07-01")
        assert [p.name for p in tmp_path.iterdir()] == ["insider_2026-07-01.json"]

    def test_a_tagged_source_gets_its_own_snapshot(self, tmp_path):
        write_outputs({"TSE:ABC": [_txn("ABC")]}, tmp_path, "2026-07-01", source="sedi")
        assert (tmp_path / "insider_sedi_2026-07-01.json").exists()
        assert not (tmp_path / "insider_2026-07-01.json").exists()

    def test_a_scrape_with_no_data_still_writes_a_snapshot(self, tmp_path):
        write_outputs({"TSE:EMPTY": []}, tmp_path, "2026-07-01")
        assert json.loads((tmp_path / "insider_2026-07-01.json").read_text()) == []


class TestLoadTargets:
    def test_cli_tickers_with_and_without_exchange(self, tmp_path):
        cfg = tmp_path / "companies.json"
        cfg.write_text(json.dumps({"companies": []}))
        targets = load_targets(cfg, ["TSE:FNV", "SHOP"])
        assert targets[0] == {"name": "FNV", "exchange": "TSE", "ticker": "FNV"}
        assert targets[1] == {"name": "SHOP", "exchange": "TSE", "ticker": "SHOP"}  # default exch

    def test_reads_config_and_skips_underscore(self, tmp_path):
        cfg = tmp_path / "companies.json"
        cfg.write_text(
            json.dumps(
                {
                    "companies": [
                        {"name": "Barrick", "exchange": "TSE", "ticker": "ABX"},
                        {"name": "_note", "exchange": "TSE", "ticker": "ZZZ"},
                    ]
                }
            )
        )
        targets = load_targets(cfg, [])
        names = [t["name"] for t in targets]
        assert "Barrick" in names
        assert "_note" not in names
