# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Progress reporting from a batch scrape.

A SEDI fetch is minutes of a browser window doing nothing this app can show, so
the progress bar is the only evidence it has not hung. That makes the callback
contract load-bearing: if it stalls, reports the wrong denominator, or takes the
scrape down with it, the user is worse off than with no bar at all.

No browser and no network — a fake scraper stands in for the session.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from insight import marketbeat
from insight.marketbeat import BotBlocked, CompanyDelisted, scrape_many
from insight.models import InsiderTransaction


@pytest.fixture(autouse=True)
def no_politeness_delay(monkeypatch):
    """scrape_many sleeps 1 s between requests; a 3-company test needn't."""
    monkeypatch.setattr(marketbeat.time, "sleep", lambda _s: None)


def txn(ticker: str = "ABC") -> InsiderTransaction:
    return InsiderTransaction(
        issuer_name=f"{ticker} Corp",
        exchange="TSE",
        ticker=ticker,
        insider_name="Jane Doe",
        insider_role="CEO",
        entity_type="individual",
        transaction_type="Buy",
        transaction_date="2026-06-01",
        shares=100,
        avg_price=10.0,
        total_value=1000.0,
        currency="CAD",
        source="test",
    )


def target(ticker: str, name: str | None = None) -> dict:
    return {"exchange": "TSE", "ticker": ticker, "name": name or f"{ticker} Corp"}


class FakeScraper:
    """A stand-in session. `fail_on` maps ticker -> exception to raise."""

    def __init__(self, fail_on: dict | None = None):
        self.fail_on = fail_on or {}
        self.fetched: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetch(self, exchange, ticker, name):
        self.fetched.append(ticker)
        if ticker in self.fail_on:
            raise self.fail_on[ticker]
        return [txn(ticker)]


def run(targets, *, fail_on=None, **kw) -> list[tuple[int, int, str]]:
    """Scrape and return everything the progress callback was told."""
    seen: list[tuple[int, int, str]] = []
    scrape_many(
        targets,
        scraper_factory=lambda: FakeScraper(fail_on),
        on_progress=lambda done, total, label: seen.append((done, total, label)),
        **kw,
    )
    return seen


class TestTheCount:
    def test_it_reports_once_per_company_plus_a_final_call(self):
        seen = run([target("A"), target("B"), target("C")])
        assert [(d, t) for d, t, _ in seen] == [(0, 3), (1, 3), (2, 3), (3, 3)]

    def test_it_ends_at_full(self):
        # The bar must reach 100%. Stopping at 2/3 reads as "gave up".
        done, total, _ = run([target("A"), target("B"), target("C")])[-1]
        assert (done, total) == (3, 3)

    def test_it_never_exceeds_the_total(self):
        for done, total, _ in run([target("A"), target("B")]):
            assert 0 <= done <= total

    def test_the_label_names_the_company_being_fetched_now(self):
        seen = run([target("A", "Alpha Ltd"), target("B", "Beta Inc")])
        assert [lbl for _, _, lbl in seen] == ["Alpha Ltd", "Beta Inc", ""]

    def test_a_nameless_target_falls_back_to_its_key(self):
        # Ad-hoc --tickers targets carry no name; a blank label would leave the
        # bar captioned with nothing.
        seen = run([{"exchange": "TSE", "ticker": "A"}])
        assert seen[0][2] == "TSE:A"


class TestTheDenominator:
    def test_cache_hits_are_not_counted(self, tmp_path: Path):
        # A discover run serves most companies from cache in milliseconds. If
        # they counted, the bar would jump to ~93% and then sit there for the
        # entire slow part — worse than no bar.
        marketbeat._write_cache(tmp_path, "TSE:CACHED", [txn("CACHED")])
        seen = run(
            [target("CACHED"), target("FRESH")],
            cache_dir=tmp_path,
            max_age_hours=12.0,
        )
        assert all(total == 1 for _, total, _ in seen), seen
        assert seen[0][2] == "FRESH Corp"

    def test_an_all_cached_run_still_reports(self, tmp_path: Path):
        # Otherwise the UI is left on its indeterminate bar with no work coming.
        marketbeat._write_cache(tmp_path, "TSE:A", [txn("A")])
        seen = run([target("A")], cache_dir=tmp_path, max_age_hours=12.0)
        assert seen == [(0, 0, "")]

    def test_no_targets_at_all_still_reports(self):
        assert run([]) == [(0, 0, "")]


class TestItNeverBreaksTheScrape:
    def test_a_failing_company_still_advances_the_bar(self):
        # A stalled bar is read as a hung app, so a company that blocks or is
        # delisted must still tick over.
        seen = run(
            [target("A"), target("B"), target("C")],
            fail_on={"B": BotBlocked("walled"), "C": CompanyDelisted("gone")},
        )
        assert [(d, t) for d, t, _ in seen] == [(0, 3), (1, 3), (2, 3), (3, 3)]

    def test_an_exploding_callback_is_swallowed(self):
        # Progress is decoration. A bug in the reporter must not cost the user
        # the scrape it was reporting on.
        def boom(done, total, label):
            raise RuntimeError("progress blew up")

        got = scrape_many(
            [target("A"), target("B")],
            scraper_factory=lambda: FakeScraper(),
            on_progress=boom,
        )
        assert sorted(got) == ["TSE:A", "TSE:B"]
        assert all(len(v) == 1 for v in got.values())

    def test_omitting_the_callback_is_fine(self):
        got = scrape_many([target("A")], scraper_factory=lambda: FakeScraper())
        assert list(got) == ["TSE:A"]
