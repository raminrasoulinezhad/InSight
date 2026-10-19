# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Pure-logic tests for the SEDI parser. No network / browser — the stateful
report-row walk (insider group headers + transaction rows) is exercised with
rows captured from a live SEDI Insider Transaction Detail report."""

from __future__ import annotations

import pytest

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


class FakeCdp:
    """Records what would have been sent to Chrome's DevTools protocol."""

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, dict]] = []
        self.fail = fail

    def send(self, method: str, params: dict | None = None) -> dict:
        if self.fail:
            raise RuntimeError("no window for target")
        self.sent.append((method, params or {}))
        return {"windowId": 7}

    def states(self) -> list[str]:
        return [p["bounds"]["windowState"] for m, p in self.sent if m == "Browser.setWindowBounds"]


class FakePage:
    def __init__(self):
        self.fronted = 0

    def bring_to_front(self) -> None:
        self.fronted += 1


def wired(cdp: FakeCdp | None = None, **kw) -> tuple[sedi.SediScraper, FakeCdp, FakePage]:
    """A scraper with a stub browser attached, without launching one."""
    cdp = cdp or FakeCdp()
    page = FakePage()
    s = sedi.SediScraper(**kw)
    s._ctx = type("Ctx", (), {"new_cdp_session": lambda self, p: cdp})()
    s._page = page
    return s, cdp, page


class TestTheWindowKeepsOutOfTheWay:
    """A SEDI scrape takes minutes and needs a human for perhaps ten seconds of
    it, so the browser stays minimized and un-minimizes itself for the CAPTCHA.
    Chrome is asked to move its own window (CDP) rather than the desktop being
    asked to move Chrome, because the latter has no portable answer: X11 needs
    wmctrl/xdotool installed and Wayland refuses outright."""

    def test_it_minimizes_itself_once_attached(self):
        s, cdp, _ = wired()
        s.hide_window()
        assert cdp.states() == ["minimized"]

    def test_asking_to_see_it_is_honoured(self):
        s, cdp, _ = wired(start_minimized=False)
        s.hide_window()
        assert cdp.states() == [], "--sedi-window means leave the window alone"

    def test_headless_has_no_window_to_move(self):
        s, cdp, _ = wired(headless=True)
        s.hide_window()
        s.show_window()
        assert cdp.sent == []

    def test_showing_it_restores_and_raises(self):
        s, cdp, page = wired()
        s.show_window()
        assert cdp.states() == ["normal"]
        assert page.fronted == 1, "un-minimizing is not enough if it is behind the app"

    def test_showing_it_ignores_start_minimized(self):
        # A CAPTCHA needs hands whatever the flag said. Only hiding is optional.
        s, cdp, _ = wired(start_minimized=False)
        s.show_window()
        assert cdp.states() == ["normal"]

    def test_the_window_handle_is_looked_up_once(self):
        s, cdp, _ = wired()
        for _ in range(3):
            s.hide_window()
        assert [m for m, _ in cdp.sent].count("Browser.getWindowForTarget") == 1
        assert cdp.states() == ["minimized"] * 3

    def test_a_window_that_will_not_move_is_not_fatal(self):
        # Some desktops refuse the request. Losing the scrape over cosmetics
        # would be a far worse outcome than a window sitting in the way.
        s, _cdp, page = wired(FakeCdp(fail=True))
        s.hide_window()
        s.show_window()
        assert page.fronted == 1, "the raise is attempted even if the resize failed"

    def test_nothing_is_sent_before_the_browser_exists(self):
        s = sedi.SediScraper()
        s.hide_window()  # __enter__ has not run; must not explode


class TestTheBotWallBringsTheWindowBack:
    def test_a_clear_page_leaves_the_window_alone(self, monkeypatch):
        s, cdp, _ = wired()
        monkeypatch.setattr(s, "_walled", lambda: False)
        s._clear_wall_or_raise("opening ITD search")
        assert cdp.sent == []

    def test_the_wall_raises_the_window_then_hides_it_again(self, monkeypatch, capsys):
        s, cdp, page = wired()
        seen = iter([True, False])  # walled, then solved
        monkeypatch.setattr(s, "_walled", lambda: next(seen))
        s._page.wait_for_timeout = lambda ms: None
        s._clear_wall_or_raise("running ITD search")
        assert cdp.states() == ["normal", "minimized"]
        assert page.fronted == 1

    def test_headless_still_fails_fast_rather_than_waiting(self):
        s, cdp, _ = wired(headless=True)
        s._walled = lambda: True
        try:
            s._clear_wall_or_raise("opening ITD search")
        except sedi.BotBlocked:
            pass
        else:
            raise AssertionError("headless cannot solve a CAPTCHA; it must not wait")
        assert cdp.sent == []


class TestBackgroundThrottlingIsOff:
    def test_the_launch_flags_keep_a_hidden_window_running(self):
        # Chrome throttles background timers to ~1/min. Radware's challenge and
        # SEDI's own scripts run on timers, so minimizing without these would
        # trade a window in the way for a scrape that crawls.
        import inspect

        src = inspect.getsource(sedi.SediScraper._launch)
        for flag in (
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ):
            assert flag in src


class TestEnteringHidesTheWindow:
    """The one place hiding has to happen: right after the browser appears."""

    def test_the_window_is_minimized_as_soon_as_it_exists(self, monkeypatch):
        cdp, page = FakeCdp(), FakePage()
        ctx = type("Ctx", (), {"new_cdp_session": lambda self, p: cdp, "pages": [page]})()
        stealth = type("CM", (), {"use_sync": lambda self, pw: pw})()
        monkeypatch.setattr(sedi, "Stealth", lambda: stealth)
        monkeypatch.setattr(
            sedi, "sync_playwright", lambda: type("PW", (), {"__enter__": lambda s: s})()
        )
        s = sedi.SediScraper()
        monkeypatch.setattr(s, "_launch", lambda channel: ctx)
        with monkeypatch.context():
            s.__enter__()
        assert cdp.states() == ["minimized"]


class FakeLocator:
    """Records which flavour of click it was asked for."""

    def __init__(self) -> None:
        self.clicked: list[int | None] = []
        self.dispatched: list[tuple[str, int | None]] = []

    def click(self, timeout: int | None = None) -> None:
        self.clicked.append(timeout)

    def dispatch_event(self, type: str, timeout: int | None = None) -> None:
        self.dispatched.append((type, timeout))


class TestClickingDoesNotPopTheWindowUp:
    """A real click reaches Chrome's Input domain, and Chrome restores a
    minimized window when it gets one. With up to two clicks per company, that
    was the window jumping to the front over and over for a whole batch."""

    def test_a_hidden_window_dispatches_instead_of_clicking(self):
        s, _cdp, _page = wired()
        loc = FakeLocator()
        s._click(loc)
        assert loc.dispatched == [("click", 8000)]
        assert loc.clicked == []

    def test_a_visible_window_gets_the_real_click(self):
        s, _cdp, _page = wired(start_minimized=False)
        loc = FakeLocator()
        s._click(loc)
        assert loc.clicked == [8000]
        assert loc.dispatched == []

    def test_headless_gets_the_real_click(self):
        # No window exists to protect, so use the more faithful input.
        s, _cdp, _page = wired(headless=True)
        loc = FakeLocator()
        s._click(loc)
        assert loc.clicked == [8000]
        assert loc.dispatched == []

    def test_the_timeout_is_carried_through_either_path(self):
        hidden, _c, _p = wired()
        shown, _c2, _p2 = wired(start_minimized=False)
        a, b = FakeLocator(), FakeLocator()
        hidden._click(a, timeout=1234)
        shown._click(b, timeout=1234)
        assert a.dispatched == [("click", 1234)]
        assert b.clicked == [1234]

    def test_every_click_in_the_scrape_goes_through_the_helper(self):
        # A raw .click() anywhere else re-introduces the pop-up, and it would
        # only show up on a live run.
        import inspect

        for fn in (
            sedi.SediScraper._fill_search,
            sedi.SediScraper._select_issuer_if_needed,
            sedi.SediScraper._select_insider_if_needed,
        ):
            src = inspect.getsource(fn)
            assert ".click(" not in src, f"{fn.__name__} clicks directly"
            assert "self._click(" in src, f"{fn.__name__} never clicks at all"


class TestABlockedSearchIsNotAnEmptyResult:
    """Radware can let the form load and then swallow the submit. Read as "no
    rows", that wiped the company's cache and saved a blank form as its page."""

    def test_a_navigation_stuck_on_the_validator_is_a_dead_wall(self):
        assert sedi._is_dead_wall("Loading http://validate.perfdrive.com/c2", "about:blank")
        assert sedi._is_dead_wall("validate.perfdrive.com", "chrome-error://chromewebdata/")

    def test_a_slow_sedi_page_is_not_a_dead_wall(self):
        url = "https://www.sedi.ca/sedi/SVTItdController?locale=en_CA"
        assert not sedi._is_dead_wall("SEDI", url)

    def test_the_criteria_heading_marks_the_form(self):
        assert sedi._is_search_form("Insider transaction detail - Enter Search criteria ...")
        assert not sedi._is_search_form("Issuer name: Franco-Nevada Corporation ...")

    def wired_to(self, body: str) -> sedi.SediScraper:
        s = sedi.SediScraper()
        s._page = type("P", (), {"inner_text": lambda self, sel: body})()
        return s

    def test_still_on_the_form_after_search_raises(self):
        s = self.wired_to("Insider transaction detail - Enter Search criteria")
        with pytest.raises(sedi.BotBlocked):
            s._raise_if_not_submitted("FNV")

    def test_a_sedi_validation_error_is_left_to_the_normal_path(self):
        # SEDI re-shows the form with a banner for its own errors; that is a
        # real answer about the query, not a block.
        s = self.wired_to("Enter Search criteria  Error: Date cannot be future dated")
        s._raise_if_not_submitted("FNV")

    def test_a_report_page_passes(self):
        self.wired_to("Issuer name: Franco-Nevada Corporation")._raise_if_not_submitted("FNV")


class TestTheBrowserDoesNotLieAboutItsVersion:
    def test_no_user_agent_override_at_launch(self):
        # Claiming Chrome 124 from Chrome 155 makes the browser contradict its
        # own client hints, which bot walls notice.
        import inspect

        src = inspect.getsource(sedi.SediScraper._launch)
        assert "user_agent=" not in src.replace("No user_agent override", "")


class TestAClosedBrowserIsNamed:
    def test_opening_a_search_in_a_closed_browser_says_so(self):
        s = sedi.SediScraper()
        s._page = type("P", (), {"is_closed": lambda self: True})()
        with pytest.raises(sedi.BrowserGone):
            s._open_search()


class TestACompanyNamedDifferentlyOnSedi:
    """SEDI's 'starts with' is literal: the watchlist's "Metalla Royalty &
    Streaming" is SEDI's "Metalla Royalty and Streaming Ltd.", so the company
    came back NOT COVERED on every run."""

    def test_and_and_ampersand_are_both_tried_then_the_first_two_words(self):
        assert sedi.issuer_queries("Metalla Royalty & Streaming Ltd.") == [
            "Metalla Royalty & Streaming",
            "Metalla Royalty and Streaming",
            "Metalla Royalty",
            "Metalla",
        ]
        assert sedi.issuer_queries("Americas Gold and Silver Corporation") == [
            "Americas Gold and Silver",
            "Americas Gold & Silver",
            "Americas Gold",
            "Americas",
        ]

    def test_history_and_qualifiers_are_not_searched_for(self):
        assert sedi.issuer_queries("Fireweed Metals Corp. (formerly Fireweed Zinc Ltd.)")[0] == (
            "Fireweed Metals"
        )
        assert sedi.issuer_queries("International Petroleum Corp. (British Columbia)")[0] == (
            "International Petroleum"
        )
        assert sedi.issuer_queries("Emerita Resources Corp. formerly Emerita Gold Corp.")[0] == (
            "Emerita Resources"
        )

    def test_a_hyphen_in_another_place_falls_back_to_the_first_word(self):
        assert sedi.issuer_queries("Alimentation-Couche Tard, Inc.")[-1] == "Alimentation"
        assert sedi.same_listing_name(
            "Alimentation-Couche Tard, Inc.", "Alimentation Couche-Tard Inc."
        )

    def test_a_short_first_word_is_never_searched_alone(self):
        assert "Gold" not in sedi.issuer_queries("Gold Royalty Corp. Holdings Ltd")

    def test_a_short_name_is_tried_once(self):
        assert sedi.issuer_queries("Fortis Inc.") == ["Fortis"]

    def test_the_row_with_the_same_name_is_picked(self):
        rows = [
            "View 00001234 Metallic Minerals Corp.",
            "View 00005157 Metalla Royalty and Streaming Ltd.",
        ]
        assert sedi.pick_issuer_row(rows, "Metalla Royalty & Streaming Ltd.", strict=True) == 1

    def test_after_a_shortened_search_a_stranger_is_never_picked(self):
        rows = ["View 00001234 Metalla Holdings Inc."]
        assert sedi.pick_issuer_row(rows, "Metalla Royalty & Streaming Ltd.", strict=True) is None

    def test_with_the_full_name_the_first_row_is_still_a_safe_fallback(self):
        rows = ["View 00000001 Athabasca Oil Corporation (formerly X)"]
        assert sedi.pick_issuer_row(rows, "Athabasca Oil", strict=False) == 0

    def test_rows_under_sedis_spelling_keep_the_ticker(self):
        # Passed in as "&", reported by SEDI as "and": still the same company.
        assert sedi._same_issuer(
            "Metalla Royalty & Streaming Ltd.", "Metalla Royalty and Streaming Ltd."
        )
        assert not sedi._same_issuer("Metalla Royalty & Streaming Ltd.", "Metallic Minerals Corp.")


class TestWhichCompaniesSediIsAskedAbout:
    def test_a_us_board_is_not_canadian_whatever_the_country_says(self):
        # Nine US and Australian companies on one watchlist carried country
        # "CA", so every run searched SEDI for them.
        assert sedi.is_canadian({"exchange": "NYSE", "country": "CA"}) is False
        assert sedi.is_canadian({"exchange": "ASX", "country": "CA"}) is False

    def test_a_canadian_board_is_canadian(self):
        assert sedi.is_canadian({"exchange": "TSXV", "country": "US"}) is True

    def test_the_country_settles_an_unknown_board(self):
        assert sedi.is_canadian({"exchange": "", "country": "CA"}) is True
        assert sedi.is_canadian({"exchange": "XYZ"}) is False
