# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Pure-logic tests for the SEDI parser. No network / browser — the stateful
report-row walk (insider group headers + transaction rows) is exercised with
rows captured from a live SEDI Insider Transaction Detail report."""

from __future__ import annotations

from insight import paths, sedi


class TestDateParsing:
    def test_iso_passthrough(self):
        assert sedi._parse_sedi_date("2026-06-16") == "2026-06-16"

    def test_embedded_iso(self):
        assert sedi._parse_sedi_date("filed 2026-06-16 (late)") == "2026-06-16"

    def test_alt_formats(self):
        assert sedi._parse_sedi_date("16-Jun-2026") == "2026-06-16"
        assert sedi._parse_sedi_date("Jun 16, 2026") == "2026-06-16"

    def test_junk_is_none(self):
        assert sedi._parse_sedi_date("") is None
        assert sedi._parse_sedi_date("Total") is None


class TestSearchQuery:
    def test_strips_trailing_corporate_suffix(self):
        assert sedi._search_query("Athabasca Oil Corporation") == "Athabasca Oil"
        assert sedi._search_query("West Red Lake Gold Mines Ltd.") == "West Red Lake Gold Mines"

    def test_strips_stacked_suffixes(self):
        assert sedi._search_query("Foo Holdings Co Ltd.") == "Foo Holdings"

    def test_keeps_plain_name_and_never_empties(self):
        assert sedi._search_query("New Found Gold") == "New Found Gold"
        # a bare suffix must not reduce to empty
        assert sedi._search_query("Inc") == "Inc"


class TestTransactionType:
    def test_market_buy_and_sell(self):
        nat = "10 - Acquisition or disposition in the public market"
        assert sedi._transaction_type(nat, 1000, None) == "Buy"
        assert sedi._transaction_type(nat, None, 1000) == "Sell"

    def test_option_natures_labelled(self):
        assert (
            sedi._transaction_type("51 - Exercise of options", 5000, None) == "Exercise of options"
        )
        assert sedi._transaction_type("50 - Grant of options", None, None) == "Grant of options"

    def test_uses_sedi_wording_stripped_of_code(self):
        # non-market natures keep SEDI's own description, minus the numeric code
        assert sedi._transaction_type("90 - Change in nature", None, None) == "Change in nature"
        assert sedi._transaction_type("57 - Exercise of rights", 100, None) == "Exercise of rights"


class TestIsCanadian:
    def test_country_takes_precedence(self):
        assert sedi.is_canadian({"country": "CA", "exchange": "NASDAQ"}) is True
        assert sedi.is_canadian({"country": "US", "exchange": "TSE"}) is False

    def test_infers_from_exchange_when_no_country(self):
        assert sedi.is_canadian({"exchange": "TSXV"}) is True
        assert sedi.is_canadian({"exchange": "NYSE"}) is False


class TestIsBotWall:
    def test_403_forbidden_title_is_wall(self):
        # Radware's hard block: "403 Forbidden" title + hex "Transaction ID" body.
        assert sedi._is_bot_wall("403 Forbidden", "https://www.sedi.ca/x", "") is True

    def test_radware_block_body_is_wall(self):
        body = "Access to this page has been denied because you are using automation tools."
        assert sedi._is_bot_wall("", "https://www.sedi.ca/x", body) is True

    def test_perfdrive_url_is_wall(self):
        assert sedi._is_bot_wall("Loading", "https://sedi.ca.perfdrive.com/?ssa=1", "") is True

    def test_normal_report_is_not_wall(self):
        # A real ITD report mentions "Transaction" columns but is not the wall.
        body = "Insider Transaction Detail  Transaction date  Nature of transaction"
        assert sedi._is_bot_wall("SEDI - Insider Transactions", "https://sedi.ca", body) is False


class TestNameAndRelationship:
    def test_flip_last_first(self):
        assert sedi._flip_name("Billan, Jason") == "Jason Billan"
        assert sedi._flip_name("Sprott, Eric S.") == "Eric S. Sprott"

    def test_flip_leaves_plain_names(self):
        assert sedi._flip_name("Eric Sprott") == "Eric Sprott"
        assert sedi._flip_name("") == ""

    def test_clean_relationship(self):
        assert (
            sedi._clean_relationship("5 - Senior Officer of Issuer") == "Senior Officer of Issuer"
        )
        assert sedi._clean_relationship("4 - Director of Issuer") == "Director of Issuer"


# Real transaction rows captured from a live SEDI ITD report (21 cells: leading
# padding, then ID, txn date, filing date, ownership, nature, signed amount, …).
def _txn_row(txn_id, tdate, nature, amount, price="", *, expiry=""):
    return [
        "",
        "",
        "",
        "",
        txn_id,
        tdate,
        "2099-01-01",
        "Direct Ownership :",
        nature,
        amount,
        price,
        "",
        "999,999",
        "",
        price,
        "",
        expiry,
        "Common Shares",
        amount,
        "999,999",
        "",
    ]


class TestReportRowToRecord:
    def test_market_buy_positive_amount(self):
        row = _txn_row(
            "4659760",
            "2026-02-06",
            "10 - Acquisition or disposition in the public market",
            "+12,500",
            "1.1400",
        )
        rec = sedi._report_row_to_record(
            row, "Sprott, Eric", "10% Security Holder", "WRLG Ltd.", "TSXV", "WRLG", "u", "now"
        )
        assert rec is not None
        assert rec.insider_name == "Eric Sprott"
        assert rec.insider_role == "10% Security Holder"
        assert rec.transaction_type == "Buy"
        assert rec.transaction_date == "2026-02-06"
        assert rec.shares == 12500
        assert rec.avg_price == 1.14
        assert rec.total_value == 12500 * 1.14
        assert rec.currency == "CAD"
        assert rec.source == "sedi"

    def test_market_sell_negative_amount(self):
        row = _txn_row(
            "4659750",
            "2026-02-06",
            "10 - Acquisition or disposition in the public market",
            "-125,000",
            "1.1400",
        )
        rec = sedi._report_row_to_record(row, "Billan, Jason", "", "I", "TSXV", "WRLG", "u", "now")
        assert rec.transaction_type == "Sell"
        assert rec.shares == 125000

    def test_option_grant_labelled(self):
        row = _txn_row(
            "4303297",
            "2024-04-11",
            "50 - Grant of options",
            "+150,000",
            "0.9000",
            expiry="2029-04-11",
        )
        rec = sedi._report_row_to_record(row, "Billan, Jason", "", "I", "TSXV", "WRLG", "u", "now")
        assert rec.transaction_type == "Grant of options"
        assert rec.shares == 150000

    def test_opening_balance_skipped(self):
        # nature 00 with no amount is a baseline, not a trade
        row = _txn_row("4336969", "2024-06-24", "00 - Opening Balance-Initial SEDI Report", "")
        assert sedi._report_row_to_record(row, "X", "", "I", "TSXV", "WRLG", "u", "now") is None

    def test_non_transaction_row_returns_none(self):
        assert (
            sedi._report_row_to_record(["", "some text", ""], "X", "", "I", "T", "T", "u", "n")
            is None
        )


class TestParseReportRows:
    def test_carries_insider_across_group(self):
        rows = [
            ["Insider name:", "Billan, Jason", ""],
            ["Insider's Relationship to Issuer:", "5 - Senior Officer of Issuer", ""],
            ["Security designation:", "Common Shares", ""],
            _txn_row("4659749", "2026-02-06", "51 - Exercise of options", "+125,000", "0.5600"),
            _txn_row("4659750", "2026-02-06", "10 - public market", "-125,000", "1.1400"),
            ["Insider name:", "Sprott, Eric", ""],
            _txn_row("4700000", "2026-03-01", "10 - public market", "+40,000", "1.20"),
        ]
        recs = sedi._parse_report_rows(rows, "WRLG Ltd.", "TSXV", "WRLG", "u", "now")
        assert len(recs) == 3
        assert recs[0].insider_name == "Jason Billan"
        assert recs[0].insider_role == "Senior Officer of Issuer"
        assert recs[0].transaction_type == "Exercise of options"
        assert recs[1].insider_name == "Jason Billan"
        assert recs[1].transaction_type == "Sell"
        # a new group header switches the current insider
        assert recs[2].insider_name == "Eric Sprott"


class TestSediPageFilename:
    def test_upper_and_extension(self):
        assert paths.sedi_page_filename("tse", "abc") == "TSE_ABC.html"

    def test_preserves_dot_and_dash(self):
        # tickers like "ABC.A" / "ABC-B" stay legible in the filename
        assert paths.sedi_page_filename("TSXV", "ABC.A") == "TSXV_ABC.A.html"
        assert paths.sedi_page_filename("CSE", "ABC-B") == "CSE_ABC-B.html"

    def test_sanitizes_separators_and_traversal(self):
        # anything that could escape the pages dir is replaced with '_'
        fn = paths.sedi_page_filename("../etc", "a/b\\c")
        assert "/" not in fn and "\\" not in fn
        assert fn == ".._ETC_A_B_C.html"
