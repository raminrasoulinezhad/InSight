# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""The LLM router: choosing a route, respecting limits, falling through.

Every test drives a fake transport and a fake clock, so nothing here touches the
network or the wall clock.
"""

from __future__ import annotations

import json

import pytest

from insight import llm


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeTransport:
    """Answers each route by name, and records who was asked in what order."""

    def __init__(self, replies: dict[str, tuple[int, dict, dict | str]]):
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, body, timeout):
        sent = json.loads(body)
        self.calls.append((sent["model"], sent))
        status, hdrs, payload = self.replies[sent["model"]]
        if isinstance(payload, str):
            return status, hdrs, payload.encode()
        return status, hdrs, json.dumps(payload).encode()

    @property
    def models(self) -> list[str]:
        return [m for m, _ in self.calls]


def ok(text: str = "hello", prompt_tokens: int = 100, total_tokens: int = 150):
    return (
        200,
        {},
        {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": total_tokens},
        },
    )


def route(name: str, model: str, priority: int = 100, **kw) -> llm.Route:
    return llm.Route(
        name=name, base_url="https://x/v1", api_key="k", model=model, priority=priority, **kw
    )


def build(routes, replies, clock=None):
    clock = clock or FakeClock()
    transport = FakeTransport(replies)
    r = llm.LLMRouter(routes, ledger=llm.Ledger(None, clock), transport=transport, now=clock)
    return r, transport, clock


class TestEstimating:
    def test_it_counts_about_four_characters_to_the_token(self):
        assert llm.estimate_tokens("a" * 400) == 100

    def test_it_rounds_up_rather_than_down(self):
        # Guessing low is what gets a call rejected by the provider mid-batch.
        assert llm.estimate_tokens("abc") == 1
        assert llm.estimate_tokens("a" * 401) == 101

    def test_empty_text_still_costs_something(self):
        assert llm.estimate_tokens("") == 1

    def test_a_real_transcript_lands_near_the_measured_size(self):
        # 17,952 chars of a real interview transcript measured ~4,500 tokens.
        assert 4000 <= llm.estimate_tokens("x" * 17952) <= 5000


class TestChoosingARoute:
    def test_the_lowest_priority_number_goes_first(self):
        r, t, _ = build(
            [route("slow", "m-slow", priority=90), route("fast", "m-fast", priority=10)],
            {"m-fast": ok(), "m-slow": ok()},
        )
        assert r.complete("hi").route == "fast"
        assert t.models == ["m-fast"]

    def test_a_prompt_too_big_for_the_context_skips_that_route(self):
        r, t, _ = build(
            [
                route("small", "m-small", priority=10, context=100),
                route("big", "m-big", priority=20),
            ],
            {"m-big": ok()},
        )
        out = r.complete("x" * 4000, max_output=50)
        assert out.route == "big"
        assert t.models == ["m-big"]  # the small one was never called
        assert any(a.route == "small" and a.outcome == "skipped" for a in out.attempts)

    def test_output_room_counts_towards_the_context_too(self):
        # 400 chars is ~100 input tokens; asking for 500 output will not fit 300.
        r, _t, _ = build(
            [
                route("tight", "m-tight", priority=10, context=300),
                route("roomy", "m-roomy", priority=20),
            ],
            {"m-roomy": ok()},
        )
        assert r.complete("x" * 400, max_output=500).route == "roomy"

    def test_it_never_asks_for_more_output_than_the_route_allows(self):
        r, t, _ = build([route("a", "m-a", max_output=256)], {"m-a": ok()})
        r.complete("hi", max_output=4096)
        assert t.calls[0][1]["max_tokens"] == 256


class TestRateLimits:
    def test_a_route_at_its_requests_per_minute_is_passed_over(self):
        clock = FakeClock()
        r, _t, _ = build(
            [
                route("capped", "m-capped", priority=10, rpm=2),
                route("spare", "m-spare", priority=20),
            ],
            {"m-capped": ok(), "m-spare": ok()},
            clock,
        )
        assert [r.complete("hi").route for _ in range(3)] == ["capped", "capped", "spare"]

    def test_the_per_minute_window_slides(self):
        clock = FakeClock()
        r, _t, _ = build(
            [
                route("capped", "m-capped", priority=10, rpm=1),
                route("spare", "m-spare", priority=20),
            ],
            {"m-capped": ok(), "m-spare": ok()},
            clock,
        )
        assert r.complete("hi").route == "capped"
        assert r.complete("hi").route == "spare"
        clock.advance(61)
        assert r.complete("hi").route == "capped"

    def test_input_tokens_per_minute_is_checked_before_sending(self):
        r, _t, _ = build(
            [
                route("tight", "m-tight", priority=10, input_tpm=200),
                route("spare", "m-spare", priority=20),
            ],
            {"m-tight": ok(prompt_tokens=150, total_tokens=200), "m-spare": ok()},
            FakeClock(),
        )
        assert r.complete("x" * 400).route == "tight"  # 150 recorded
        # Another ~100-token prompt would reach 250 of 200, so it moves on.
        assert r.complete("x" * 400).route == "spare"

    def test_the_daily_budget_is_enforced_and_resets_on_the_next_utc_day(self):
        clock = FakeClock()
        r, _t, _ = build(
            [
                route("budget", "m-budget", priority=10, daily_tokens=300),
                route("spare", "m-spare", priority=20),
            ],
            {"m-budget": ok(total_tokens=250), "m-spare": ok()},
            clock,
        )
        assert r.complete("hi", max_output=50).route == "budget"
        # 250 already spent, plus this call's estimate, would pass 300.
        assert r.complete("hi", max_output=50).route == "spare"
        clock.advance(60 * 60 * 24)
        assert r.complete("hi", max_output=50).route == "budget"

    def test_the_monthly_budget_is_enforced_separately(self):
        r, _t, _ = build(
            [
                route("month", "m-month", priority=10, monthly_tokens=300),
                route("spare", "m-spare", priority=20),
            ],
            {"m-month": ok(total_tokens=250), "m-spare": ok()},
            FakeClock(),
        )
        assert r.complete("hi", max_output=50).route == "month"
        assert r.complete("hi", max_output=50).route == "spare"

    def test_an_undeclared_limit_is_not_enforced(self):
        # Blank means "unknown", not "zero" — the provider's 429 is the backstop.
        r, _t, _ = build([route("open", "m-open")], {"m-open": ok()}, FakeClock())
        assert [r.complete("hi").route for _ in range(5)] == ["open"] * 5


class TestFallingThrough:
    def test_a_429_moves_to_the_next_route(self):
        r, t, _ = build(
            [route("first", "m-first", priority=10), route("second", "m-second", priority=20)],
            {"m-first": (429, {}, {"error": {"message": "slow down"}}), "m-second": ok()},
        )
        out = r.complete("hi")
        assert out.route == "second"
        assert t.models == ["m-first", "m-second"]

    def test_a_rate_limited_route_sits_out_the_cooldown_then_returns(self):
        clock = FakeClock()
        replies = {"m-first": (429, {"retry-after": "30"}, {}), "m-second": ok()}
        r, t, _ = build(
            [route("first", "m-first", priority=10), route("second", "m-second", priority=20)],
            replies,
            clock,
        )
        r.complete("hi")
        t.calls.clear()
        r.complete("hi")
        assert t.models == ["m-second"]  # still cooling: not retried at all
        clock.advance(31)
        replies["m-first"] = ok()
        t.calls.clear()
        assert r.complete("hi").route == "first"

    def test_a_payment_error_takes_the_route_out_for_good(self):
        # Cerebras answered 402 on every model. Retrying it each call would cost
        # a round trip forever and never once succeed.
        clock = FakeClock()
        r, t, _ = build(
            [route("unpaid", "m-unpaid", priority=10), route("free", "m-free", priority=20)],
            {"m-unpaid": (402, {}, {"message": "Payment required"}), "m-free": ok()},
            clock,
        )
        r.complete("hi")
        t.calls.clear()
        clock.advance(10_000)
        r.complete("hi")
        assert t.models == ["m-free"]

    def test_a_transport_failure_is_one_routes_problem_not_the_calls(self):
        def boom(url, headers, body, timeout):
            if json.loads(body)["model"] == "m-down":
                raise OSError("connection reset")
            return 200, {}, json.dumps(ok()[2]).encode()

        clock = FakeClock()
        r = llm.LLMRouter(
            [route("down", "m-down", priority=10), route("up", "m-up", priority=20)],
            ledger=llm.Ledger(None, clock),
            transport=boom,
            now=clock,
        )
        assert r.complete("hi").route == "up"

    def test_when_everything_refuses_it_says_what_each_one_said(self):
        r, _t, _ = build(
            [route("a", "m-a", priority=10), route("b", "m-b", priority=20)],
            {"m-a": (429, {}, {"error": {"message": "too fast"}}), "m-b": (500, {}, "boom")},
        )
        with pytest.raises(llm.NoRouteAvailable) as e:
            r.complete("hi")
        assert "too fast" in str(e.value)
        assert "a:" in str(e.value) and "b:" in str(e.value)

    def test_the_attempt_trail_records_the_route_that_worked(self):
        r, _t, _ = build(
            [route("first", "m-first", priority=10), route("second", "m-second", priority=20)],
            {"m-first": (429, {}, {}), "m-second": ok()},
        )
        out = r.complete("hi")
        assert [(a.route, a.outcome) for a in out.attempts] == [
            ("first", "error"),
            ("second", "ok"),
        ]


class TestAccounting:
    def test_the_providers_own_token_count_wins_over_the_estimate(self):
        clock = FakeClock()
        r, _t, _ = build(
            [route("a", "m-a")], {"m-a": ok(prompt_tokens=999, total_tokens=1234)}, clock
        )
        out = r.complete("hi")
        assert (out.input_tokens, out.output_tokens) == (999, 235)
        assert r.ledger.spent("a")["day_tokens"] == 1234

    def test_spend_survives_a_restart(self, tmp_path):
        clock = FakeClock()
        path = tmp_path / "usage.json"
        for _ in range(2):
            led = llm.Ledger(path, clock)
            llm.LLMRouter(
                [route("a", "m-a")],
                ledger=led,
                transport=FakeTransport({"m-a": ok(total_tokens=100)}),
                now=clock,
            ).complete("hi")
        assert llm.Ledger(path, clock).spent("a")["day_tokens"] == 200

    def test_a_corrupt_ledger_costs_accuracy_not_the_run(self, tmp_path):
        path = tmp_path / "usage.json"
        path.write_text("{not json")
        clock = FakeClock()
        r = llm.LLMRouter(
            [route("a", "m-a")],
            ledger=llm.Ledger(path, clock),
            transport=FakeTransport({"m-a": ok()}),
            now=clock,
        )
        assert r.complete("hi").route == "a"


class TestConfigFromEnv:
    def test_a_key_written_once_is_expanded_into_each_route(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text(
            "MY_KEY=secret-value\n"
            "INSIGHT_LLM_ROUTES=one\n"
            "INSIGHT_LLM_ONE_KEY=${MY_KEY}\n"
            "INSIGHT_LLM_ONE_BASE_URL=https://x/v1\n"
            "INSIGHT_LLM_ONE_MODEL=m\n"
        )
        routes = llm.routes_from_env(llm.load_dotenv(p, environ={}))
        assert [r.api_key for r in routes] == ["secret-value"]

    def test_comments_blank_lines_and_quotes_are_handled(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text('\n# a comment\nA="quoted"\n\nB=plain\n')
        assert llm.load_dotenv(p, environ={}) == {"A": "quoted", "B": "plain"}

    def test_limits_are_parsed_including_underscores_and_commas(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text(
            "INSIGHT_LLM_ROUTES=one\n"
            "INSIGHT_LLM_ONE_KEY=k\n"
            "INSIGHT_LLM_ONE_BASE_URL=https://x/v1\n"
            "INSIGHT_LLM_ONE_MODEL=m\n"
            "INSIGHT_LLM_ONE_RPM=5\n"
            "INSIGHT_LLM_ONE_DAILY_TOKENS=1_000_000\n"
            "INSIGHT_LLM_ONE_INPUT_TPM=30,000\n"
        )
        r = llm.routes_from_env(llm.load_dotenv(p, environ={}))[0]
        assert (r.rpm, r.daily_tokens, r.input_tpm) == (5, 1_000_000, 30_000)

    def test_a_route_missing_its_key_is_dropped_not_fatal(self, tmp_path):
        # Half a pool still works; crashing on one unused provider would not.
        p = tmp_path / ".env"
        p.write_text(
            "INSIGHT_LLM_ROUTES=good,bad\n"
            "INSIGHT_LLM_GOOD_KEY=k\n"
            "INSIGHT_LLM_GOOD_BASE_URL=https://x/v1\n"
            "INSIGHT_LLM_GOOD_MODEL=m\n"
            "INSIGHT_LLM_BAD_BASE_URL=https://y/v1\n"
            "INSIGHT_LLM_BAD_MODEL=m2\n"
        )
        assert [r.name for r in llm.routes_from_env(llm.load_dotenv(p, environ={}))] == ["good"]

    def test_the_url_is_built_from_the_base(self):
        assert route("a", "m").url == "https://x/v1/chat/completions"


class TestTheExampleFileCarriesNoSecrets:
    """The one file of this pair that gets committed must never hold a key."""

    def test_env_example_has_placeholders_only(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
        for marker in ("gsk_", "csk-", "AIzaSy", "sk-ant", "sk-EqX"):
            assert marker not in text, f"{marker} looks like a real key in .env.example"
        assert "your-key-here" in text

    def test_dotenv_is_gitignored(self):
        from pathlib import Path

        ignored = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
        assert "\n.env\n" in "\n" + ignored
