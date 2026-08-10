# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Searching SEDI by person instead of by company.

Every other path in this app is company-first: you can only see an insider
because a company they traded in was scraped. That makes a person trading
somewhere off the watchlist invisible — there is no signal to act on. SEDI's
"Insider family name" search is the one place that signal exists, so these cover
the two pure halves of it: reading a report whose issuer changes mid-grid, and
turning the legal names it returns back into tickers.

No browser and no network — the grid rows are the same lists of strings the page
JS hands back, and the resolver is a stub.
"""

from __future__ import annotations

import pytest

from insight import sedi
from insight.sedi import _parse_report_rows, resolve_tickers

URL = "https://www.sedi.ca/sedi/SVTItdController?locale=en_CA"
NOW = "2026-08-10T00:00:00+00:00"


def txn_row(txn_id: str, date_str: str, nature: str, signed: str, price: str = "") -> list[str]:
    """A transaction row, shaped like the live grid: the transaction id anchors
    it, then date, two ignored columns, nature, signed share change, price.

    The id is zero-padded to 7 digits because that anchor is what tells a
    transaction row from a header — a short id parses as no row at all.
    """
    return [txn_id.zfill(7), date_str, "Common Shares", "", nature, signed, price]


def header(label: str, value: str) -> list[str]:
    return [f"{label}:", value]


# One person's report, switching issuer partway through — the shape an
# insider search returns and the reason the parser tracks issuer at all.
ROWS = [
    header("Insider Name", "Sprott, Eric"),
    header("Issuer Name", "West Red Lake Gold Mines Ltd."),
    header("Insider's Relationship to Issuer", "3 - 10% Security Holder of Issuer"),
    txn_row("1000001", "2026-06-01", "10 - Acquisition in the public market", "5,000", "$1.20"),
    header("Issuer Name", "Tiny Venture Explorations Inc."),
    txn_row("1000002", "2026-06-05", "11 - Disposition in the public market", "-2,000", "$0.40"),
    txn_row("1000003", "2026-06-06", "10 - Acquisition in the public market", "800", "$0.41"),
]


class TestAnIssuerReportIsUnchanged:
    """The company-first path must behave exactly as before."""

    def test_the_passed_in_company_stamps_every_row(self):
        rows = [
            header("Insider Name", "Doe, Jane"),
            header("Insider's Relationship to Issuer", "4 - Director of Issuer"),
            txn_row(
                "1234567", "2026-06-01", "10 - Acquisition in the public market", "1,000", "$5"
            ),
        ]
        recs = _parse_report_rows(rows, "Athabasca Oil", "TSE", "ATH", URL, NOW)
        assert len(recs) == 1
        assert (recs[0].exchange, recs[0].ticker) == ("TSE", "ATH")
        assert recs[0].issuer_name == "Athabasca Oil"
        assert recs[0].insider_name == "Jane Doe"
        assert recs[0].insider_role == "Director of Issuer"

    def test_an_issuer_header_naming_the_same_company_keeps_the_ticker(self):
        # SEDI writes the full legal name where the watchlist holds a short one;
        # that must not be read as "a different company".
        rows = [
            header("Issuer Name", "Athabasca Oil Corporation"),
            header("Insider Name", "Doe, Jane"),
            txn_row("1234567", "2026-06-01", "10 - Acquisition in the public market", "1,000"),
        ]
        recs = _parse_report_rows(rows, "Athabasca Oil", "TSE", "ATH", URL, NOW)
        assert (recs[0].exchange, recs[0].ticker) == ("TSE", "ATH")


class TestAnInsiderReportSpansCompanies:
    def test_each_group_carries_its_own_issuer(self):
        recs = _parse_report_rows(ROWS, "", "", "", URL, NOW)
        assert [r.issuer_name for r in recs] == [
            "West Red Lake Gold Mines Ltd.",
            "Tiny Venture Explorations Inc.",
            "Tiny Venture Explorations Inc.",
        ]

    def test_the_insider_carries_across_the_issuer_change(self):
        # The person is named once, at the top; only the issuer changes.
        recs = _parse_report_rows(ROWS, "", "", "", URL, NOW)
        assert {r.insider_name for r in recs} == {"Eric Sprott"}

    def test_no_ticker_is_invented(self):
        # SEDI reports names, not tickers. A guess here would silently file one
        # company's trades under another.
        recs = _parse_report_rows(ROWS, "", "", "", URL, NOW)
        assert all(r.ticker == "" and r.exchange == "" for r in recs)

    def test_buys_and_sells_still_read_correctly(self):
        recs = _parse_report_rows(ROWS, "", "", "", URL, NOW)
        assert [r.transaction_type for r in recs] == ["Buy", "Sell", "Buy"]
        assert [r.shares for r in recs] == [5000, 2000, 800]

    def test_a_stale_ticker_is_dropped_when_the_issuer_changes(self):
        # The dangerous case: a fallback company is supplied AND the grid moves
        # on. Rows after the switch must lose the ticker rather than keep it.
        recs = _parse_report_rows(ROWS, "West Red Lake Gold", "TSXV", "WRLG", URL, NOW)
        first, rest = recs[0], recs[1:]
        assert (first.exchange, first.ticker) == ("TSXV", "WRLG")
        assert all(r.ticker == "" for r in rest), "company B kept company A's ticker"


class TestResolveTickers:
    @staticmethod
    def resolver(mapping: dict):
        return lambda name: mapping.get(name, [])

    def test_it_fills_in_the_exchange_and_ticker(self):
        recs = _parse_report_rows(
            [
                header("Issuer Name", "West Red Lake Gold Mines Ltd."),
                header("Insider Name", "Sprott, Eric"),
                txn_row("1", "2026-06-01", "10 - Acquisition in the public market", "5,000"),
            ],
            "",
            "",
            "",
            URL,
            NOW,
        )
        out, unresolved = resolve_tickers(
            recs,
            self.resolver(
                {
                    "West Red Lake Gold Mines Ltd.": [
                        {"ticker": "wrlg", "exchange": "tsxv", "country": "CA"}
                    ]
                }
            ),
        )
        assert (out[0].exchange, out[0].ticker) == ("TSXV", "WRLG")
        assert unresolved == []

    def test_an_unresolvable_name_is_reported_not_dropped(self):
        # These are exactly the obscure venture names this feature exists to
        # surface. Discarding them would defeat the point.
        recs = _parse_report_rows(
            [
                header("Issuer Name", "Tiny Venture Explorations Inc."),
                header("Insider Name", "Sprott, Eric"),
                txn_row("1", "2026-06-01", "10 - Acquisition in the public market", "100"),
            ],
            "",
            "",
            "",
            URL,
            NOW,
        )
        out, unresolved = resolve_tickers(recs, self.resolver({}))
        assert len(out) == 1, "the record must survive"
        assert out[0].issuer_name == "Tiny Venture Explorations Inc."
        assert unresolved == ["Tiny Venture Explorations Inc."]

    def test_a_us_listing_of_the_same_name_is_refused(self):
        # SEDI is Canadian. A same-named US issuer is a different company.
        recs = _parse_report_rows(
            [
                header("Issuer Name", "American Eagle"),
                header("Insider Name", "Doe, Jane"),
                txn_row("1", "2026-06-01", "10 - Acquisition in the public market", "100"),
            ],
            "",
            "",
            "",
            URL,
            NOW,
        )
        out, unresolved = resolve_tickers(
            recs,
            self.resolver(
                {"American Eagle": [{"ticker": "AEO", "exchange": "NYSE", "country": "US"}]}
            ),
        )
        assert out[0].ticker == ""
        assert unresolved == ["American Eagle"]

    def test_each_name_is_looked_up_once(self):
        # A person with 40 filings in one company must not mean 40 lookups.
        rows = [header("Issuer Name", "Repeat Corp"), header("Insider Name", "Doe, Jane")]
        for i in range(5):
            rows.append(txn_row(str(1000 + i), "2026-06-01", "10 - Acquisition", "100"))
        recs = _parse_report_rows(rows, "", "", "", URL, NOW)
        calls: list[str] = []

        def counting(name):
            calls.append(name)
            return [{"ticker": "RPT", "exchange": "TSE", "country": "CA"}]

        resolve_tickers(recs, counting)
        assert calls == ["Repeat Corp"]

    def test_a_resolver_that_raises_is_survivable(self):
        recs = _parse_report_rows(
            [
                header("Issuer Name", "Boom Corp"),
                header("Insider Name", "Doe, Jane"),
                txn_row("1", "2026-06-01", "10 - Acquisition in the public market", "100"),
            ],
            "",
            "",
            "",
            URL,
            NOW,
        )

        def boom(name):
            raise RuntimeError("network down")

        out, unresolved = resolve_tickers(recs, boom)
        assert len(out) == 1 and unresolved == ["Boom Corp"]

    def test_records_that_already_have_a_ticker_are_left_alone(self):
        recs = _parse_report_rows(
            [
                header("Insider Name", "Doe, Jane"),
                txn_row("1", "2026-06-01", "10 - Acquisition in the public market", "100"),
            ],
            "Athabasca Oil",
            "TSE",
            "ATH",
            URL,
            NOW,
        )
        called: list[str] = []
        resolve_tickers(recs, lambda n: called.append(n) or [])
        assert called == [], "an already-keyed record needs no lookup"


class TestTheSearchTypeConstants:
    def test_they_match_the_live_form(self):
        # Read off a captured SEDI ITD page: 5 = Insider family name,
        # 8 = Issuer name. Getting these backwards would search the wrong field
        # and return an empty report with no error.
        assert sedi._SELECT_TYPE_INSIDER == "5"
        assert sedi._SELECT_TYPE_ISSUER == "8"


class TestSameIssuer:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Athabasca Oil Corporation", "Athabasca Oil"),
            ("Athabasca Oil", "Athabasca Oil Corporation"),
            ("ATHABASCA OIL CORP.", "Athabasca Oil"),
        ],
    )
    def test_a_longer_legal_name_still_matches(self, a, b):
        assert sedi._same_issuer(a, b) is True

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("West Red Lake Gold", "Athabasca Oil"),
            ("", "Athabasca Oil"),
            ("Athabasca Oil", ""),
        ],
    )
    def test_different_or_missing_names_do_not_match(self, a, b):
        assert sedi._same_issuer(a, b) is False
