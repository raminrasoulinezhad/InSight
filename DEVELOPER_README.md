<p align="center">
  <img src="assets/logo.png" alt="InSight — Uncovering Insider Intelligence" width="360" />
</p>

# InSight — Insider Transaction Collector

Proof of concept that collects **insider transactions** (individuals, officers,
directors, and the **issuer itself** / institutions) for a watchlist of
Canadian (and US) stocks, normalizes them into one schema, and writes daily
CSV/JSON for downstream processing.

## TL;DR — current status

- ✅ **Working today** against **MarketBeat** per-stock insider-trades pages.
- ✅ Verified live for: Franco-Nevada (FNV), Canadian Natural Resources (CNQ),
  Wheaton Precious Metals (WPM), Suncor (SU), Athabasca Oil (ATH),
  American Eagle Outfitters (AEO).
- ⚠️ **SEDI (the authoritative Canadian source) could not be used from this
  machine** — see [Why not SEDI directly?](#why-not-sedi-directly) below.

## Quick start

InSight installs as a normal application with a single command via
[`uv`](https://docs.astral.sh/uv/) (one cross-platform tool — the same steps
work on **Linux, macOS, and Windows**). It ships two commands: `insight` (the
app window) and `insight-scrape` (the collector). After install the source repo
is no longer needed and can be deleted.

```bash
# 1. install uv (once) — see https://docs.astral.sh/uv/getting-started/install/
#    Linux/macOS:  curl -LsSf https://astral.sh/uv/install.sh | sh
#    Windows:      powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. install InSight from a clone (or `git+<url>` to skip cloning)
uv tool install .
# uv tool install git+https://github.com/<you>/InSight

# 3. one-time: download the Chromium browser the scraper drives (~150 MB cache)
uv tool run --from playwright playwright install chromium
```

Then, from anywhere:

```bash
# scrape the watchlist
insight-scrape

# or ad-hoc tickers (EXCHANGE:TICKER)
insight-scrape --tickers TSE:FNV TSE:CNQ NYSE:AEO

# widen coverage: auto-discover MarketBeat's ticker universe and scrape it
# alongside the watchlist (default exchange TSE, ~215 large/mid-cap names).
# The People tab then spans every scraped company; the Companies tab stays
# limited to your watchlist. Small-cap TSX-V/CSE names are not enumerated —
# MarketBeat does not list them — so this is a best-effort free expansion.
insight-scrape --discover          # TSE universe + watchlist
insight-scrape --discover TSE      # same, explicit

# show the browser (useful if your IP gets a bot challenge)
insight-scrape --headful

# cache control (each company is cached to avoid re-fetching)
insight-scrape --max-age 24    # reuse cache younger than 24h (default 12)
insight-scrape --force         # ignore the cache, re-fetch everything
insight-scrape --no-cache      # don't read or write the cache
```

Each company's fetched data is cached (one JSON per issuer under the app
folder's `cache/`); a re-run reuses data younger than `--max-age` hours instead
of re-fetching, and only launches the browser if something actually needs
fetching. The app's **Refresh** button always forces a fresh fetch. The app UI
also filters transactions by a selectable window (1M/3M/6M/1Y/2Y) via
`GET /api/data?months=N`.

### Run it daily in the background (Linux / systemd)

Because history accumulates across snapshots (above), running the scrape on a
schedule is what deepens the window over time. On Linux a **systemd user timer**
is the cleanest option — it runs once a day and, with `Persistent=true`, catches
up on the next login if the machine was off at the trigger (so effectively:
"when the machine is on, run once if it hasn't run today, else skip"). It runs
while you are logged in; for a headless box, also `sudo loginctl enable-linger
$USER`.

`~/.config/systemd/user/insight-scrape.service`:

```ini
[Unit]
Description=InSight daily insider-transaction scrape (broad TSE universe)

[Service]
Type=oneshot
ExecStart=%h/.local/bin/insight-scrape --discover
TimeoutStartSec=1800
```

`~/.config/systemd/user/insight-scrape.timer`:

```ini
[Unit]
Description=Run the InSight scrape once per day (catches up if the machine was off)

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now insight-scrape.timer   # install + start
systemctl --user list-timers insight-scrape.timer    # when it next runs
journalctl --user -u insight-scrape.service -n 30    # last run's log
systemctl --user disable --now insight-scrape.timer  # remove/stop
```

The watchlist and all output live in a per-user app folder (created on first
run), so nothing depends on the repo location:

| OS | app folder |
|---|---|
| Linux | `${XDG_DATA_HOME:-~/.local/share}/InSight` |
| macOS | `~/Library/Application Support/InSight` |
| Windows | `%LOCALAPPDATA%\InSight` |

<details>
<summary>Run from a checkout without installing (dev)</summary>

`uv run` reads <code>pyproject.toml</code> and provisions the environment on the
fly — no manual virtualenv:

```bash
uv run insight-scrape --tickers TSE:FNV
uv run insight --no-browser
```

Prefer pip? `pip install .` (or `pip install -e .`) exposes the same
`insight` / `insight-scrape` commands.
</details>

### Output (per run, stamped with the date, under the app folder)

```
data/insider_YYYY-MM-DD.json          all records (source-agnostic schema)
data/insider_YYYY-MM-DD.csv           same, flat CSV
data/by_ticker/TSE_FNV_YYYY-MM-DD.csv one CSV per company
```

(Use `insight-scrape --outdir ./data` to write to an explicit folder instead.)

**History accumulates.** Each scrape captures only MarketBeat's most-recent page
(no deep backfill), so a single file spans limited history. The app therefore
merges **all** `insider_*.json` snapshots into one deduplicated view (newest
scrape wins on an identical-transaction collision), and the "Last N months"
filter runs over that union. Running the scraper regularly (e.g. via cron) is
what deepens the window over time.

**Snapshots are folded into one consolidated store** (`data/store.json`, see
`store.py`). Because every scrape re-states the history rather than appending to
it, the snapshot pile is dominated by repetition — a real 57-snapshot folder of
357 MB deduplicated to 31 032 records. Re-merging that on every cold start cost
2.4 s, and again after each scrape (a new file invalidates the in-memory cache).
So snapshots are folded once, oldest → newest, into a single store carrying a
manifest of what it has already absorbed:

```
{"version": 1,
 "folded": {"insider_2026-06-30.json": [mtime_ns, size], ...},
 "records": [ ...deduplicated records... ]}
```

`store.sync()` parses only snapshots whose name+mtime+size are missing from the
manifest, so a fresh scrape costs one small file instead of the whole history —
**cold start 2.44 s → 0.10 s** on that same folder. The store is a derived cache:
delete it and it rebuilds. The manifest also *is* the history — it remembers
every snapshot ever folded, so `history_files` and the newest data date stay
correct once the originals are gone.

**Reclaiming the disk.** Once folded, the dated files are redundant bulk:

```bash
insight-scrape --prune-snapshots      # keep the newest 2, delete the rest
insight-scrape --prune-snapshots 10   # keep the newest 10
```

This re-syncs and re-reads the store from disk *before* deleting anything, and
only ever removes files the manifest proves were absorbed — no records are lost.
It touches `insider_*.json` only; the per-run `.csv` and `by_ticker/` exports are
independent and left alone (delete those by hand if you don't use them).

**Delisted / acquired companies are dropped automatically.** When a fetch finds
a ticker's insider page gone — the URL redirects to `…/stocks/<EXCH>/<TICKER>/`
(profile kept, no `insider-trades` subpage), as happens after an acquisition —
the scraper raises `CompanyDelisted`, deletes that company's cache file, and
records `EXCH:TICKER` in `delisted.json`. Both views filter those out, so stale
insider activity for acquired names stops showing. The snapshots themselves are
left intact (nothing is destroyed), and the flag is **self-healing**: if a
company's data ever returns, the next scrape un-flags it. A ticker that instead
redirects to the bare `…/stocks/<EXCH>/` list (unknown / not covered) is treated
as "no data", not delisted.

**Search is the hot path, so access is cached.** Filtering by name/company is the
app's main job, and rebuilding a view means re-reading and re-aggregating the
record set. `aggregate` memoizes the merged records and the built views, keyed by
a cheap signature (each snapshot's mtime+size, plus the watchlist and delisted
files). A rebuild happens only when the underlying data actually changes (a
scrape, add/remove, or delist); otherwise repeated access is an in-memory dict
lookup — ~1000× faster on a large accumulated history (≈2 s → a few ms in a
300-snapshot benchmark). The consolidated store is what makes the *miss* cheap
too, so the first request after a restart or a scrape no longer stalls. The
frontend likewise precomputes a lowercased search string per company/insider once
per load and debounces the box, so typing filters in constant work per item. This
in-memory approach beats an embedded DB for a single-user, few-MB dataset;
SQLite/FTS5 is the escalation path only if the data ever outgrows memory or needs
concurrent writers.

**The window defaults to the last 2 weeks.** The range selector opens on
`Last 2 weeks`, not the full history. Rendering is a single `innerHTML` rebuild
of the whole feed, so the window size sets the interaction cost directly: on the
same data a 2-year window was a 6.2 MB payload and 195 746 feed nodes taking
699 ms to paint, against 56 KB / 1 805 nodes / 13 ms for 2 weeks. The wider
ranges are all still one click away — they just aren't what you pay for on every
load, tab switch and keystroke. If the feed ever needs to render a large window
smoothly, virtualizing the transaction rows is the next step.

### Example summary

```
TSE:FNV    8 txns  (buys=0 sells=8 institutional=0)  latest=2025-11-26
TSE:CNQ   15 txns  (buys=2 sells=13 institutional=0) latest=2026-03-24
TSE:ATH   27 txns  (buys=27 sells=0 institutional=27) latest=2026-05-29   <- issuer buyback
```

## The application window

A self-contained local web app (the server is Python stdlib only — no X11
display required) renders the data as a scrollable feed of **boxes**, one per
insider/entity, grouped under each watchlist company. Each box shows the
buy / sell / total transaction counts, the shares and dollar amounts bought
and sold, a buy↔sell ratio bar, and who was trading (name, role, and an
individual / institution / buyback badge).

```bash
insight              # serve on http://127.0.0.1:8765 + open a browser
insight --window     # open as a standalone desktop window (chromeless)
insight --port 9000
insight --no-browser # headless box: open the URL yourself
```

### Desktop launcher

`--window` opens the UI as a chromeless desktop window (via Chrome's `--app=`
mode) whose lifetime owns the server — close the window and the server stops.
The window runs in its own dedicated Chrome profile so it never disturbs your
main browser. It finds Chrome, Edge, Chromium, or Brave automatically (and
falls back to Playwright's bundled Chromium) on Linux, macOS, and Windows.

**Linux** — install it as a real app (icon in the app grid) that launches the
installed `insight` command:

```bash
./install-desktop.sh              # add InSight to your application menu
./install-desktop.sh --uninstall  # remove it
```

**macOS / Windows** — run `insight --window` directly, or pin it: on macOS wrap
it in a one-line `.command` file or an Automator app; on Windows create a
Start-Menu shortcut whose target is `insight --window` (the `insight.exe`
shim lives in the uv tools bin, shown by `uv tool dir`).

It reads and merges all `data/insider_YYYY-MM-DD.json` snapshots from the app
folder (deduplicated; see "History accumulates" above). Re-run
`insight-scrape` to refresh the data, then reload the page. Watchlist companies
with no data (e.g. uncovered TSX-V names) appear as empty cards so coverage gaps
are visible.

Search filters by company/ticker/insider; the segmented control filters to net
buyers, net sellers, or institutions.

## The normalized record

Every source is mapped to one schema (`insight/models.py`):

| field | meaning |
|---|---|
| `issuer_name`, `exchange`, `ticker` | the company the trade is in |
| `insider_name`, `insider_role` | who traded (e.g. "Director", "Officer", "Insider") |
| `entity_type` | `individual` or `institution` (companies, funds, the issuer) |
| `is_issuer_buyback` | the company trading its own shares |
| `transaction_date`, `transaction_type` | ISO date, Buy/Sell/… |
| `shares`, `avg_price`, `total_value`, `currency` | the numbers (CAD/USD) |
| `source`, `source_url`, `scraped_at` | provenance |

## Watchlist & adding companies by name

The watchlist (`companies.json`) is keyed by **full legal company name** — the
stable identifier. Tickers are kept as a secondary field (MarketBeat needs
them) but are *not* the key, because they collide across exchanges and listings
(e.g. `NFG` is New Found Gold in Canada but National Fuel Gas in the US;
`AEO`/`AE` is American Eagle the apparel retailer vs. the gold explorer).

```json
{ "name": "New Found Gold Corp.", "exchange": "TSXV", "ticker": "NFG", "country": "CA", "confirmed": true }
```

Exchange codes are MarketBeat's: `TSE` (Toronto), `TSXV` (TSX Venture),
`NYSE`/`NASDAQ` (US).

**Add by name in the app.** The header has an "Add a company by name" box. As
you type, `insight/issuers.py` resolves the name to issuer candidates and shows
a picker; you select the right listing and it's appended to `companies.json`.
When a name is ambiguous you choose — the app never silently guesses.

- Resolver backend: TradingView's public symbol-search endpoint (reachable from
  this host; covers TSX / TSX-V / CSE / US). It is isolated behind
  `search_issuers()` so the authoritative **SEDI issuer search** can replace it
  later without touching the app or watchlist code.
- API: `GET /api/search?q=<name>` → candidates; `POST /api/watchlist` → add a
  picked candidate.

## Run it daily

The transactions need only a 1–2 day freshness, so a daily schedule is plenty.

**Linux/macOS (cron)** — use the absolute path to the installed command
(`which insight-scrape`):

```cron
30 6 * * *  ~/.local/bin/insight-scrape >> ~/.local/share/InSight/cron.log 2>&1
```

**Windows (Task Scheduler)** — create a daily task running `insight-scrape`
(find its path with `where insight-scrape`).

Each run writes a new date-stamped file, so you accumulate a history you can
load into a database or diff day-over-day to detect *new* filings.

## Why not SEDI directly?

SEDI (`sedi.ca`) is the official Canadian System for Electronic Disclosure by
Insiders — the authoritative, near-real-time source (filings public within
~5 min). It's where MarketBeat and others ultimately get their data. We tried
to drive it with a headless browser and hit hard walls, **verified live from
this host**:

- **SEDI** sits behind **ShieldSquare / PerfDrive** bot protection and serves
  an **hCaptcha** challenge to automated traffic. After a couple of requests
  the IP was flagged and *every* page (even the welcome page) returned the
  captcha. This machine's egress IP is a **hosting/VPS IP**, which anti-bot
  systems score as high-risk — so headless *and* headful automation get
  challenged regardless.
- **canadianinsider.com** and **insidertracking.com** (SEDI aggregators) are
  behind **Cloudflare** bot protection (HTTP 403 "Just a moment…").
- **MarketBeat** per-stock pages are reachable → used here.

### Getting the authoritative SEDI data later

SEDI scraping *does* work from a normal browser on a residential connection
(that's how the community [SEDI bookmarklet](https://tomcardoso.github.io/sedi-bookmarklet/)
works). To productionize the SEDI path, pick one:

1. **Residential / Canadian proxy** in front of the Playwright scraper (most
   robust; the navigation + table-parsing code is straightforward to add as a
   second source behind the same `InsiderTransaction` schema).
2. **Run the scraper on a residential machine** in headful mode and solve the
   one-time captcha manually; reuse the browser profile so the session sticks.
3. **A captcha-solving service** (e.g. 2Captcha) — costs money, gray area.

## Known limitations of the MarketBeat source

- **Coverage gap:** small **TSX-Venture** issuers are *not* covered (e.g.
  *American Eagle Gold Corp*, TSXV:AE returned no page — only the US apparel
  retailer *American Eagle Outfitters*, NYSE:AEO, exists on MarketBeat). For
  micro/small-cap TSX-V names you will need SEDI.
- **Freshness/detail:** MarketBeat aggregates and may lag SEDI by days, and it
  drops SEDI's richer fields (ownership type, nature-of-transaction codes,
  post-transaction balance).
- **Terms of use:** scraping MarketBeat may conflict with their ToS. For
  production, prefer a licensed feed or the authoritative SEDI route.

## Alarms & notifications

Set an alarm on a **company** ("any insider trade in ATH") or a **person**
("whenever Eric Sprott trades, in any company") via the 🔔 button on its card, or
manage everything in the **Alarms tab** (settings + list). After every scrape
(`evaluate_and_notify`, called from the daily timer and the Refresh button), any
alarm whose target has a transaction *newer than when the alarm was set* fires
over the enabled free channels:

- **Email** — SMTP (e.g. a Gmail/Workspace **app password**), an HTML message
  with the InSight logo (CID-embedded) and a sentence per transaction.
- **ntfy** — a free push topic (`https://ntfy.sh/<topic>`), no credentials.

Every notification's **title** (email subject + ntfy title) is `InSight:
<target>` — it always leads with InSight and names *what* fired (the company name
or the person), never a bare count of trades. Each generated notification is
stamped with a monotonic reference index that appears **in the message body**
(`#N`, plus a "Notification #N" line in the email footer) and is appended to an
**append-only JSONL log**, `notifications.log`, beside `notify.json` (see
`paths.notify_log_file()`). A line records the index, timestamp, target label,
the message lines and the per-channel delivery outcome — so any alert, delivered
or failed, can be traced back later when debugging or reporting an issue. The
next index is derived from the log itself (no separate counter), and logging is
best-effort so it never breaks a scrape.

State (channel settings + alarms) lives in `notify.json` in the app folder —
**never the repo**, since it holds the SMTP password (the API masks it in
responses). Each alarm keeps a `seen` set of transaction keys baselined at
creation, so pre-existing history never alerts and nothing double-fires; the
baseline only advances once a channel actually delivered, so a transient outage
retries next scrape. Sends are best-effort and wrapped — a notification failure
never breaks a scrape. Truly free SMS isn't offered (carrier email-gateways are
deprecated/unreliable); email + ntfy are the free, reliable channels.

## Layout

```
pyproject.toml          packaging + the `insight` / `insight-scrape` commands
install-desktop.sh       Linux app-menu launcher installer
insight/
  app.py                the application window (local web server + UI)
  scrape.py             scraper CLI entry point (config, output, summary)
  paths.py              per-user data/config/profile dirs (cross-platform)
  models.py             InsiderTransaction schema + parsing helpers
  marketbeat.py         MarketBeat scraper (Playwright + stealth)
  store.py              dated snapshots -> one deduplicated, incrementally folded store
  aggregate.py          flat records -> per-company / per-insider boxes
  notes.py              per-company user notes (your own research, kept per ticker)
  issuers.py            name -> issuer-candidate resolver (+ watchlist add)
  companies.default.json  seed watchlist (copied to the app folder on first run)
  webui/index.html      the single-page UI (scrollable insider boxes)
```

The editable watchlist and dated outputs live in the per-user app folder (see
[Quick start](#quick-start)), not in the repo.

Adding a new source = a new module that yields `InsiderTransaction` objects;
nothing downstream changes.

## Tests

Unit tests live under `tests/` (pytest) and cover the pure logic — parsing,
classification, aggregation math, average-cost calculations, history merge/dedup,
delisted filtering, watchlist add/remove, and the scraper's row/URL parsing —
without any network or browser. Dev tooling is a `uv` dependency group:

```bash
uv sync --group dev            # install dev tools (pytest, ruff, mypy, …)
uv run pytest                  # tests
uv run ruff check insight tests
uv run mypy insight            # strict on the pure core; glue modules relaxed
```

The network/browser parts (live MarketBeat fetch, discovery, delisting redirect)
are intentionally isolated behind pure helpers (`_extract_tickers`,
`_no_insider_page_kind`, `_row_to_record`, …) so they can be tested with sample
inputs rather than live HTTP.

### Browser-UI tests (`tests/webui/`)

All of the app's interaction logic lives in `insight/webui/index.html`, so it
gets its own suite — on **Node's built-in test runner**, so there is still no
npm install, no `package.json` dependencies and no build step:

```bash
node --test tests/webui/       # directly
uv run pytest                  # …or via the bridge in tests/test_webui_js.py
```

`tests/webui/harness.mjs` reads the real `index.html`, extracts its `<script>`,
and evaluates it in a `node:vm` context against a small DOM stub — so the tests
exercise shipped code rather than a copy. Two things to know when writing them:

- Top-level `function` declarations land on the vm context (`ctx`); `let`/`const`
  bindings (`STATE`, `BULLET`, `NAV_MAX`, the arrow-function helpers) are
  lexically scoped and are reached through `lex`, which the harness exposes via
  live getters.
- Arrays returned from the vm carry that realm's prototype, so wrap them in
  `Array.from()` before `assert.deepEqual`.

The suite covers bullet normalization and note escaping, timeline geometry
(nothing clipped, radii ordered by share count), the back-stack state machine
(typing collapses to one step, the cap drops the oldest), the cross-link markup,
and markup invariants — including that the preselected `<option>` and
`STATE.range` still agree, which is the same fact stated in two places.

Anything needing real layout or real events (focus, key handling, paint cost) is
verified against a live browser instead, not here.

CI (`.github/workflows/`) runs the same ruff/mypy/pytest checks on every push and
PR, plus the Node UI suite, plus a `gitleaks` secret scan over the full history as
a server-side backstop to the local pre-commit hook. Node is installed explicitly
in CI because the pytest bridge *skips* when node is missing — without that step
the UI tests would quietly stop running instead of failing.
