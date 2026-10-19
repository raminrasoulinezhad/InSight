# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""SEC EDGAR: Form 4 parsing, name handling, and a client that stays polite.

No network: the client takes an opener, and the scraper a fake client.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path
from typing import ClassVar

import pytest

from insight import aggregate, edgar, notify

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer>
    <issuerCik>0001164727</issuerCik>
    <issuerName>NEWMONT Corp /DE/</issuerName>
    <issuerTradingSymbol>NEM</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001936354</rptOwnerCik>
      <rptOwnerName>Toth Peter</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>EVP &amp; CDO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2024-11-01</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>3000</value></transactionShares>
        <transactionPricePerShare><value>45.45</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2024-11-01</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>812.5</value></transactionShares>
        <transactionPricePerShare><footnoteId id="F1"/></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <transactionDate><value>2024-11-01</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>5000</value></transactionShares>
      </transactionAmounts>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>
"""


class TestAForm4IsRead:
    def test_the_owner_and_the_issuer(self):
        f = edgar.parse_form4(FORM4)
        assert (f.issuer_cik, f.issuer_ticker, f.owner_cik) == (1164727, "NEM", 1936354)
        assert f.owner_name == "Peter Toth", "EDGAR files people family name first"

    def test_each_share_transaction_becomes_a_record(self):
        sale, withholding = edgar.parse_form4(FORM4, source_url="u", scraped_at="t").records
        assert sale.transaction_type == "Sell"
        assert (sale.shares, sale.avg_price, sale.total_value) == (3000, 45.45, 136350.0)
        assert sale.insider_role == "EVP & CDO"
        assert (sale.currency, sale.source, sale.source_url) == ("USD", "edgar", "u")
        assert sale.transaction_date == "2024-11-01"
        assert withholding.transaction_type == "Tax withholding on vesting"
        assert withholding.shares == 812 or withholding.shares == 813
        assert withholding.avg_price is None and withholding.total_value is None

    def test_the_derivative_table_is_not_counted_twice(self):
        # The option exercise also shows as shares acquired in the non-derivative
        # table; reading both would show the one event twice.
        assert len(edgar.parse_form4(FORM4).records) == 2

    def test_the_issuer_name_drops_the_state_tag(self):
        assert edgar.issuer_display("NEWMONT Corp /DE/") == "NEWMONT Corp"
        assert edgar.issuer_display("HYCROFT MINING HOLDING CORP") == "Hycroft Mining Holding Corp"


class TestNames:
    @pytest.mark.parametrize(
        ("sec", "shown"),
        [
            ("Toth Peter", "Peter Toth"),
            ("SPROTT ERIC", "Eric Sprott"),
            ("FRIEDLAND ROBERT M", "Robert M Friedland"),
            ("SMITH JOHN A JR", "John A Smith Jr"),
            ("2176423 Ontario Ltd.", "2176423 Ontario Ltd."),
            ("BERKSHIRE HATHAWAY INC", "BERKSHIRE HATHAWAY INC"),
        ],
    )
    def test_people_read_given_name_first_and_firms_as_filed(self, sec, shown):
        assert edgar.display_name(sec) == shown

    def test_the_lookup_is_typed_family_name_first(self):
        # EDGAR's lookup needs every word: the full name finds nobody.
        assert edgar.entity_query("Robert Martin Friedland") == "Friedland Robert"
        assert edgar.entity_query("Sprott Mining Inc.") == "Sprott Mining"


class TestPickingTheRightPerson:
    FRIEDLANDS: ClassVar = [(938198, "FRIEDLAND ROBERT M"), (1237332, "FRIEDLAND ROBERT J/CT/")]

    def test_a_middle_initial_settles_a_tie(self):
        assert edgar.pick_owner(self.FRIEDLANDS, "Robert Martin Friedland") == 938198

    def test_an_unbreakable_tie_is_no_one(self):
        # Several Michael Roses and nothing to tell them apart: never guess.
        roses = [(1, "ROSE MICHAEL A"), (2, "ROSE MICHAEL B")]
        assert edgar.pick_owner(roses, "Mike Rose") is None

    def test_a_single_match_with_a_nickname(self):
        assert edgar.pick_owner([(5, "ROSE MICHAEL")], "Mike Rose") == 5

    def test_a_different_given_name_is_not_them(self):
        assert edgar.pick_owner([(7, "GIUSTRA ROBERT F")], "Frank Giustra") is None

    def test_an_organisation_by_its_name(self):
        hits = [(9, "Sprott Mining Inc."), (10, "Sprott Asset Management LP")]
        assert edgar.pick_owner(hits, "Sprott Mining Inc.") == 9


class TestTheFilingList:
    SUB: ClassVar = {
        "filings": {
            "recent": {
                "form": ["4", "8-K", "4/A", "5", "4"],
                "filingDate": [
                    "2026-10-01",
                    "2026-09-01",
                    "2026-08-01",
                    "2026-02-01",
                    "2023-01-01",
                ],
                "accessionNumber": ["a1", "a2", "a3", "a4", "a5"],
                "primaryDocument": [
                    "xslF345X05/wk-form4.xml",
                    "x.htm",
                    "xslF345X05/f4a.xml",
                    "f5.xml",
                    "old.xml",
                ],
            }
        }
    }

    def test_only_ownership_forms_inside_the_window(self):
        assert edgar.ownership_filings(self.SUB, "2024-10-19") == [
            ("2026-10-01", "a1", "wk-form4.xml"),
            ("2026-08-01", "a3", "f4a.xml"),
            ("2026-02-01", "a4", "f5.xml"),
        ]


class TestTheClient:
    def client(self, tmp_path: Path, replies: list) -> tuple[edgar.EdgarClient, list]:
        seen: list = []

        def opener(req, timeout):
            seen.append(req)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        limiter = edgar._RateLimiter(1000.0)
        return edgar.EdgarClient("me@example.com", tmp_path, opener=opener, limiter=limiter), seen

    def test_it_names_itself_with_the_contact(self, tmp_path: Path):
        c, seen = self.client(tmp_path, [b"{}"])
        c.get_json("https://data.sec.gov/x")
        assert seen[0].get_header("User-agent") == "InSight personal research me@example.com"

    def test_it_backs_off_when_told_to_slow_down(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(edgar.time, "sleep", lambda s: None)
        busy = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        c, seen = self.client(tmp_path, [busy, b"ok"])
        assert c.get("https://www.sec.gov/x") == b"ok"
        assert len(seen) == 2

    def test_a_refusal_that_is_not_about_load_raises_at_once(self, tmp_path: Path):
        gone = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        c, seen = self.client(tmp_path, [gone])
        with pytest.raises(urllib.error.HTTPError):
            c.get("https://www.sec.gov/x")
        assert len(seen) == 1

    def test_a_filing_is_fetched_once_ever(self, tmp_path: Path):
        c, seen = self.client(tmp_path, [FORM4.encode()])
        assert c.document(1164727, "0001-24-000001", "f4.xml") == FORM4.encode()
        assert c.document(1164727, "0001-24-000001", "f4.xml") == FORM4.encode()
        assert len(seen) == 1

    def test_no_contact_no_client(self, tmp_path: Path):
        with pytest.raises(edgar.EdgarNotConfigured):
            edgar.EdgarClient("", tmp_path)

    def test_the_rate_limit_spaces_requests(self, monkeypatch):
        slept: list[float] = []
        clock = [100.0]
        monkeypatch.setattr(edgar.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(edgar.time, "sleep", slept.append)
        lim = edgar._RateLimiter(5.0)
        for _ in range(3):
            lim.wait()
        assert slept == pytest.approx([0.2, 0.4])


class FakeClient:
    def __init__(self, filings=None, listing=(1164727, "NYSE", "NEWMONT Corp /DE/")):
        self.requests = 0
        self._filings = filings or [("2026-10-01", "acc-1", "f4.xml")]
        self._listing = listing

    def listings(self):
        return {"NEM": self._listing} if self._listing else {}

    def filings_for(self, cik, since):
        return self._filings

    def document(self, cik, accession, doc):
        return FORM4.encode()

    def find_entities(self, name):
        return [(1936354, "TOTH PETER")]


class TestTheScraper:
    def test_a_company_files_under_the_watchlists_own_names(self):
        with edgar.EdgarScraper(Path("."), client=FakeClient()) as s:
            recs = s.fetch("NYSE", "nem", "Newmont Corporation")
        assert {(r.exchange, r.ticker, r.issuer_name) for r in recs} == {
            ("NYSE", "NEM", "Newmont Corporation")
        }

    def test_a_foreign_issuer_files_nothing(self):
        with edgar.EdgarScraper(Path("."), client=FakeClient(listing=None)) as s:
            assert s.fetch("NYSE", "NEM", "BHP") == []

    def test_a_person_brings_the_board_sec_lists(self):
        with edgar.EdgarScraper(Path("."), client=FakeClient()) as s:
            recs = s.fetch_insider("Peter Toth")
        assert {(r.exchange, r.ticker) for r in recs} == {("NYSE", "NEM")}

    def test_us_finds_take_the_watchlists_name_and_board(self):
        with edgar.EdgarScraper(Path("."), client=FakeClient()) as s:
            recs = s.fetch_insider("Peter Toth")
        wl = [{"name": "Newmont Corporation", "exchange": "NYSE", "ticker": "NEM"}]
        edgar.align_to_watchlist(recs, wl)
        assert {r.issuer_name for r in recs} == {"Newmont Corporation"}


class TestAlongsideTheOtherSources:
    def rec(self, source, role="Insider", name="Peter Toth"):
        return {
            "source": source,
            "exchange": "NYSE",
            "ticker": "NEM",
            "insider_name": name,
            "insider_role": role,
            "transaction_date": "2026-10-01",
            "transaction_type": "Sell",
            "shares": 3000,
        }

    def test_edgars_copy_of_a_trade_beats_marketbeats(self):
        mb, sec = self.rec("marketbeat"), self.rec("edgar", role="EVP & CDO")
        assert aggregate.drop_cross_source_twins([mb, sec]) == [sec]

    def test_members_of_congress_are_not_insiders(self):
        house = self.rec("marketbeat", role="House (D-CA)", name="Ro Khanna")
        senate = self.rec("marketbeat", role="Senate (R-TX)", name="Someone Else")
        officer = self.rec("marketbeat", role="CEO")
        assert aggregate.drop_congress_trades([house, senate, officer]) == [officer]

    def test_tax_withholding_does_not_alert(self):
        assert notify._alertable({"transaction_type": "Tax withholding on vesting"}) is False
        assert notify._alertable({"transaction_type": "Sell"}) is True

    def test_never_fetched_companies_are_found_by_their_cache(self, tmp_path: Path):
        (tmp_path / "NYSE_NEM.json").write_text("{}")
        targets = [
            {"exchange": "NYSE", "ticker": "NEM"},
            {"exchange": "NASDAQ", "ticker": "VKTX"},
        ]
        assert edgar.never_fetched(tmp_path, targets) == {"NASDAQ:VKTX"}

    def test_contact_comes_from_the_environment_first(self, tmp_path: Path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("INSIGHT_SEC_CONTACT=file@example.com\n")
        monkeypatch.delenv("INSIGHT_SEC_CONTACT", raising=False)
        assert edgar.contact(env) == "file@example.com"
        monkeypatch.setenv("INSIGHT_SEC_CONTACT", "env@example.com")
        assert edgar.contact(env) == "env@example.com"


class TestCompaniesListedInBothCountries:
    def test_a_us_find_files_under_the_toronto_card_of_the_same_company(self):
        with edgar.EdgarScraper(Path("."), client=FakeClient()) as s:
            recs = s.fetch_insider("Peter Toth")
        for r in recs:
            r.issuer_name = "Ivanhoe Electric Inc."
            r.ticker = "IE"
        wl = [{"name": "Ivanhoe Electric Inc.", "exchange": "TSE", "ticker": "IE"}]
        edgar.align_to_watchlist(recs, wl)
        assert {(r.exchange, r.ticker) for r in recs} == {("TSE", "IE")}

    def test_the_same_symbol_on_another_board_is_not_assumed_the_same(self):
        with edgar.EdgarScraper(Path("."), client=FakeClient()) as s:
            recs = s.fetch_insider("Peter Toth")
        wl = [{"name": "Nemaska Lithium", "exchange": "TSE", "ticker": "NEM"}]
        edgar.align_to_watchlist(recs, wl)
        assert {r.exchange for r in recs} == {"NYSE"}

    def test_sedi_and_edgar_copies_of_one_trade_are_kept_once(self):
        def rec(source):
            return {
                "source": source,
                "exchange": "TSE",
                "ticker": "IE",
                "insider_name": "Robert Friedland",
                "transaction_date": "2026-06-01",
                "transaction_type": "Buy",
                "shares": 1000,
            }

        sedi, sec, mb = rec("sedi"), rec("edgar"), rec("marketbeat")
        assert aggregate.drop_cross_source_twins([mb, sec, sedi]) == [sedi]
        assert aggregate.drop_cross_source_twins([mb, sec]) == [sec]

    def test_two_trades_from_one_source_are_both_kept(self):
        a = {
            "source": "edgar",
            "exchange": "NYSE",
            "ticker": "NEM",
            "insider_name": "A B",
            "transaction_date": "2026-01-01",
            "transaction_type": "Sell",
            "shares": 5,
        }
        assert aggregate.drop_cross_source_twins([a, dict(a)]) == [a, a]


class TestWhichCompaniesFileWithTheSec:
    LISTINGS: ClassVar = {
        "IE": (1879016, "NYSE", "Ivanhoe Electric Inc."),
        "CNQ": (1017413, "NYSE", "CANADIAN NATURAL RESOURCES LTD"),
        "ABX": (1, "NYSE", "Some Unrelated Co"),
    }

    def test_us_listings_and_same_name_registrants_elsewhere(self):
        targets = [
            {"name": "Newmont Corporation", "exchange": "NYSE", "ticker": "NEM"},
            {"name": "Ivanhoe Electric Inc.", "exchange": "TSE", "ticker": "IE"},
            {"name": "Canadian Natural Resources Limited", "exchange": "TSE", "ticker": "CNQ"},
            {"name": "Barrick Mining", "exchange": "TSE", "ticker": "ABX"},
            {"name": "Athabasca Oil", "exchange": "TSE", "ticker": "ATH"},
        ]
        got = [t["ticker"] for t in edgar.sec_registrants(targets, self.LISTINGS)]
        assert got == ["NEM", "IE", "CNQ"], "a shared symbol alone is not the same company"

    def test_a_company_sedi_already_covers_stays_sedis(self):
        targets = [
            {"name": "Ivanhoe Electric Inc.", "exchange": "TSE", "ticker": "IE"},
            {"name": "Canadian Natural Resources Limited", "exchange": "TSE", "ticker": "CNQ"},
        ]
        got = edgar.sec_registrants(targets, self.LISTINGS, on_sedi={"TSE:CNQ"})
        assert [t["ticker"] for t in got] == ["IE"]
