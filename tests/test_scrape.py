# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Output writing + target loading (insight.scrape)."""

import csv
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
    def test_writes_json_csv_and_per_ticker(self, tmp_path):
        results = {"TSE:ABC": [_txn("ABC")], "TSE:EMPTY": []}
        write_outputs(results, tmp_path, "2026-07-01")

        combined = json.loads((tmp_path / "insider_2026-07-01.json").read_text())
        assert len(combined) == 1
        assert combined[0]["ticker"] == "ABC"

        assert (tmp_path / "insider_2026-07-01.csv").exists()

        per = tmp_path / "by_ticker" / "TSE_ABC_2026-07-01.csv"
        assert per.exists()
        rows = list(csv.DictReader(per.open()))
        assert rows[0]["insider_name"] == "Jane Doe"
        assert rows[0]["total_value"] == "1000.0"

        # empty company writes no per-ticker file
        assert not (tmp_path / "by_ticker" / "TSE_EMPTY_2026-07-01.csv").exists()


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
