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
                "bullets": ["Free is a really good price for a billion ounces"],
            },
            {"name": "Southern Copper", "bullets": ["Political risk"]},
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
            resolve=False,
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
        assert "Free is a really good price" in e.mentions[0].bullets[0]

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
            resolve=False,
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


class TestBulletsCarryTheSpeakerAndTheDate:
    """A note that does not say who said it reads as InSight's own view later,
    and one that does not say when cannot be weighed at all."""

    def test_each_bullet_is_prefixed_with_the_speaker_and_date(self):
        m = interviews.Mention(name="X", bullets=["Cheap.", "Execution superb."])
        assert interviews.note_bullets(m, "Rick Rule", "2026-05-02") == [
            "[Rick Rule - May 2 2026] Cheap.",
            "[Rick Rule - May 2 2026] Execution superb.",
        ]

    def test_an_unknown_date_is_left_off_rather_than_faked(self):
        # Filling it with the run date would quietly date the opinion to
        # whenever the video happened to be read.
        m = interviews.Mention(name="X", bullets=["Cheap."])
        assert interviews.note_bullets(m, "Rick Rule", "") == ["[Rick Rule] Cheap."]

    def test_a_day_is_not_zero_padded(self):
        assert interviews.pretty_date("2026-05-02") == "May 2 2026"

    def test_a_date_that_is_not_a_date_yields_nothing(self):
        assert interviews.pretty_date("not-a-date") == ""
        assert interviews.pretty_date("") == ""

    def test_an_unknown_speaker_is_labelled_not_omitted(self):
        m = interviews.Mention(name="X", bullets=["Cheap."])
        assert interviews.note_bullets(m, "") == ["[Unattributed] Cheap."]

    def test_the_report_uses_the_same_bullets_the_app_would_write(self):
        e = interviews.extract(
            _router(TestExtracting.PAYLOAD),
            "t",
            watchlist=TestMatchingCompanyNames.WATCHLIST,
            published="2026-05-02",
            resolve=False,
        )
        assert "[Rick Rule - May 2 2026] Political risk" in interviews.render_report([e])

    def test_the_stored_bullets_carry_the_date_too(self):
        e = interviews.extract(
            _router(TestExtracting.PAYLOAD),
            "t",
            published="2026-05-02",
            resolve=False,
        )
        assert interviews.as_dict(e)["companies"][0]["bullets"][0].startswith(
            "[Rick Rule - May 2 2026] "
        )


class TestSpeakerFromTitle:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Rick Rule: gold is cheap", "Rick Rule"),
            ("Rick Rule - why he is buying", "Rick Rule"),
            ("Uranium outlook with Julian Treger", "Julian Treger"),
            ("Why gold is going up", ""),  # no name to be confident about
            ("", ""),
        ],
    )
    def test_only_the_confident_shapes_yield_a_name(self, title, expected):
        # A wrong guess puts the wrong person's name on every bullet, which is
        # worse than leaving it blank.
        assert interviews.speaker_from_title(title) == expected


class TestResolvingAListing:
    """The issuer search is the authority on tickers; the model only guesses."""

    def stub(self, monkeypatch, hits):
        from insight import issuers

        monkeypatch.setattr(issuers, "search_issuers", lambda name, limit=5: hits)

    def test_the_searches_ticker_replaces_the_models_guess(self, monkeypatch):
        # The model offered CTEK for CoTec; the real listing is CTH.
        self.stub(
            monkeypatch,
            [{"legal_name": "CoTec Holdings Corp", "ticker": "CTH", "exchange": "TSXV"}],
        )
        out = interviews.resolve_listing(interviews.Mention(name="CoTec Holdings", ticker="CTEK"))
        assert out.resolved and (out.ticker, out.exchange) == ("CTH", "TSXV")

    def test_an_unrelated_hit_is_not_accepted(self, monkeypatch):
        self.stub(
            monkeypatch,
            [{"legal_name": "Kioxia Holdings Corporation", "ticker": "285A", "exchange": "TSE"}],
        )
        out = interviews.resolve_listing(interviews.Mention(name="CoTec Holdings"))
        assert not out.resolved and out.ticker == ""

    def test_a_company_already_followed_is_left_alone(self, monkeypatch):
        self.stub(monkeypatch, [{"legal_name": "Anything", "ticker": "X", "exchange": "Y"}])
        m = interviews.Mention(name="Capstone", on_watchlist=True)
        assert not interviews.resolve_listing(m).resolved

    def test_a_search_that_fails_is_not_fatal(self, monkeypatch):
        from insight import issuers

        def boom(name, limit=5):
            raise OSError("offline")

        monkeypatch.setattr(issuers, "search_issuers", boom)
        assert not interviews.resolve_listing(interviews.Mention(name="X")).resolved


class TestSavedInterviews:
    """Storage keyed by video, with the note text frozen at extraction time."""

    def entry(self, vid="v1", company="Pan American Silver", applied=False):
        return {
            "video_id": vid,
            "url": f"https://youtu.be/{vid}",
            "speaker": "Rick Rule",
            "companies": [{"name": company, "bullets": ["[Rick Rule] Cheap."], "applied": applied}],
        }

    def test_a_run_can_be_saved_and_read_back(self, tmp_path):
        p = tmp_path / "interviews.json"
        interviews.save_extraction(self.entry(), p)
        assert [r["video_id"] for r in interviews.load_saved(p)] == ["v1"]

    def test_the_newest_run_comes_first(self, tmp_path):
        p = tmp_path / "interviews.json"
        interviews.save_extraction(self.entry("old"), p)
        interviews.save_extraction(self.entry("new"), p)
        assert [r["video_id"] for r in interviews.load_saved(p)] == ["new", "old"]

    def test_rerunning_a_video_replaces_it_rather_than_duplicating(self, tmp_path):
        p = tmp_path / "interviews.json"
        interviews.save_extraction(self.entry("v1", "First"), p)
        interviews.save_extraction(self.entry("v1", "Second"), p)
        rows = interviews.load_saved(p)
        assert len(rows) == 1 and rows[0]["companies"][0]["name"] == "Second"

    def test_forgetting_reports_whether_it_removed_anything(self, tmp_path):
        p = tmp_path / "interviews.json"
        interviews.save_extraction(self.entry("v1"), p)
        assert interviews.forget("v1", p) is True
        assert interviews.forget("v1", p) is False

    def test_applying_is_remembered_so_it_is_not_offered_twice(self, tmp_path):
        p = tmp_path / "interviews.json"
        interviews.save_extraction(self.entry(), p)
        interviews.mark_applied("v1", "Pan American Silver", p)
        assert interviews.load_saved(p)[0]["companies"][0]["applied"] is True

    def test_an_unreadable_file_yields_nothing_rather_than_raising(self, tmp_path):
        p = tmp_path / "interviews.json"
        p.write_text("{not json")
        assert interviews.load_saved(p) == []

    def test_the_stored_bullets_already_carry_the_speaker(self):
        # Frozen at extraction time: what the user approves in the UI is exactly
        # what later lands in the notes, even if the prompt changes underneath.
        e = interviews.extract(
            _router(TestExtracting.PAYLOAD),
            "t",
            watchlist=TestMatchingCompanyNames.WATCHLIST,
            resolve=False,
        )
        stored = interviews.as_dict(e)
        assert stored["companies"][0]["bullets"][0].startswith("[Rick Rule] ")


class TestAMissingDependencyExplainsItself:
    def test_the_error_names_the_command_that_fixes_it(self, monkeypatch):
        # An install predating this dependency raised a bare
        # ModuleNotFoundError, which tells the user nothing they can act on.
        import builtins

        real = builtins.__import__

        def no_transcripts(name, *a, **kw):
            if name == "youtube_transcript_api":
                raise ImportError("No module named 'youtube_transcript_api'")
            return real(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", no_transcripts)
        with pytest.raises(RuntimeError, match="uv tool"):
            interviews.fetch_transcript("i25DJxYcDGw")
