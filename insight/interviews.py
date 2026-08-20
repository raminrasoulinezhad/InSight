# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Pull what an interview said about each company, as bullets.

Insider filings say what management *did*. Interviews say what people *think*,
which is the half InSight has never had. This module fetches a video's caption
track, asks an LLM which companies were discussed and what was said about each,
and writes it out as a report.

Two deliberate choices:

**The speaker's tone is kept, not flattened.** "I'd be a buyer under two dollars"
and "it's fine" are not the same claim, and a summary that turns every opinion
into neutral prose destroys the only thing an interview offers over a filing. So
the prompt asks for the speaker's stance and voice, in their words where it
matters.

**Nothing is written into the app.** This stage produces a `.txt` report and
stops, on purpose: the extraction has to be read by a human and judged before
anything of this shape is allowed near the notes a user relies on.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm import Completion, LLMRouter, estimate_tokens

_VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")

# Room for a long list of companies. An 11k-token transcript of a wide-ranging
# Q&A mentioned well over a dozen, and a truncated reply loses the tail silently.
_MAX_OUTPUT = 4096

SYSTEM = (
    "You extract what was said about specific public companies in an investing "
    "interview. You are precise, you never invent a company that was not "
    "discussed, and you preserve the speaker's own stance and phrasing rather "
    "than neutralising it. You reply with JSON only."
)

PROMPT = """Below is the transcript of an investing interview or Q&A.

Find every publicly traded company the speaker actually gives a VIEW ON or \
material information about. For each one, summarise what they said.

EXCLUDE a company when it is only:
- background for a person ("our chairman used to run X"),
- a generic industry example ("the majors like X and Y"),
- named in passing with nothing said about it,
- the interviewer's own firm, channel, newsletter or conference.
If nothing was said about the company itself, leave it out. A short list of real \
opinions is worth more than a long list of name-drops.

Rules that matter:
- Keep the speaker's tone and intent. If they are enthusiastic, sceptical, \
hedging, or warning, that must survive into the bullet. Use their own words for \
the parts that carry the opinion.
- One bullet per distinct point. Short. No preamble.
- Attribute each bullet to the speaker by their surname if you know it from the \
transcript, otherwise write "Speaker". Never use a name that does not appear in \
the transcript.
- If a ticker or exchange is stated in the transcript, record it. If not, leave \
those empty rather than guessing.
- stance is one of: bullish, cautious, bearish, neutral.
- If no company is genuinely discussed, return an empty list.

Reply with JSON of exactly this shape and nothing else:

{{"speaker": "who is being interviewed, or empty",
  "companies": [
    {{"name": "...", "ticker": "", "exchange": "",
      "stance": "bullish|cautious|bearish|neutral",
      "bullets": ["...", "..."]}}
  ]}}

TRANSCRIPT:
{transcript}
"""


def video_id(url_or_id: str) -> str:
    """The 11-character id from any YouTube URL shape, or the id itself."""
    s = (url_or_id or "").strip()
    m = _VIDEO_ID.search(s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    raise ValueError(f"not a YouTube video URL or id: {url_or_id!r}")


def fetch_transcript(vid: str) -> str:
    """The video's caption track as one line of text.

    Imported lazily so the rest of InSight does not need the dependency just to
    start, and so a machine without it still runs everything else.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    fetched = YouTubeTranscriptApi().fetch(video_id(vid))
    return " ".join(chunk.text for chunk in fetched).strip()


@dataclass
class Mention:
    """One company as an interview discussed it."""

    name: str
    ticker: str = ""
    exchange: str = ""
    stance: str = "neutral"
    bullets: list[str] = field(default_factory=list)
    on_watchlist: bool = False
    matched_name: str = ""  # the watchlist name this was matched to


@dataclass
class Extraction:
    """Everything one video produced."""

    video_id: str
    url: str
    speaker: str
    mentions: list[Mention]
    transcript_chars: int
    route: str
    model: str
    input_tokens: int
    output_tokens: int
    attempts: list[str] = field(default_factory=list)


def _json_from(text: str) -> dict[str, Any]:
    """Parse a model's reply, tolerating a ```json fence around it.

    Models wrap JSON in a code fence often enough that failing on it would mean
    throwing away a good answer over punctuation.
    """
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        parsed: Any = json.loads(s)
    except ValueError:
        m = re.search(r"\{.*\}", s, re.S)  # last resort: the outermost object
        if not m:
            raise
        parsed = json.loads(m.group(0))
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def normalise(name: str) -> str:
    """A company name reduced to what is stable about it.

    Only the legal suffixes come off. Commodity words stay, because in this
    sector they *are* the name: stripping "silver" from "Silver Mines" leaves
    "mines", which then matches half the watchlist.
    """
    s = (name or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(
        r"\b(inc|incorporated|corp|corporation|ltd|limited|llc|plc|company|co|"
        r"holdings|holding|sa|nv|ag)\b",
        " ",
        s,
    )
    return re.sub(r"\s+", " ", s).strip()


def _same_company(a: str, b: str) -> bool:
    """Whether two normalised names denote the same company.

    Substring containment was the first attempt and it was wrong: it matched
    "Royal Gold" to "Elemental Royalty" and "Silver Mines" to "West Red Lake
    Gold Mines", because the shorter name happened to appear inside the longer
    one. Comparing whole words, and insisting the names *start* the same, is
    what separates a shortened name from a coincidence.
    """
    x, y = a.split(), b.split()
    if not x or not y:
        return False
    if x == y:
        return True
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return short[0] == long_[0] and set(short) <= set(long_)


def load_watchlist(path: Path | None = None) -> list[dict[str, str]]:
    """The followed companies, as `scrape.load_targets` sees them."""
    from .paths import config_file

    try:
        raw = json.loads((path or config_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = raw.get("companies") if isinstance(raw, dict) else raw
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("name")]


def match_watchlist(mention: Mention, watchlist: Sequence[dict[str, str]]) -> Mention:
    """Decide whether this company is already followed.

    Ticker first when the transcript stated one, since a ticker is exact and a
    name is not. Then normalised name, then a containment check either way round
    so "Barrick" finds "Barrick Mining Corporation".
    """
    want_ticker = (mention.ticker or "").strip().upper()
    if want_ticker:
        for row in watchlist:
            if (row.get("ticker") or "").strip().upper() == want_ticker:
                mention.on_watchlist, mention.matched_name = True, row["name"]
                return mention

    key = normalise(mention.name)
    if not key:
        return mention
    for row in watchlist:
        other = normalise(row.get("name", ""))
        if other and _same_company(key, other):
            mention.on_watchlist, mention.matched_name = True, row["name"]
            return mention
    return mention


def extract(
    router: LLMRouter,
    transcript: str,
    *,
    watchlist: Sequence[dict[str, str]] | None = None,
    url: str = "",
    vid: str = "",
) -> Extraction:
    """Run one transcript through the router and match the result to a watchlist."""
    completion: Completion = router.complete(
        PROMPT.format(transcript=transcript), system=SYSTEM, max_output=_MAX_OUTPUT
    )
    data = _json_from(completion.text)
    mentions = []
    for row in data.get("companies") or []:
        m = Mention(
            name=str(row.get("name") or "").strip(),
            ticker=str(row.get("ticker") or "").strip(),
            exchange=str(row.get("exchange") or "").strip(),
            stance=str(row.get("stance") or "neutral").strip().lower(),
            bullets=[str(b).strip() for b in (row.get("bullets") or []) if str(b).strip()],
        )
        if m.name:
            mentions.append(match_watchlist(m, watchlist or []))
    return Extraction(
        video_id=vid,
        url=url,
        speaker=str(data.get("speaker") or "").strip(),
        mentions=mentions,
        transcript_chars=len(transcript),
        route=completion.route,
        model=completion.model,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        # Kept so the report can show which routes declined and why. Without it
        # a fallback is invisible, and "why did it not use the good model" has
        # no answer after the fact.
        attempts=[
            f"{a.route}: {a.outcome}" + (f" ({a.detail})" if a.detail else "")
            for a in completion.attempts
        ],
    )


def render_report(extractions: list[Extraction], when: datetime | None = None) -> str:
    """The whole run as plain text, grouped so a human can check it quickly.

    Watchlist companies come first because those are the ones with somewhere to
    go; the unlisted ones are the proposal, and are kept visibly separate so
    nobody mistakes a suggestion for a decision.
    """
    when = when or datetime.now(UTC)
    out: list[str] = [
        "InSight — interview extraction report",
        f"generated {when.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "This is a dry run. Nothing here has been written into the app.",
        "",
    ]

    for e in extractions:
        out += [
            "=" * 78,
            f"VIDEO   {e.url or e.video_id}",
            f"SPEAKER {e.speaker or '(not identified)'}",
            f"SOURCE  {e.transcript_chars:,} chars of transcript "
            f"(~{estimate_tokens('x' * e.transcript_chars):,} tokens)",
            f"MODEL   {e.route} / {e.model}"
            f"  ({e.input_tokens:,} in, {e.output_tokens:,} out tokens)",
        ]
        if len(e.attempts) > 1:  # something declined before this route took it
            out += ["ROUTING " + "; ".join(e.attempts)]
        out += ["=" * 78, ""]
        known = [m for m in e.mentions if m.on_watchlist]
        new = [m for m in e.mentions if not m.on_watchlist]

        out.append(f"ON YOUR WATCHLIST ({len(known)})")
        out.append("")
        out += _render_group(known) or ["  (none)", ""]

        out.append(f"NOT ON YOUR WATCHLIST ({len(new)}) — would be proposed as additions")
        out.append("")
        out += _render_group(new) or ["  (none)", ""]

    total = sum(len(e.mentions) for e in extractions)
    listed = sum(1 for e in extractions for m in e.mentions if m.on_watchlist)
    out += [
        "=" * 78,
        f"TOTAL {total} companies discussed across {len(extractions)} video(s): "
        f"{listed} already followed, {total - listed} new.",
    ]
    return "\n".join(out) + "\n"


def _render_group(mentions: list[Mention]) -> list[str]:
    lines: list[str] = []
    for m in mentions:
        tag = " ".join(x for x in [m.exchange, m.ticker] if x)
        head = f"  {m.name}" + (f"  [{tag}]" if tag else "")
        if m.matched_name and normalise(m.matched_name) != normalise(m.name):
            head += f"  (watchlist: {m.matched_name})"
        lines.append(head)
        lines.append(f"    stance: {m.stance}")
        lines += [f"    - {b}" for b in m.bullets]
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# The dry run


def main(argv: list[str] | None = None) -> int:
    """Fetch, extract, and write a report. Writes nothing into the app.

    python -m insight.interviews URL [URL ...] -o report.txt
    """
    import argparse

    from .llm import default_router

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("urls", nargs="+", help="YouTube video URLs or ids")
    ap.add_argument("-o", "--out", default="interview-report.txt", help="report file to write")
    ap.add_argument("--config", help="watchlist JSON (default: the app's)")
    args = ap.parse_args(argv)

    router = default_router()
    if not router.routes:
        print("No LLM routes configured. Copy .env.example to .env and add a key.")
        return 2
    print(f"Routes: {', '.join(r.name for r in router.routes)}")

    watchlist = load_watchlist(Path(args.config) if args.config else None)
    print(f"Watchlist: {len(watchlist)} companies")

    extractions: list[Extraction] = []
    for url in args.urls:
        vid = video_id(url)
        print(f"\n{vid}: fetching transcript…")
        transcript = fetch_transcript(vid)
        print(
            f"{vid}: {len(transcript):,} chars "
            f"(~{estimate_tokens(transcript):,} tokens), extracting…"
        )
        e = extract(router, transcript, watchlist=watchlist, url=url, vid=vid)
        print(f"{vid}: {len(e.mentions)} companies via {e.route} ({e.input_tokens:,} in)")
        extractions.append(e)

    out = Path(args.out)
    out.write_text(render_report(extractions), encoding="utf-8")
    print(f"\nWrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
