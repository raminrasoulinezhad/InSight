# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Parsing + classification (insight.models)."""

from insight.models import InsiderTransaction, parse_int, parse_money, parse_us_date


class TestParseMoney:
    def test_canadian_with_symbol_and_commas(self):
        assert parse_money("C$1,700,250.00") == (1700250.0, "CAD")

    def test_us_dollar(self):
        assert parse_money("$5.20") == (5.2, "USD")

    def test_currency_word_cad(self):
        val, cur = parse_money("CAD 1,234.50")
        assert (val, cur) == (1234.5, "CAD")

    def test_no_symbol_no_currency(self):
        assert parse_money("1,234") == (1234.0, "")

    def test_empty_and_dashes(self):
        assert parse_money("") == (None, "")
        assert parse_money("-") == (None, "")
        assert parse_money("   ") == (None, "")

    def test_junk_returns_none_value(self):
        assert parse_money("n/a") == (None, "")


class TestParseInt:
    def test_commas(self):
        assert parse_int("1,234,567") == 1234567

    def test_trailing_text(self):
        assert parse_int("1,000 shares") == 1000

    def test_empty_and_dash(self):
        assert parse_int("") is None
        assert parse_int("-") is None

    def test_non_numeric(self):
        assert parse_int("abc") is None

    def test_negative(self):
        assert parse_int("-500") == -500


class TestParseUsDate:
    def test_basic(self):
        assert parse_us_date("3/24/2026") == "2026-03-24"

    def test_single_digit_month_day(self):
        assert parse_us_date("12/1/2025") == "2025-12-01"

    def test_embedded(self):
        assert parse_us_date("Filed 3/5/2026 (late)") == "2026-03-05"

    def test_invalid_calendar_date(self):
        assert parse_us_date("13/40/2026") is None

    def test_no_date(self):
        assert parse_us_date("") is None
        assert parse_us_date("no date here") is None


def _txn(insider, issuer="Barrick Gold"):
    return InsiderTransaction(
        issuer_name=issuer, exchange="TSE", ticker="ABX", insider_name=insider
    )


class TestClassify:
    def test_individual(self):
        t = _txn("John A. Smith").classify()
        assert t.entity_type == "individual"
        assert t.is_issuer_buyback is False

    def test_institution_tokens(self):
        assert _txn("Sprott Asset Management").classify().entity_type == "institution"
        assert _txn("BlackRock Inc").classify().entity_type == "institution"
        assert _txn("Some Capital Partners LP").classify().entity_type == "institution"

    def test_buyback_containment_both_directions(self):
        # issuer name contained in insider name
        t = InsiderTransaction(
            issuer_name="Athabasca Oil",
            exchange="TSE",
            ticker="ATH",
            insider_name="Athabasca Oil Corporation",
        ).classify()
        assert t.is_issuer_buyback is True
        assert t.entity_type == "institution"

    def test_individual_not_matching_issuer(self):
        t = _txn("Jane Doe", issuer="Franco-Nevada").classify()
        assert t.is_issuer_buyback is False
        assert t.entity_type == "individual"
