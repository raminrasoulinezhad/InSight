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

import contextlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .llm import Completion, LLMRouter, estimate_tokens

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

_VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")

# Room for a long list of companies. An 11k-token transcript of a wide-ranging
# Q&A mentioned well over a dozen, and a truncated reply loses the tail silently.
_MAX_OUTPUT = 4096

SYSTEM = (
    "You extract what was said about specific public companies in an investing "
    "interview. The transcript is auto-generated captions, so company names are "
    "often misheard: you correct them. You never invent a company that was not "
    "discussed. You reply with JSON only."
)

PROMPT = """Below is the transcript of an investing interview or Q&A.

Find every publicly traded company the speaker gives a VIEW ON or material \
information about, and summarise what they said.

THE TRANSCRIPT IS AUTO-GENERATED CAPTIONS. Company names in it are frequently \
misheard: "Kotekch" for CoTec, "Macango" for Mkango, "East Cole" for Eastcoal. \
Use the surrounding context (the exchange, the projects, the commodity, the \
people) to work out which company is meant, and put the CORRECT legal name in \
"name". Put the transcript's spelling in "heard_as" whenever the two differ, so \
a human can check you. If you genuinely cannot tell which company is meant, put \
the transcript's spelling in both. NEVER correct a name into a different \
company because it sounds similar: "Free Gold" is Freegold Ventures, not \
Freehold Royalties. When in doubt, keep what you heard.

EXCLUDE a company when it is only:
- background for a person ("our chairman used to run X"),
- a generic industry example ("the majors like X and Y"),
- named in passing with nothing said about it,
- the interviewer's own firm, channel, newsletter or conference.
If nothing was said about the company itself, leave it out.

BULLETS. This is the part that matters most:
- SHORT. One clause, ideally under 15 words. No sub-clauses, no "which means".
- One point per bullet. Split anything compound.
- Keep the speaker's own judgement words: "cheap", "superb execution", "a \
mistake", "I don't own it". Strip everything else.
- No speaker name inside the bullet: it is added later.
- No preamble, no "the speaker says".

Good:  "Execution superb, stock cheap even allowing for political risk."
Bad:   "The speaker notes that their execution has been superb and that the \
stock, even accounting for political risk, appears to be cheap."

If a ticker or exchange is stated, record it; otherwise leave those empty.

Reply with JSON of exactly this shape and nothing else:

{{"speaker": "the interviewee's full name, or empty",
  "companies": [
    {{"name": "correct legal name", "heard_as": "", "ticker": "", "exchange": "",
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


def fetch_title(vid: str) -> str:
    """The video's title, best effort.

    Uses YouTube's oEmbed endpoint rather than scraping the watch page: it is a
    documented JSON API, needs no key, and returns the title directly. Scraping
    the page for `<title>` worked from curl but not from urllib, which is the
    kind of difference not worth owning.

    Only a fallback for the speaker's name, so failure is silent.
    """
    import urllib.parse
    import urllib.request

    url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
        {"url": f"https://www.youtube.com/watch?v={video_id(vid)}", "format": "json"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return str(json.load(r).get("title") or "").strip()
    except Exception:
        return ""


def fetch_published(vid: str) -> str:
    """The video's publication date as YYYY-MM-DD, best effort.

    Read from the watch page, since oEmbed does not carry it. The date sits
    roughly 700 KB in, so the page has to be read whole rather than sampled.
    That is one 1.2 MB request per interview, which is cheap against a fetch
    that already pulls a transcript and runs an LLM.

    Worth the trouble because the date is what makes an old opinion legible: an
    unattributed "cheap" is noise a year later.
    """
    import urllib.request

    try:
        req = urllib.request.Request(
            f"https://www.youtube.com/watch?v={video_id(vid)}", headers={"User-Agent": _UA}
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            html = r.read().decode("utf-8", errors="replace")
        m = re.search(r'"uploadDate":"(\d{4}-\d{2}-\d{2})', html)
        return m.group(1) if m else ""
    except Exception:
        return ""


def pretty_date(iso: str) -> str:
    """`2026-05-02` as `May 2 2026`. Blank stays blank."""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%b %-d %Y")
    except (ValueError, TypeError):
        return ""


def speaker_from_title(title: str) -> str:
    """The interviewee's name out of a title.

    Only the shapes that are unambiguous: a leading "Name:" or "Name -", a
    leading possessive ("Rick Rule's 1,500% bet"), or a trailing "with Name".
    Guessing more loosely would put the wrong person's name on every bullet of
    an interview, which is worse than leaving it blank.
    """
    # Titles use a curly apostrophe as often as a straight one.
    t = (title or "").strip().replace("\u2019", "'")
    name = r"[A-Z][\w.'-]+(?: [A-Z][\w.'-]+){1,2}"
    for pattern in (rf"^({name})'s\b", rf"^({name})\s*[:|-]", rf"\bwith ({name})\b"):
        m = re.search(pattern, t)
        if m:
            return m.group(1).strip()
    return ""


def fetch_transcript(vid: str) -> str:
    """The video's caption track as one line of text.

    Imported lazily so the rest of InSight does not need the dependency just to
    start, and so a machine without it still runs everything else.

    An install that predates this dependency raises a bare ModuleNotFoundError,
    which tells the user nothing they can act on, so it is turned into the
    command that fixes it.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as e:
        raise RuntimeError(
            "the transcript reader is not installed in this copy of InSight. "
            "Update it with:  uv tool install --editable . --force  (from a "
            "checkout), or  uv tool upgrade insight"
        ) from e

    fetched = YouTubeTranscriptApi().fetch(video_id(vid))
    return " ".join(chunk.text for chunk in fetched).strip()


@dataclass
class Mention:
    """One company as an interview discussed it."""

    name: str
    applied: bool = False  # its bullets are already in the company's notes
    heard_as: str = ""  # the transcript's spelling, when captions mangled it
    ticker: str = ""
    exchange: str = ""
    bullets: list[str] = field(default_factory=list)
    on_watchlist: bool = False
    matched_name: str = ""  # the watchlist name this was matched to
    resolved: bool = False  # a real listing was found for a company we don't follow


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
    title: str = ""
    published: str = ""  # the video's own date, YYYY-MM-DD
    fetched: str = ""  # ISO timestamp of the run
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


def resolve_listing(mention: Mention) -> Mention:
    """Look a not-yet-followed company up in the issuer search.

    This is the same lookup behind the app's "Add a company by name" box, so a
    company proposed here can be added on exactly the terms the app already
    trusts. It also second-guesses the model: a name that resolves to a real
    listing is a name worth showing, and one that resolves to nothing is a hint
    that the captions beat us.
    """
    from . import issuers

    if mention.on_watchlist or not mention.name:
        return mention
    try:
        hits = issuers.search_issuers(mention.name, limit=5)
    except Exception:
        return mention  # offline, or the search is down: not a reason to fail
    for hit in hits:
        if _same_company(normalise(mention.name), normalise(str(hit.get("legal_name") or ""))):
            mention.resolved = True
            mention.name = str(hit.get("legal_name") or mention.name)
            # The search is the authority on the ticker. A model-supplied one is
            # a guess: it offered CTEK for CoTec, which is really CTH.
            mention.ticker = str(hit.get("ticker") or mention.ticker)
            mention.exchange = str(hit.get("exchange") or mention.exchange)
            return mention
    return mention


def extract(
    router: LLMRouter,
    transcript: str,
    *,
    watchlist: Sequence[dict[str, str]] | None = None,
    url: str = "",
    vid: str = "",
    title: str = "",
    published: str = "",
    resolve: bool = True,
) -> Extraction:
    """Run one transcript through the router and match the result to a watchlist."""
    completion: Completion = router.complete(
        PROMPT.format(transcript=transcript),
        system=SYSTEM,
        max_output=_MAX_OUTPUT,
    )
    data = _json_from(completion.text)
    mentions = []
    for row in data.get("companies") or []:
        m = Mention(
            name=str(row.get("name") or "").strip(),
            ticker=str(row.get("ticker") or "").strip(),
            exchange=str(row.get("exchange") or "").strip(),
            heard_as=str(row.get("heard_as") or "").strip(),
            bullets=[str(b).strip() for b in (row.get("bullets") or []) if str(b).strip()],
        )
        if not m.name:
            continue
        m = match_watchlist(m, watchlist or [])
        mentions.append(resolve_listing(m) if resolve else m)
    return Extraction(
        video_id=vid,
        url=url,
        # The transcript is the better source; the title is the fallback, since
        # an interview title nearly always names its guest.
        speaker=str(data.get("speaker") or "").strip() or speaker_from_title(title),
        title=title,
        published=published,
        fetched=datetime.now(UTC).isoformat(timespec="seconds"),
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
            f"SPEAKER {e.speaker or '(not identified)'}"
            + (f"   PUBLISHED {pretty_date(e.published)}" if e.published else ""),
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
        out += _render_group(known, e.speaker, e.published) or ["  (none)", ""]

        out.append(f"NOT ON YOUR WATCHLIST ({len(new)}) — would be proposed as additions")
        out.append("")
        out += _render_group(new, e.speaker, e.published) or ["  (none)", ""]

    total = sum(len(e.mentions) for e in extractions)
    listed = sum(1 for e in extractions for m in e.mentions if m.on_watchlist)
    out += [
        "=" * 78,
        f"TOTAL {total} companies discussed across {len(extractions)} video(s): "
        f"{listed} already followed, {total - listed} new.",
    ]
    return "\n".join(out) + "\n"


def note_bullets(mention: Mention, speaker: str, published: str = "") -> list[str]:
    """The bullets exactly as they would be appended to a company's notes.

    Every line carries who said it and when. Without the name a note reads as
    InSight's own view a month later; without the date an opinion cannot be
    weighed at all, since "cheap" in a different market is a different claim.
    An unknown date is left off rather than filled with the run date, which
    would quietly date the opinion to whenever the video happened to be read.

    The one function the app and the report share, so what you check in the
    report is exactly what gets written.
    """
    who = (speaker or "Unattributed").strip()
    when = pretty_date(published)
    tag = f"{who} - {when}" if when else who
    return [f"[{tag}] {b}" for b in mention.bullets]


def _render_group(mentions: list[Mention], speaker: str, published: str = "") -> list[str]:
    lines: list[str] = []
    for m in mentions:
        tag = " ".join(x for x in [m.exchange, m.ticker] if x)
        head = f"  {m.name}" + (f"  [{tag}]" if tag else "")
        if m.heard_as and normalise(m.heard_as) != normalise(m.name):
            head += f'  (heard as "{m.heard_as}")'
        if m.matched_name and normalise(m.matched_name) != normalise(m.name):
            head += f"  (watchlist: {m.matched_name})"
        elif not m.on_watchlist:
            head += "  [listing found]" if m.resolved else "  [no listing found]"
        lines.append(head)
        lines += [f"    - {b}" for b in note_bullets(m, speaker, published)]
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Saved interviews


def as_dict(e: Extraction) -> dict[str, Any]:
    """One extraction as stored JSON, with the note text already rendered.

    The bullets are frozen at extraction time rather than rebuilt on read, so
    what the user approved in the UI is exactly what later gets written into a
    company's notes, even if the prompt changes underneath.
    """
    return {
        "video_id": e.video_id,
        "url": e.url,
        "title": e.title,
        "speaker": e.speaker,
        "published": e.published,
        "fetched": e.fetched,
        "route": e.route,
        "model": e.model,
        "transcript_chars": e.transcript_chars,
        "companies": [
            {
                "name": m.name,
                "heard_as": m.heard_as,
                "ticker": m.ticker,
                "exchange": m.exchange,
                "on_watchlist": m.on_watchlist,
                "matched_name": m.matched_name,
                "resolved": m.resolved,
                "applied": m.applied,
                "bullets": note_bullets(m, e.speaker, e.published),
            }
            for m in e.mentions
        ],
    }


def load_saved(path: Path | None = None) -> list[dict[str, Any]]:
    """Every interview run so far, newest first. Unreadable file yields []."""
    from .paths import interviews_file

    try:
        raw = json.loads((path or interviews_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = raw.get("videos") if isinstance(raw, dict) else raw
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("video_id")]


def save_extraction(entry: dict[str, Any], path: Path | None = None) -> None:
    """Store one run, replacing any earlier run of the same video."""
    from .paths import interviews_file

    target = path or interviews_file()
    rows = [r for r in load_saved(target) if r.get("video_id") != entry.get("video_id")]
    rows.insert(0, entry)
    _write_json(target, {"videos": rows})


def forget(video_id: str, path: Path | None = None) -> bool:
    """Drop one interview. True when something was actually removed."""
    from .paths import interviews_file

    target = path or interviews_file()
    rows = load_saved(target)
    kept = [r for r in rows if r.get("video_id") != video_id]
    if len(kept) == len(rows):
        return False
    _write_json(target, {"videos": kept})
    return True


def mark_applied(video_id: str, company: str, path: Path | None = None) -> None:
    """Remember that a company's bullets went into the notes, so the UI can stop
    offering to add them twice."""
    from .paths import interviews_file

    target = path or interviews_file()
    rows = load_saved(target)
    for row in rows:
        if row.get("video_id") != video_id:
            continue
        for c in row.get("companies") or []:
            if c.get("name") == company:
                c["applied"] = True
    _write_json(target, {"videos": rows})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic replace, matching how notes.py guards its own file."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


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
        title = fetch_title(vid)
        published = fetch_published(vid)
        transcript = fetch_transcript(vid)
        print(
            f"{vid}: {len(transcript):,} chars "
            f"(~{estimate_tokens(transcript):,} tokens), extracting…"
        )
        e = extract(
            router,
            transcript,
            watchlist=watchlist,
            url=url,
            vid=vid,
            title=title,
            published=published,
        )
        print(f"{vid}: {len(e.mentions)} companies via {e.route} ({e.input_tokens:,} in)")
        extractions.append(e)

    out = Path(args.out)
    out.write_text(render_report(extractions), encoding="utf-8")
    print(f"\nWrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
