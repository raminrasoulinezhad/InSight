# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Route one LLM call across several free-tier keys.

The premise is a handful of keys from different companies, each free, each
capped differently: one allows many requests but few tokens a minute, another
allows a million tokens a day but only a few requests a minute. No single one is
"best" — the right one depends on how big *this* call is and on what has already
been spent today. So the caller says what it wants and the router decides.

Three jobs, in order:

1. **Estimate.** Size the prompt and the expected reply before sending, because
   every limit that matters (context window, input tokens per minute, daily
   budget) is checked against a number we only know in advance by estimating.
2. **Choose.** Drop routes that cannot fit the call or have no headroom left,
   then take the cheapest survivor by declared priority.
3. **Fall through.** A 429 or an outage moves to the next candidate rather than
   failing the call. Only when every route is exhausted does it raise.

Spend is written to disk, so limits survive a restart: a daily budget is useless
if it resets every time the process does. All providers here speak the
OpenAI-compatible `/chat/completions` shape, so one client covers them and a new
provider is a config line rather than code.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import app_dir

# A token is about four characters of English. Measured against a real 16.7 min
# interview transcript: 17,952 characters, ~4,500 tokens. Deliberately a slight
# over-estimate, since the cost of guessing high is picking a roomier route and
# the cost of guessing low is a 400 from the provider mid-batch.
_CHARS_PER_TOKEN = 4

# What a route is assumed to allow when its .env line does not say. Generous on
# purpose: an unknown limit should not silently sideline an otherwise good route,
# and the provider's own 429 is the backstop.
_DEFAULT_CONTEXT = 32_000
_DEFAULT_MAX_OUTPUT = 4_096

# How long a route sits out after each kind of refusal.
_COOLDOWN_RATE_LIMIT = 60.0  # a 429 with no Retry-After
_COOLDOWN_SERVER = 30.0  # 5xx or a transport error


class NoRouteAvailable(RuntimeError):
    """Every route was either unusable for this call or refused it."""


def estimate_tokens(text: str) -> int:
    """Rough token count for `text`, erring high. See `_CHARS_PER_TOKEN`."""
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


@dataclass(frozen=True)
class Route:
    """One provider+model pair, with the limits its free tier imposes.

    A limit left as None means "not declared", and is not enforced locally: the
    provider's own refusal handles it. Declared limits are enforced before
    sending, which is the difference between choosing a route and hoping.
    """

    name: str
    base_url: str
    api_key: str
    model: str
    priority: int = 100  # lower goes first
    rpm: int | None = None  # requests per minute
    input_tpm: int | None = None  # input tokens per minute
    daily_tokens: int | None = None  # total tokens per UTC day
    monthly_tokens: int | None = None  # total tokens per calendar month
    context: int = _DEFAULT_CONTEXT  # input + output must fit
    max_output: int = _DEFAULT_MAX_OUTPUT

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


@dataclass
class Attempt:
    """One route's turn: what happened, and whether it was even tried."""

    route: str
    outcome: str  # "ok" | "skipped" | "error"
    detail: str = ""


@dataclass
class Completion:
    """A successful reply, plus the trail of what it took to get one."""

    text: str
    route: str
    model: str
    input_tokens: int
    output_tokens: int
    attempts: list[Attempt] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Spend tracking


class Ledger:
    """What each route has spent, across process restarts.

    Two shapes of limit need two shapes of memory. Per-minute limits need the
    individual call timestamps, since the window slides; per-day and per-month
    limits need only a counter and the period it belongs to. Keeping both means
    a restart mid-day cannot hand back a fresh daily budget.
    """

    def __init__(self, path: Path | None = None, now: Callable[[], float] = time.time):
        self._path = path
        self._now = now
        self._calls: dict[str, list[tuple[float, int, int]]] = {}  # ts, input, total
        self._periods: dict[str, dict[str, Any]] = {}
        self._cooldowns: dict[str, float] = {}
        self._disabled: dict[str, str] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return  # a corrupt ledger should cost budget accuracy, not the run
        self._calls = {k: [tuple(c) for c in v] for k, v in raw.get("calls", {}).items()}
        self._periods = raw.get("periods", {})

    def save(self) -> None:
        if not self._path:
            return
        cutoff = self._now() - 60
        self._calls = {k: [c for c in v if c[0] >= cutoff] for k, v in self._calls.items()}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"calls": self._calls, "periods": self._periods}), encoding="utf-8"
            )
        except Exception:
            pass  # never lose a completion over a ledger write

    # -- windows -----------------------------------------------------------
    def _recent(self, name: str) -> list[tuple[float, int, int]]:
        cutoff = self._now() - 60
        rows = [c for c in self._calls.get(name, []) if c[0] >= cutoff]
        self._calls[name] = rows
        return rows

    def _stamps(self) -> tuple[str, str]:
        d = datetime.fromtimestamp(self._now(), UTC)
        return d.strftime("%Y-%m-%d"), d.strftime("%Y-%m")

    def _period(self, name: str) -> dict[str, Any]:
        day, month = self._stamps()
        p = self._periods.setdefault(
            name, {"day": day, "day_tokens": 0, "month": month, "month_tokens": 0}
        )
        if p.get("day") != day:  # a new UTC day hands the budget back
            p["day"], p["day_tokens"] = day, 0
        if p.get("month") != month:
            p["month"], p["month_tokens"] = month, 0
        return p

    # -- recording ---------------------------------------------------------
    def record(self, name: str, input_tokens: int, total_tokens: int) -> None:
        self._calls.setdefault(name, []).append((self._now(), input_tokens, total_tokens))
        p = self._period(name)
        p["day_tokens"] += total_tokens
        p["month_tokens"] += total_tokens

    def cool_down(self, name: str, seconds: float) -> None:
        self._cooldowns[name] = self._now() + seconds

    def disable(self, name: str, why: str) -> None:
        """Take a route out for the rest of the process. For refusals that will
        not fix themselves: an unpaid account, a model the key cannot see."""
        self._disabled[name] = why

    # -- questions ---------------------------------------------------------
    def blocked(self, route: Route, input_tokens: int, total_tokens: int) -> str:
        """Why `route` cannot take this call right now, or "" if it can."""
        if route.name in self._disabled:
            return self._disabled[route.name]
        until = self._cooldowns.get(route.name, 0.0)
        if until > self._now():
            return f"cooling down for {until - self._now():.0f}s"

        recent = self._recent(route.name)
        if route.rpm is not None and len(recent) >= route.rpm:
            return f"at {route.rpm} requests/min"
        if route.input_tpm is not None:
            spent = sum(c[1] for c in recent)
            if spent + input_tokens > route.input_tpm:
                return f"input tokens/min would reach {spent + input_tokens} of {route.input_tpm}"

        p = self._period(route.name)
        day, month = int(p["day_tokens"]), int(p["month_tokens"])
        if route.daily_tokens is not None and day + total_tokens > route.daily_tokens:
            return f"daily budget would reach {day + total_tokens} of {route.daily_tokens}"
        if route.monthly_tokens is not None and month + total_tokens > route.monthly_tokens:
            return f"monthly budget would reach {month + total_tokens} of {route.monthly_tokens}"
        return ""

    def spent(self, name: str) -> dict[str, int]:
        p = self._period(name)
        return {"day_tokens": int(p["day_tokens"]), "month_tokens": int(p["month_tokens"])}


# ---------------------------------------------------------------------------
# Transport


def _http_post(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


# ---------------------------------------------------------------------------
# The router


class LLMRouter:
    """Picks a route per call, and moves on when one refuses."""

    def __init__(
        self,
        routes: Sequence[Route],
        ledger: Ledger | None = None,
        transport: Callable[..., tuple[int, dict[str, str], bytes]] = _http_post,
        now: Callable[[], float] = time.time,
        timeout: float = 180.0,
    ):
        self.routes = list(routes)
        self.ledger = ledger if ledger is not None else Ledger(usage_file(), now)
        self._transport = transport
        self._now = now
        self._timeout = timeout

    # -- choosing ----------------------------------------------------------
    def candidates(
        self, input_tokens: int, output_tokens: int
    ) -> tuple[list[Route], list[Attempt]]:
        """Routes that can take this call, best first, plus why the rest can't.

        The skipped list is returned rather than logged away: when every route
        declines, that list *is* the error message.
        """
        usable: list[Route] = []
        skipped: list[Attempt] = []
        total = input_tokens + output_tokens
        for r in sorted(self.routes, key=lambda r: (r.priority, r.name)):
            if input_tokens + min(output_tokens, r.max_output) > r.context:
                skipped.append(
                    Attempt(r.name, "skipped", f"needs {total} tokens, context is {r.context}")
                )
                continue
            why = self.ledger.blocked(r, input_tokens, total)
            if why:
                skipped.append(Attempt(r.name, "skipped", why))
                continue
            usable.append(r)
        return usable, skipped

    # -- calling -----------------------------------------------------------
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_output: int = 1024,
        temperature: float = 0.2,
    ) -> Completion:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        input_tokens = estimate_tokens((system or "") + prompt)
        usable, attempts = self.candidates(input_tokens, max_output)

        for route in usable:
            want = min(max_output, route.max_output)
            status, headers, raw = self._send(route, messages, want, temperature)
            if status == 200:
                try:
                    data = json.loads(raw)
                    text = (data["choices"][0]["message"].get("content") or "").strip()
                except Exception as e:
                    attempts.append(Attempt(route.name, "error", f"unreadable reply: {e}"))
                    self.ledger.cool_down(route.name, _COOLDOWN_SERVER)
                    continue
                usage = data.get("usage") or {}
                # Prefer the provider's own count; the estimate is only a stand-in.
                used_in = int(usage.get("prompt_tokens") or input_tokens)
                used_total = int(usage.get("total_tokens") or used_in + estimate_tokens(text))
                self.ledger.record(route.name, used_in, used_total)
                self.ledger.save()
                attempts.append(Attempt(route.name, "ok"))
                return Completion(
                    text=text,
                    route=route.name,
                    model=route.model,
                    input_tokens=used_in,
                    output_tokens=used_total - used_in,
                    attempts=attempts,
                )

            detail = _explain(status, raw)
            attempts.append(Attempt(route.name, "error", detail))
            if status == 429:
                self.ledger.cool_down(route.name, _retry_after(headers, _COOLDOWN_RATE_LIMIT))
            elif status in (401, 402, 403, 404):
                # Unpaid, revoked, or a model this key cannot see. Retrying costs
                # a round trip every call for the rest of the run and never works.
                self.ledger.disable(route.name, detail)
            else:
                self.ledger.cool_down(route.name, _COOLDOWN_SERVER)

        self.ledger.save()
        raise NoRouteAvailable(_no_route_message(input_tokens, max_output, attempts))

    def _send(
        self, route: Route, messages: list[dict[str, str]], max_output: int, temperature: float
    ) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(
            {
                "model": route.model,
                "messages": messages,
                "max_tokens": max_output,
                "temperature": temperature,
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {route.api_key}",
            "Content-Type": "application/json",
        }
        try:
            return self._transport(route.url, headers, body, self._timeout)
        except Exception as e:  # a dead host is one route's problem, not the call's
            return 0, {}, str(e).encode("utf-8")


def _retry_after(headers: dict[str, str], default: float) -> float:
    raw = headers.get("retry-after") or ""
    try:
        return max(1.0, float(raw))
    except ValueError:
        return default


def _explain(status: int, raw: bytes) -> str:
    """A one-line reason from a provider error body, which is nested JSON in
    some providers and flat in others."""
    text = raw.decode("utf-8", errors="replace")[:400]
    try:
        data = json.loads(text)
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else None
        if msg:
            return f"HTTP {status}: {str(msg)[:200]}"
    except Exception:
        pass
    return f"HTTP {status}: {re.sub(r'\\s+', ' ', text)[:200]}"


def _no_route_message(input_tokens: int, max_output: int, attempts: Iterable[Attempt]) -> str:
    lines = [f"no route could serve ~{input_tokens} in / {max_output} out tokens:"]
    lines += [f"  {a.route}: {a.detail}" for a in attempts]
    return "\n".join(lines)


def usage_file() -> Path:
    """Where spend is remembered between runs. Beside the other app state, not
    in the repo: it is per-machine bookkeeping, not configuration."""
    return app_dir() / "llm_usage.json"


# ---------------------------------------------------------------------------
# Configuration


def load_dotenv(path: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Read a .env into a dict, expanding `${OTHER}` against what came before.

    Deliberately tiny and dependency-free. The expansion exists so one provider
    key can be written once at the top and referenced by each of its routes,
    which is what keeps the file honest: a key appears exactly once.
    """
    env = dict(environ if environ is not None else os.environ)
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        value = re.sub(
            r"\$\{(\w+)\}", lambda m: env.get(m.group(1), out.get(m.group(1), "")), value
        )
        out[key] = value
        env[key] = value
    return out


def _int(env: dict[str, str], key: str) -> int | None:
    raw = (env.get(key) or "").strip()
    if not raw:
        return None
    try:
        return int(raw.replace("_", "").replace(",", ""))
    except ValueError:
        return None


def routes_from_env(env: dict[str, str]) -> list[Route]:
    """Build the pool from `INSIGHT_LLM_ROUTES` and one block per name.

    A route missing its key or model is dropped rather than raising: half a pool
    still works, and a startup crash over one unused provider would be worse
    than a shorter list.
    """
    names = [n.strip() for n in (env.get("INSIGHT_LLM_ROUTES") or "").split(",") if n.strip()]
    routes: list[Route] = []
    for name in names:
        p = f"INSIGHT_LLM_{name.upper()}_"
        key, model = (env.get(p + "KEY") or "").strip(), (env.get(p + "MODEL") or "").strip()
        base = (env.get(p + "BASE_URL") or "").strip()
        if not (key and model and base):
            continue
        routes.append(
            Route(
                name=name,
                base_url=base,
                api_key=key,
                model=model,
                priority=_int(env, p + "PRIORITY") or 100,
                rpm=_int(env, p + "RPM"),
                input_tpm=_int(env, p + "INPUT_TPM"),
                daily_tokens=_int(env, p + "DAILY_TOKENS"),
                monthly_tokens=_int(env, p + "MONTHLY_TOKENS"),
                context=_int(env, p + "CONTEXT") or _DEFAULT_CONTEXT,
                max_output=_int(env, p + "MAX_OUTPUT") or _DEFAULT_MAX_OUTPUT,
            )
        )
    return routes


def default_router(env_path: Path | None = None) -> LLMRouter:
    """The pool described by `.env` in the repo root, falling back to the
    process environment so a deployment can set the same names directly."""
    path = env_path or Path(__file__).resolve().parent.parent / ".env"
    env = {**os.environ, **load_dotenv(path)}
    return LLMRouter(routes_from_env(env))
