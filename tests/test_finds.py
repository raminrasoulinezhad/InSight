# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Following insiders: turning their SEDI searches into companies to add."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from insight import finds, notify, sedi
from insight.models import InsiderTransaction


def txn(issuer: str, ticker: str = "", d: str = "2026-10-01", who: str = "Eric Sprott"):
    return InsiderTransaction(
        issuer_name=issuer,
        exchange="TSXV" if ticker else "",
        ticker=ticker,
        insider_name=who,
        insider_role="10% holder",
        entity_type="individual",
        transaction_type="Buy",
        transaction_date=d,
        shares=1000,
        avg_price=1.0,
        total_value=1000.0,
        currency="CAD",
        source="sedi",
    )


class TestASearchIsSummarisedPerCompany:
    def test_one_row_per_company_most_active_first(self):
        r = finds.summarize(
            [txn("Alpha", "ALP"), txn("Beta", "BET"), txn("Beta", "BET", d="2026-10-09")]
        )
        assert [c["issuer_name"] for c in r["companies"]] == ["Beta", "Alpha"]
        assert r["companies"][0] == {
            "issuer_name": "Beta",
            "exchange": "TSXV",
            "ticker": "BET",
            "txn_count": 2,
            "latest_date": "2026-10-09",
            "resolved": True,
        }

    def test_a_company_without_a_ticker_is_kept_not_dropped(self):
        # Obscure venture names are what following a person is for.
        r = finds.summarize([txn("Tiny Gold Corp.")], ["Tiny Gold Corp."])
        assert r["companies"][0]["resolved"] is False
        assert r["unresolved"] == ["Tiny Gold Corp."]


class TestSuggestions:
    def test_only_companies_off_the_watchlist(self):
        saved = {
            "Eric Sprott": finds.summarize([txn("Alpha", "ALP"), txn("Beta", "BET")]),
        }
        rows = finds.suggestions(saved, lambda e, t: t == "ALP")
        assert [r["ticker"] for r in rows] == ["BET"]

    def test_a_company_two_insiders_trade_names_both(self):
        saved = {
            "Eric Sprott": finds.summarize([txn("Beta", "BET")]),
            "Frank Giustra": finds.summarize([txn("Beta", "BET", who="Frank Giustra")]),
        }
        (row,) = finds.suggestions(saved, lambda e, t: False)
        assert row["insiders"] == ["Eric Sprott", "Frank Giustra"]
        assert row["txn_count"] == 2

    def test_most_recent_first(self):
        saved = {
            "Eric Sprott": finds.summarize(
                [txn("Old", "OLD", d="2025-01-01"), txn("New", "NEW", d="2026-10-01")]
            )
        }
        assert [r["ticker"] for r in finds.suggestions(saved, lambda e, t: False)] == [
            "NEW",
            "OLD",
        ]

    def test_unresolved_names_cannot_be_added_so_are_not_suggested(self):
        saved = {"Eric Sprott": finds.summarize([txn("Tiny Gold Corp.")])}
        assert finds.suggestions(saved, lambda e, t: False) == []


class TestTheStore:
    def test_save_replaces_one_insider_and_keeps_the_rest(self, tmp_path: Path):
        p = tmp_path / "insider_finds.json"
        finds.save(p, "A", finds.summarize([txn("Alpha", "ALP")]))
        finds.save(p, "B", finds.summarize([txn("Beta", "BET")]))
        finds.save(p, "A", finds.summarize([]))
        got = finds.load(p)
        assert set(got) == {"A", "B"} and got["A"]["companies"] == []

    def test_a_corrupt_file_reads_as_empty(self, tmp_path: Path):
        p = tmp_path / "insider_finds.json"
        p.write_text("{not json")
        assert finds.load(p) == {}

    def test_age(self):
        an_hour_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        assert 1.9 < finds.age_hours({"searched_at": an_hour_ago}) < 2.1
        assert finds.age_hours({}) == float("inf")


class TestTheRightInsiderIsSearched:
    """The app shows "Eric Sprott"; SEDI searches family names and files
    "Sprott, Eric". The full name found nobody, so Find companies never worked."""

    def test_a_person_is_searched_by_family_name(self):
        assert sedi.insider_query("Eric Sprott") == ("5", "Sprott")
        assert sedi.insider_query("Robert Martin Friedland") == ("5", "Friedland")
        assert sedi.insider_query("William A.W. Lundin") == ("5", "Lundin")
        assert sedi.insider_query("Sprott, Eric") == ("5", "Sprott")
        assert sedi.insider_query("John Smith Jr.") == ("5", "Smith")

    def test_a_company_insider_is_searched_as_a_company(self):
        assert sedi.insider_query("Nemesia S.a.r.l.")[0] == "4"
        assert sedi.insider_query("Sprott Mining Inc.") == ("4", "Sprott Mining")


ROSE_ROWS = [
    "View AROSEN001 Rosen, Andrea Sarah Toronto Element Fleet Management Corp.",
    "View DROSE004 Rose, David William Vancouver Oroco Resource Corp.",
    "View MROSE002 Rose, Michael John Calgary Tourmaline Oil Corp.",
    "View MROSE007 Rose, Michael Toronto Some Other Corp.",
]


class TestThePickerNeverGuesses:
    def test_family_and_given_name_must_both_match(self):
        assert sedi.pick_insider_row(ROSE_ROWS, "David Rose") == 1

    def test_rosen_is_not_rose(self):
        assert sedi.pick_insider_row(ROSE_ROWS[:1], "Andrea Rose") is None

    def test_no_match_means_none_not_the_first_row(self):
        # The old picker took the first row: a stranger's trades filed under
        # someone the user follows.
        assert sedi.pick_insider_row(ROSE_ROWS, "Brian Rose") is None

    def test_nicknames_match_the_legal_name(self):
        assert sedi.pick_insider_row(ROSE_ROWS, "Mike Rose") in (2, 3)

    def test_two_people_with_one_name_are_told_apart_by_known_companies(self):
        assert sedi.pick_insider_row(ROSE_ROWS, "Mike Rose", ["Tourmaline Oil Corp"]) == 2
        assert sedi.pick_insider_row(ROSE_ROWS, "Mike Rose", ["Some Other Corp."]) == 3

    def test_a_bare_family_name_takes_the_first_exact_family(self):
        # The CLI's --insider takes a family name only.
        assert sedi.pick_insider_row(ROSE_ROWS, "Rose") == 1

    def test_a_company_matches_from_the_start_of_its_name(self):
        rows = ["View CALAMIC003 Nemesia S.a.r.l. Luxembourg Bluestone Resources Inc."]
        assert sedi.pick_insider_row(rows, "Nemesia S.a.r.l.") == 0
        assert sedi.pick_insider_row(rows, "Other Holdings Ltd.") is None


class TestFollowedInsidersAndTheirFirstSearch:
    def test_followed_means_a_person_alarm(self, tmp_path: Path):
        p = tmp_path / "notify.json"
        cfg = notify.load_config(p)
        cfg["alarms"] = [
            {"type": "company", "exchange": "TSE", "ticker": "ATH"},
            {"type": "person", "name": "Eric Sprott"},
            {"type": "person", "name": "eric  sprott"},
            {"type": "person", "name": "Frank Giustra"},
        ]
        notify.save_config(p, cfg)
        assert notify.followed_insiders(p) == ["Eric Sprott", "Frank Giustra"]

    def test_a_first_search_does_not_fire_as_new(self, tmp_path: Path):
        p = tmp_path / "notify.json"
        cfg = notify.load_config(p)
        cfg["alarms"] = [{"type": "person", "name": "Eric Sprott", "seen": []}]
        notify.save_config(p, cfg)
        today = datetime.now(UTC).date().isoformat()
        recs = [txn("Beta", "BET", d=today).to_dict()]
        assert notify.absorb_into_baselines(p, recs) == 1
        assert notify.load_config(p)["alarms"][0]["seen"], "the trade is now baseline"
        assert notify.absorb_into_baselines(p, recs) == 0, "idempotent"


class TestATickerIsTakenOnlyForTheSameName:
    """The name search ranks loosely: Tourmaline came back Athabasca first, and
    taking the first Canadian hit filed Tourmaline's trades under ATH."""

    def resolver(self, results):
        return lambda _name: results

    def test_the_matching_listing_wins_over_a_better_ranked_stranger(self):
        def listing(ticker, name):
            return {"exchange": "TSE", "ticker": ticker, "legal_name": name, "country": "CA"}

        listings = [
            listing("ATH", "Athabasca Oil Corporation"),
            listing("TOU", "Tourmaline Oil Corp."),
        ]
        recs, unresolved = sedi.resolve_tickers(
            [txn("Tourmaline Oil Corp.")], resolver=self.resolver(listings)
        )
        assert (recs[0].exchange, recs[0].ticker) == ("TSE", "TOU")
        assert unresolved == []

    def test_no_matching_name_means_unresolved_not_a_guess(self):
        listings = [
            {
                "exchange": "TSE",
                "ticker": "CVE",
                "legal_name": "Cenovus Energy Inc.",
                "country": "CA",
            }
        ]
        recs, unresolved = sedi.resolve_tickers(
            [txn("BUTTE ENERGY INC.")], resolver=self.resolver(listings)
        )
        assert recs[0].ticker == "" and unresolved == ["BUTTE ENERGY INC."]

    def test_names_written_differently_still_match(self):
        assert sedi.same_listing_name(
            "West Red Lake Gold Mines Ltd.", "West Red Lake Gold Mines Ltd"
        )
        assert sedi.same_listing_name(
            "Outcrop Silver & Gold Corporation", "Outcrop Silver and Gold Corp"
        )
        assert sedi.same_listing_name(
            "Cerro de Pasco Resources Inc. (formerly Genius Properties Ltd.)",
            "Cerro de Pasco Resources Inc",
        )
        assert sedi.same_listing_name(
            "Emerita Resources Corp. formerly Emerita Gold Corp.", "Emerita Resources Corp."
        )

    def test_a_shared_word_is_not_the_same_company(self):
        assert not sedi.same_listing_name(
            "Discovery Silver Corp.", "Canadian Goldfields Discovery Corp"
        )
        assert not sedi.same_listing_name(
            "Oceanic Iron Ore Corp.", "Labrador Iron Ore Royalty Corporation"
        )
        assert not sedi.same_listing_name("Gold Corp.", "Gold Royalty Corp.")


class TestUnmatchedCompaniesAreListed:
    def test_each_once_with_who_trades_it(self):
        saved = {
            "Eric Sprott": finds.summarize([txn("Discovery Silver Corp.")]),
            "Frank Giustra": finds.summarize([txn("Discovery Silver Corp.", who="Frank Giustra")]),
        }
        assert finds.unmatched(saved) == [
            {"issuer_name": "Discovery Silver Corp.", "insiders": ["Eric Sprott", "Frank Giustra"]}
        ]


class TestAFoundCompanyTakesTheWatchlistsExchange:
    def test_a_board_move_does_not_split_one_company_in_two(self):
        rec = txn("Blackrock Silver Corp.", "BRC")
        rec.exchange = "TSE"
        watchlist = [{"name": "Blackrock Silver Corp.", "exchange": "TSXV", "ticker": "BRC"}]
        sedi.align_to_watchlist([rec], watchlist)
        assert rec.exchange == "TSXV"

    def test_a_different_company_on_the_same_ticker_is_left_alone(self):
        # Rio2 (TSX: RIO) is not Rio Tinto (NYSE: RIO).
        rec = txn("Rio2 Limited", "RIO")
        rec.exchange = "TSE"
        watchlist = [{"name": "Rio Tinto", "exchange": "NYSE", "ticker": "RIO"}]
        sedi.align_to_watchlist([rec], watchlist)
        assert rec.exchange == "TSE"
