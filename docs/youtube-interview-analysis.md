# Plan: mine YouTube interviews for insight on watchlist companies

_Status: **planned, agreed to build**. Feasibility probed live 2026-08-18 (every number
below was measured on this machine, not quoted from a blog). Two design decisions are
still open (see below); phase 1 does not depend on either._

## The idea

A handful of channels publish CEO interviews about small-cap resource companies several
times a week, and those interviews routinely cover the exact names on an InSight
watchlist. Nothing in the app looks at them today.

Proposal: on a schedule (daily or weekly), find interviews published since the last run,
fetch the transcript, and send it to an LLM to extract what matters about companies the
user follows. Insider trades tell you what management *did*; interviews tell you what
management *says*. Sitting the two side by side is the point.

## Bottom line

**Both components the idea depends on work, and both are free.** The transcript problem
is smaller than expected, and the LLM problem is a rate-limit budgeting exercise, not a
capability one. What is genuinely unresolved is *what the LLM should output* and *how
much a sponsored interview is worth*, which are product questions, not technical ones.

## What was verified live

### Transcripts: solved, no API key, no cost

Two independent routes were tested against a real 16.7 minute interview
(`Canada Nickel (TSXV:CNC) - Federal Approval + C$21 Million Funding`, video `6-kkO3PyuPE`):

| Route | Result |
|---|---|
| `yt-dlp --skip-download --write-auto-subs --sub-langs "en.*" --sub-format json3` | 17,952 chars of clean text, sub-second download |
| `youtube-transcript-api` (`YouTubeTranscriptApi().fetch(video_id)`) | **byte-identical** 17,952 chars |

`youtube-transcript-api` is the better fit: 7 packages, pure HTTP, no JavaScript runtime
and no `ffmpeg` (yt-dlp warned about both). It reads the same caption track YouTube's own
player uses, so there is nothing to parse out of a page.

Size: that 16.7 minute interview is roughly **4,500 tokens**. A 60 minute one lands near
16,000.

### The known failure mode does not apply here

`youtube-transcript-api` has a well-documented problem: YouTube blocks datacenter IP
ranges, so it works in development and dies the moment it is deployed to AWS, GCP or
Azure. Every fix on offer is a paid rotating-residential-proxy service.

InSight never deploys. It runs on the user's own machine, on a residential connection,
which is precisely the environment where this library is reliable. **A limitation that
kills this approach for a SaaS is a non-issue for a local-first app.** This is the same
structural advantage that makes the SEDI browser scraper viable.

### Discovering new videos: RSS, no API key

`https://www.youtube.com/feeds/videos.xml?channel_id=UC...` returns the latest 15 videos
as Atom XML with video ID, title, and publish time. Verified working. No YouTube Data API
key, no quota, no account. Poll it per channel, diff against what has been seen, done.

### Most videos announce their own subject

Of 30 recent titles from one such channel, **25 carried an exchange-qualified ticker in
the title** (`Canada Nickel (TSXV:CNC) - ...`, `Asante Gold (CSE:ASE) - ...`).

That is a cheap watchlist filter that costs nothing: parse the title, and only spend an
LLM call when the video is about a company the user actually follows. It should keep the
LLM budget aimed at maybe one video in five rather than every upload.

### `youtubettt.com`, the original idea

It exists, but it is not a foundation to build on. `youtubettt.com/watch?v=<id>`
302-redirects to `youvideototext.com/?r=ytb&v=<id>`, a consumer site with sign-in,
marketing copy, and terms of use, which renders the transcript client-side. There is no
documented API, no stability contract, and building on it would mean scraping a scraper.

Since it is a middleman for the same caption track `youtube-transcript-api` reads
directly, the middleman adds a dependency and a point of failure while removing nothing.
**Recommend going direct.**

### The LLM: free tiers cover this comfortably

Verified against Groq's own published limits (`console.groq.com/docs/rate-limits`):

| Model | RPM | RPD | TPM | TPD |
|---|---|---|---|---|
| `openai/gpt-oss-120b` | 30 | 1,000 | **8,000** | 200,000 |
| `openai/gpt-oss-20b` | 30 | 1,000 | 8,000 | 200,000 |

Daily budget: 200,000 tokens against ~5,000 per interview is roughly **40 interviews a
day** from Groq alone, far past what a few channels produce.

Groq's pinch point is **8,000 tokens per minute**, not the daily cap. A short interview
fits in one call; a 60 minute one does not. On a single provider that would force
chunking. It does not, because of the next section.

Groq also hosts Whisper free (20 RPM, 2,000 requests/day, 7,200 audio-seconds/hour, 25 MB
per file), which covers the caption fallback below.

## The provider pool

**Design premise: several free-tier keys from different companies, not one.** This is a
decision, not an incidental detail, and it changes the shape of `llm.py`.

Free tiers in 2026 differ in *which* limit binds, and the differences are complementary
rather than redundant. Reported figures, to be confirmed against each provider's own docs
before relying on them (only the Groq numbers above were verified directly):

| Provider | Reported free-tier shape | Best used for |
|---|---|---|
| Groq | 8K TPM, 200K TPD, very fast | short interviews, the default |
| Cerebras | ~30K TPM, ~1M tokens/day | long transcripts in one pass |
| Google AI Studio (Gemini Flash) | 15 RPM, 1,500 RPD, ~1M context | the longest transcripts, whole-batch passes |
| Mistral | ~1B tokens/month, but ~2 RPM | slow bulk backfill |
| OpenRouter | one key, 30+ free models, ~10-20 RPM | overflow and model comparison |

Consequences worth designing for:

1. **The per-minute pinch point mostly disappears.** Route by transcript size: a long
   interview goes to a wide-context provider in one call instead of being chunked and
   stitched. Chunking becomes a fallback, not the default path, which is a real quality
   win since map-then-reduce loses cross-answer context in exactly the interviews worth
   reading.
2. **A provider going down or changing its terms stops being an outage.** Free tiers are
   not contractual and have changed before. With a pool, one provider withdrawing its free
   tier is a config change.
3. **The interface must be provider-shaped, not Groq-shaped.** Each entry needs its
   endpoint, model name, key, and declared limits (RPM/TPM/RPD/context). Most expose an
   OpenAI-compatible chat-completions endpoint, so one client with a per-provider base URL
   and model covers nearly all of them.
4. **Rotation needs to be honest about state.** Track spend per provider per window
   locally, pick the first provider with headroom, and fall through on HTTP 429 rather
   than guessing. Since runs are scheduled and unattended, waiting is always an acceptable
   last resort.
5. **Keys are secrets and follow the pattern already in the codebase.** `notify.py`
   already stores an SMTP app password in `settings.json` inside the per-user app dir and
   masks it to the UI with a sentinel, keeping the stored value when the UI sends the mask
   back. LLM keys do exactly the same. Nothing goes in the repo; gitleaks runs in
   pre-commit and should stay able to pass.

## Sketch of how it would fit

Roughly parallel to how `sedi.py` slots in beside `marketbeat.py` today:

- **`insight/interviews.py`**: poll channel feeds, diff against seen video IDs, fetch
  transcripts, cache them.
- **`insight/llm.py`**: the provider pool described above, behind one `complete()`
  interface. Callers pass a prompt and a token estimate; the pool decides who serves it.
- **Storage**: a separate `data/interviews.json`, keyed by video ID, holding transcript
  plus extraction plus a `seen`/`analysed` marker. Keeping it out of `store.json` means
  the existing merge and prune logic stays untouched.
- **CLI**: `insight-scrape --source interviews`, matching the existing source flag.
- **UI**: a section on the company card, next to the SEDI report link.
- **Settings ⚙**: channel list and API key, following the pattern already established for
  notifications.

Two properties worth keeping deliberately:

1. **Transcript and analysis are separate stages.** Transcripts are free and permanent;
   LLM output is rate-limited and will want re-running as prompts improve. Cache the
   transcript, and re-analysis costs nothing extra.
2. **No key means no LLM, not no feature.** With zero keys it can still list new
   interviews for watchlist companies and link them. That is useful on its own, and it is
   what phase 1 ships.

## Open questions

Two of these block code (1 and 4); the rest can be settled as the phases arrive.

1. **What should the LLM actually return?** Structured JSON (sentiment, concrete claims,
   guidance with dates, financing plans, stated risks) is queryable, alertable and
   testable. Prose is easier to write and easier to read once. These lead to very
   different features, and this is the decision the whole design hangs on.
2. **Which channels, and who curates them?** Ship a default list, or make it entirely
   user-managed? A default list is a maintenance burden and an implicit endorsement.
3. **How do interviews attach to companies?** Ticker-in-title covers ~83% cheaply. The
   remainder needs the LLM to name the companies discussed, which also catches a company
   mentioned inside an interview that is nominally about someone else. Possibly the more
   valuable case.
4. **Sponsored content.** Channels of this kind are frequently paid investor-relations
   placements. The CEO is a guest, not a defendant, and nobody asks a hard question. An
   LLM summary of a promotional interview reads exactly like an LLM summary of a critical
   one, which is a real way for this feature to mislead. Options: label the source, keep
   extraction to checkable facts rather than tone, or state the bias plainly in the UI.
   **Do not skip this one.** It is the difference between a useful feature and a
   confident one.
5. **Missing captions.** Some videos have none. Fallback is yt-dlp for audio and Groq
   Whisper to transcribe, which costs a heavier dependency (`ffmpeg`) for an unknown
   fraction of videos. Measure the fraction before paying for it.
6. **Cadence and trigger.** Fold into the existing scrape run, or a separate scheduled
   pass? Interviews arrive on the channel's schedule, not the market's.
7. **Alarms.** Should a new interview about a followed company fire the existing
   notification path? Cheap to add, and arguably the strongest reason to have the feature
   at all.
8. **Provider routing policy.** Cheapest-with-headroom first, or best-model-first and fall
   back on exhaustion? These give different answers on the same interview, which matters
   if output is ever compared across days. Leaning cheapest-first with the model recorded
   alongside each extraction, so a later reader can tell what produced what.

## Phasing

Each phase is independently useful, which keeps the expensive uncertainty last.

- **Phase 0 (design)**: settle questions 1 and 4. Nothing after phase 1 unblocks without
  them, and neither is a question code can answer.
- **Phase 1 (no LLM, no keys)**: feed polling, transcript fetch, caching, title-based
  watchlist matching. Ship it as "new interviews about your companies" with links. This
  proves real coverage against a real watchlist before any key exists, and it is the phase
  that tells us whether the idea is worth the rest.
- **Phase 2 (the provider pool)**: `llm.py` with the key list, declared limits, local
  spend tracking, size-based routing and 429 fallthrough. Worth building before the
  prompts, because it is the part that has to be right and the part that is pure logic:
  testable against fakes with no network at all.
- **Phase 3 (extraction)**: the prompt and its output schema, run over cached transcripts.
  Cached input means prompts can be re-run and compared for free.
- **Phase 4 (UI)**: surface on the company card; wire alarms if question 7 says yes.
- **Phase 5 (fallback)**: Whisper for caption-less videos, only if phase 1 shows the gap
  is worth the `ffmpeg` dependency.

## Caveats

- Auto-generated captions carry filler and mis-hear proper nouns (`[music]`, `uh`,
  repeated words are all visible in the sample). Fine for an LLM, poor to quote verbatim
  to a user.
- Nothing here has an API contract. Both caption routes read an endpoint YouTube does not
  publish for this purpose, and it has broken before. Treat a transcript failure as
  routine and skip the video, the same way SEDI failures are handled now.
- YouTube's terms address automated access. This is one user, on their own machine,
  reading public captions for personal research, which is the same posture the app
  already takes with SEDI. Worth a conscious decision rather than an assumption.
- Free-tier limits are not contractual and have changed before, at every provider. The
  pool is the hedge, and the reason the non-Groq figures above are marked as reported
  rather than verified: check each one against its own docs when its key is added.
- A pool of free tiers is still a set of terms of service. Each provider's free tier has
  its own rules about what the output may be used for, and they are not identical. Worth
  reading once per key rather than assuming.

## Primary sources

- [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api) and its
  [cloud-IP blocking issue](https://github.com/jdepoix/youtube-transcript-api/issues/593)
- [Groq rate limits](https://console.groq.com/docs/rate-limits) and
  [speech-to-text docs](https://console.groq.com/docs/speech-to-text)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- Other free tiers, secondhand and unverified:
  [OpenRouter's comparison](https://openrouter.ai/blog/tutorials/free-llm-apis-compared/)
