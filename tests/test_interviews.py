# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Interview extraction: parsing the reply, and matching companies by name.

The matching cases here are not hypothetical. Every one marked "regression" was
a wrong match in the first real run against two live interviews.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest

from insight import interviews, llm


class TestVideoId:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=i25DJxYcDGw",
            "https://youtu.be/i25DJxYcDGw",
            "https://www.youtube.com/embed/i25DJxYcDGw",
            "https://www.youtube.com/watch?v=i25DJxYcDGw&t=42s",
            "i25DJxYcDGw",
        ],
    )
    def test_it_finds_the_id_in_every_url_shape(self, url):
        assert interviews.video_id(url) == "i25DJxYcDGw"

    def test_it_refuses_something_that_is_not_a_video(self):
        with pytest.raises(ValueError):
            interviews.video_id("https://example.com/not-a-video")


class TestMatchingCompanyNames:
    """Whether an interview's company is one the watchlist already follows."""

    WATCHLIST: ClassVar[list[dict[str, str]]] = [
        {"name": "Capstone Copper Corp", "exchange": "TSE", "ticker": "CS"},
        {"name": "Elemental Royalty Corp.", "exchange": "TSE", "ticker": "ELE"},
        {"name": "West Red Lake Gold Mines Limited", "exchange": "TSXV", "ticker": "WRLG"},
        {"name": "Franco-Nevada Corporation", "exchange": "TSE", "ticker": "FNV"},
        {"name": "Pan American Silver Corp.", "exchange": "TSE", "ticker": "PAAS"},
    ]

    def match(self, name: str, ticker: str = ""):
        m = interviews.Mention(name=name, ticker=ticker)
        return interviews.match_watchlist(m, self.WATCHLIST)

    def test_a_shortened_name_finds_its_company(self):
        out = self.match("Capstone")
        assert out.on_watchlist and out.matched_name == "Capstone Copper Corp"

    def test_a_legal_suffix_does_not_prevent_a_match(self):
        assert self.match("Pan American Silver").matched_name == "Pan American Silver Corp."

    def test_punctuation_does_not_prevent_a_match(self):
        assert self.match("Franco Nevada").matched_name == "Franco-Nevada Corporation"

    def test_a_stated_ticker_wins_over_the_name(self):
        # The transcript's name may be garbled by auto-captions; a ticker is not.
        out = self.match("Capstan Copper", ticker="CS")
        assert out.matched_name == "Capstone Copper Corp"

    # -- regressions from the first live run --------------------------------
    def test_royal_gold_is_not_elemental_royalty(self):
        # Substring matching paired these, because stripping "gold" left "royal",
        # which appears inside "elemental royalty".
        assert not self.match("Royal Gold").on_watchlist

    def test_gold_royalty_corp_is_not_elemental_royalty(self):
        assert not self.match("Gold Royalty Corp").on_watchlist

    def test_silver_mines_is_not_west_red_lake_gold_mines(self):
        # Stripping the commodity word left the bare word "mines".
        assert not self.match("Silver Mines").on_watchlist

    def test_an_unrelated_company_stays_unmatched(self):
        out = self.match("Southern Copper")
        assert not out.on_watchlist and out.matched_name == ""

    def test_a_commodity_word_is_kept_because_it_carries_identity(self):
        assert interviews.normalise("Silver Mines Limited") == "silver mines"

    def test_only_the_legal_suffix_comes_off(self):
        assert interviews.normalise("Barrick Mining Corporation") == "barrick mining"


class TestParsingTheReply:
    def test_plain_json_is_read(self):
        assert interviews._json_from('{"companies": []}') == {"companies": []}

    def test_a_code_fence_is_tolerated(self):
        # Models wrap JSON in a fence often enough that failing on it would mean
        # throwing away a good answer over punctuation.
        assert interviews._json_from('```json\n{"companies": []}\n```') == {"companies": []}

    def test_prose_around_the_object_is_tolerated(self):
        assert interviews._json_from('Sure!\n{"speaker": "x"}\nHope that helps') == {"speaker": "x"}

    def test_a_reply_that_is_not_an_object_is_an_error(self):
        with pytest.raises(ValueError):
            interviews._json_from("[1, 2, 3]")


def _router(payload: dict, route: str = "fake") -> llm.LLMRouter:
    """A router whose single route always returns `payload`."""

    def transport(url, headers, body, timeout):
        return (
            200,
            {},
            json.dumps(
                {
                    "choices": [{"message": {"content": json.dumps(payload)}}],
                    "usage": {"prompt_tokens": 10, "total_tokens": 20},
                }
            ).encode(),
        )

    r = llm.Route(name=route, base_url="https://x/v1", api_key="k", model="m")
    return llm.LLMRouter([r], ledger=llm.Ledger(None), transport=transport)


class TestExtracting:
    PAYLOAD: ClassVar[dict[str, Any]] = {
        "speaker": "Rick Rule",
        "companies": [
            {
                "name": "Pan American Silver",
                "ticker": "",
                "exchange": "",
                "stance": "bullish",
                "bullets": ["Rule: free is a really good price for a billion ounces"],
            },
            {
                "name": "Southern Copper",
                "stance": "cautious",
                "bullets": ["Rule: political risk"],
            },
            {"name": "", "bullets": ["nameless, should be dropped"]},
        ],
    }

    def extraction(self):
        return interviews.extract(
            _router(self.PAYLOAD),
            "a transcript",
            watchlist=TestMatchingCompanyNames.WATCHLIST,
            url="https://youtu.be/i25DJxYcDGw",
            vid="i25DJxYcDGw",
        )

    def test_it_keeps_the_speaker_and_drops_nameless_rows(self):
        e = self.extraction()
        assert e.speaker == "Rick Rule"
        assert [m.name for m in e.mentions] == ["Pan American Silver", "Southern Copper"]

    def test_watchlist_membership_is_resolved_per_company(self):
        e = self.extraction()
        assert [m.on_watchlist for m in e.mentions] == [True, False]

    def test_the_speakers_words_survive_into_the_bullet(self):
        # The whole point of an interview over a filing is the opinion in it.
        e = self.extraction()
        assert "free is a really good price" in e.mentions[0].bullets[0]

    def test_the_route_that_answered_is_recorded(self):
        e = self.extraction()
        assert e.route == "fake" and e.attempts == ["fake: ok"]


class TestTheReport:
    def report(self):
        e = interviews.extract(
            _router(TestExtracting.PAYLOAD),
            "a transcript",
            watchlist=TestMatchingCompanyNames.WATCHLIST,
            url="https://youtu.be/i25DJxYcDGw",
            vid="i25DJxYcDGw",
        )
        return interviews.render_report([e])

    def test_followed_and_unfollowed_companies_are_kept_apart(self):
        # A suggestion must never read as a decision.
        text = self.report()
        known = text.index("ON YOUR WATCHLIST")
        new = text.index("NOT ON YOUR WATCHLIST")
        assert known < text.index("Pan American Silver") < new < text.index("Southern Copper")

    def test_it_says_plainly_that_nothing_was_written(self):
        assert "Nothing here has been written into the app" in self.report()

    def test_the_totals_add_up(self):
        assert "TOTAL 2 companies discussed across 1 video(s): 1 already followed, 1 new." in (
            self.report()
        )
