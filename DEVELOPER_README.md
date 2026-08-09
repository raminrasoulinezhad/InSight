<p align="center">
  <img src="assets/logo.png" alt="InSight — Uncovering Insider Intelligence" width="360" />
</p>

# InSight — developer guide

Collects **insider transactions** (individuals, officers, directors, and the
**issuer itself**) for a watchlist of Canadian and US stocks, normalizes them
into one schema, and folds dated snapshots into a single deduplicated store.

- **`insight`** — local HTTP server + single-page UI.
- **`insight-scrape`** — the collector (Playwright).
- Single-user, local, no database, no cloud, no paid feed.

User-facing docs live in **[README.md](README.md)**.

---

## Quick start

```bash
# 1. install uv — https://docs.astral.sh/uv/getting-started/install/
# 2. install InSight
uv tool install .                  # or: uv tool install git+https://github.com/<you>/InSight
# 3. one-time: the Chromium the scraper drives (~150 MB)
uv tool run --from playwright playwright install chromium
```

Run from a checkout without installing:

```bash
uv run insight --no-browser
uv run insight-scrape --tickers TSE:FNV
```

Typically installed editable (`uv tool install --editable .`): UI edits show on
reload, Python edits need an app restart.

---

## Data sources

Both normalize to `InsiderTransaction`, and both merge into the same deduplicated
view. Snapshots are tagged by source (`insider_sedi_*.json`) so neither clobbers
the other.

| | **MarketBeat** (`marketbeat.py`) | **SEDI** (`sedi.py`) |
|---|---|---|
| Role | Default, everyday | On demand, for what MarketBeat misses |
| Coverage | ~215 large/mid-cap TSE + US | Every Canadian filing, incl. TSXV/CSE micro-caps |
| Authority | Aggregator, may lag days | Official CSA system, public within ~5 min |
| Automation | Headless, unattended | **Headful only** — bot-walled |
| Fields | Drops ownership type, nature-of-transaction codes, post-transaction balance | Full |

### Why SEDI can't be the default

SEDI sits behind ShieldSquare / PerfDrive, which serves an **hCaptcha** to
automated and datacenter-IP traffic. Headless *and* stealthed headful requests
get challenged (verified: `navigator.webdriver=false` + `playwright-stealth`
still got captcha'd from a VPS IP).

The workaround is not to defeat the wall but to **solve it once by hand**:

- Runs headful with a **persistent profile** (`paths.sedi_profile_dir()`), so the
  session cookie survives between runs.
- If the wall is up and nobody can solve it, the fetch raises `BotBlocked` and the
  batch falls back to cache — same as the MarketBeat path.
- Canada-only: non-Canadian targets are filtered out by `is_canadian`.
- Each fetched report page is saved to `paths.sedi_pages_dir()`. The UI shows a
  **⛏ SEDI report** link on companies that have one, served by `/api/sedi-page`
  with a `<base>` injected so sedi.ca's relative CSS/images resolve.

Because a human may be needed, SEDI is a deliberate button (**⛏ Fetch from
SEDI**) and an explicit `--source sedi`, never the daily timer.

`sedi.py` mirrors `marketbeat.py`'s split: pure header-driven parsing
(`_map_columns`, `_row_to_record`, `_transaction_type`, `_parse_sedi_date`) that
survives column reordering, plus browser glue whose selectors are best-effort —
SEDI is a legacy Struts app. Run with `--capture-dir` to dump live HTML when a
selector needs adjusting.

### Blocked alternatives

- **canadianinsider.com**, **insidertracking.com** — Cloudflare (HTTP 403).
- Paid aggregators and the CSA bulk license were researched in
  [`docs/canadian-insider-data-research.md`](docs/canadian-insider-data-research.md).
  Bottom line: no free, official, person-searchable bulk feed exists.

**Terms of use:** scraping MarketBeat may conflict with their ToS; SEDI's terms
bar automated collection. For production, prefer a licensed feed.

---

## The scraper CLI

```bash
insight-scrape                              # the watchlist, via MarketBeat
insight-scrape --tickers TSE:FNV NYSE:AEO   # ad-hoc (EXCHANGE:TICKER)
insight-scrape --discover                   # + MarketBeat's TSE universe (~215)
insight-scrape --source sedi                # official SEDI, opens a browser
```

| Flag | Effect |
|---|---|
| `--config` / `--outdir` | Override the watchlist / output dir (default: app folder) |
| `--discover [EXCH…]` | Add MarketBeat's exchange listing to the targets (default `TSE`) |
| `--source {marketbeat,sedi}` | Data source (default `marketbeat`) |
| `--sedi-months N` | SEDI lookback, months back from today (default 24) |
| `--capture-dir DIR` | (sedi) dump each page's HTML + screenshot for debugging |
| `--headful` | Visible browser — helps on flagged IPs |
| `--max-age H` | Reuse cache younger than H hours (default 12) |
| `--force` / `--no-cache` | Ignore the cache / don't read or write it |
| `--keep-snapshots N` | Snapshots to keep after the auto-prune (default 2) |
| `--prune-snapshots [N]` | Reclaim disk now, no scrape (default keep 2) |
| `--prune-browser-cache` | Clear the Chromium caches, keep cookies |

Each company is cached as one JSON per issuer under `cache/`; a re-run only
launches the browser if something actually needs fetching. The app's **Refresh**
button always forces a fresh fetch.

### Run it daily

Freshness of 1–2 days is plenty, and running regularly is what deepens the
history window (each scrape only captures the source's most recent page).

**Linux — systemd user timer.** `Persistent=true` catches up after the machine
was off. For a headless box also `sudo loginctl enable-linger $USER`.

`~/.config/systemd/user/insight-scrape.service`:

```ini
[Unit]
Description=InSight daily insider-transaction scrape

[Service]
Type=oneshot
ExecStart=%h/.local/bin/insight-scrape --discover
TimeoutStartSec=1800
```

`~/.config/systemd/user/insight-scrape.timer`:

```ini
[Unit]
Description=Run the InSight scrape once per day

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now insight-scrape.timer   # install + start
systemctl --user list-timers insight-scrape.timer    # next run
journalctl --user -u insight-scrape.service -n 30    # last run's log
```

**Linux/macOS — cron** (use the absolute path from `which insight-scrape`):

```cron
30 6 * * *  ~/.local/bin/insight-scrape >> ~/.local/share/InSight/cron.log 2>&1
```

**Windows** — a daily Task Scheduler task running `insight-scrape` (path from
`where insight-scrape`).

---

## Storage

Everything lives in a per-user app folder, created on first run — nothing depends
on the repo location:

| OS | app folder |
|---|---|
| Linux | `${XDG_DATA_HOME:-~/.local/share}/InSight` |
| macOS | `~/Library/Application Support/InSight` |
| Windows | `%LOCALAPPDATA%\InSight` |

```
data/insider_YYYY-MM-DD.json        a run's snapshot (source-agnostic schema)
data/insider_sedi_YYYY-MM-DD.json   …from SEDI
data/store.json                     the deduplicated fold of every snapshot
```

Only JSON is written. Earlier versions also emitted a flat `.csv` and a
`by_ticker/` of per-company CSVs; nothing read them back, and since every run
restates the same rows they grew ~5 MB and ~130 files per scrape (305 MB across
7,000+ files in a real folder). Delete leftovers by hand. For a spreadsheet
export, read `store.json` — it is the deduplicated union of everything.

### The consolidated store (`store.py`)

Every scrape *restates* history rather than appending, so the snapshot pile is
dominated by repetition: a real 57-snapshot folder of 357 MB deduplicated to
31,032 records. Re-merging that cost 2.4 s on every cold start, and again after
each scrape.

So snapshots are folded once, oldest → newest, into a store carrying a manifest
of what it has already absorbed:

```json
{"version": 1,
 "folded": {"insider_2026-06-30.json": [mtime_ns, size]},
 "records": ["…deduplicated records…"]}
```

- `store.sync()` parses only snapshots whose name+mtime+size are missing from the
  manifest — a fresh scrape costs one small file, not the whole history.
  **Cold start 2.44 s → 0.10 s.**
- Newest scrape wins on an identical-transaction collision.
- It's a derived cache: delete it and it rebuilds.
- The manifest also *is* the history — it remembers every snapshot ever folded,
  so `history_files` and the newest data date stay correct once the originals are
  gone.

### Reclaiming disk

**Snapshots prune themselves.** Every scrape folds the new snapshot in and drops
the copies it made redundant, keeping the newest `--keep-snapshots N` (default 2).
Deliberately automatic: snapshots restate each other, so left alone they grow
unnoticed, and a cleanup command nobody discovers is the same as no cleanup. It
also leaves the store warm for the next cold start. Pass a large N to keep
everything.

```bash
insight-scrape --prune-snapshots      # keep the newest 2
insight-scrape --prune-snapshots 10   # keep the newest 10
```

This re-syncs and re-reads the store *before* deleting, and only removes files the
manifest proves were absorbed. It touches `insider_*.json` only — the store itself
is never a prune candidate.

### Browser profiles (`profiles.py`)

InSight drives Chromium twice — a persistent profile for SEDI (so a solved
challenge survives) and another for the `--window` app. Both accumulate
browser-sized caches: 362 MB and 164 MB on a real install, against 49 MB of
actual insider data. Nobody would guess that's where their disk went.

- Both launch with a capped disk cache. The app window also passes
  `--disable-component-update` — it only shows a local page, so it has no use for
  the ML model stores, TTS engine and Safe Browsing lists Chromium downloads
  (~110 MB). The scraper keeps them on: it faces a bot wall and should look like a
  normal browser.
- `prune_profile` removes only entries on an explicit `DISPOSABLE` allowlist, all
  caches Chromium regenerates. An allowlist rather than a denylist so a future
  Chromium directory holding real state isn't swept up by accident.
- **Session state is never touched** — for the SEDI profile the cookie jar *is*
  the solved CAPTCHA.
- Cleanup also runs by itself after a SEDI scrape and when the `--window` app
  closes — both points where that browser has definitely exited.
- A profile a browser still has open is detected via Chromium's `SingletonLock` (a
  symlink naming the live pid) and skipped; a lock naming a dead pid is a crash
  leftover and ignored. The check errs toward "in use": skipping a cleanup costs
  disk, a wrong guess costs a broken session.

### Delisted companies

When a fetch finds a ticker's insider page gone — the URL redirects to
`…/stocks/<EXCH>/<TICKER>/` with no `insider-trades` subpage, as after an
acquisition — the scraper raises `CompanyDelisted`, deletes that company's cache,
and records `EXCH:TICKER` in `delisted.json`.

- Both views filter those out, so stale activity for acquired names stops showing.
- Snapshots are left intact — nothing is destroyed.
- **Self-healing:** if the data ever returns, the next scrape un-flags it.
- A ticker redirecting to the bare `…/stocks/<EXCH>/` list is "no data", not
  delisted.

---

## Performance

**Access is cached.** Search is the app's main job, and rebuilding a view means
re-reading and re-aggregating everything. `aggregate` memoizes the merged records
and built views, keyed by a cheap signature (each snapshot's mtime+size, plus the
watchlist and delisted files).

- A rebuild happens only when the data actually changes (scrape, add/remove,
  delist); otherwise access is a dict lookup — ~1000× faster on a large history
  (≈2 s → a few ms over 300 snapshots).
- The consolidated store makes the *miss* cheap too, so the first request after a
  restart no longer stalls.
- The frontend precomputes a lowercased search string per company/insider once per
  load and debounces the box, so typing costs constant work per item.
- In-memory beats an embedded DB at this scale. SQLite/FTS5 is the escalation path
  only if the data outgrows memory or needs concurrent writers.

**Wide windows are bounded.** Rendering is a single `innerHTML` rebuild, so cost
is total node count. Two caps:

- each company's table shows `ROWS_PER_COMPANY` (25) with a *show all* expander —
  a 2-year window meant 21,416 rows, and nobody reads 400 rows of one company;
- the timeline collapses marks landing on the same pixel and side, keeping the
  largest. They painted over each other anyway, so the strip is unchanged, but a
  busy company costs a node per visible position rather than per transaction.

| Window | Nodes | Render |
|---|---:|---:|
| 2 years, before | 195,746 | 699 ms |
| 2 years, after | 27,529 | 105 ms |
| 2 weeks (default) | 1,881 | 11 ms |

Filter click 74 ms, search 11 ms. True virtualization is the next step if ever
needed — it wasn't, for a 3–7× win from two caps.

---

## The normalized record

Every source maps to one schema (`models.py`):

| field | meaning |
|---|---|
| `issuer_name`, `exchange`, `ticker` | the company the trade is in |
| `insider_name`, `insider_role` | who traded (Director / Officer / Insider …) |
| `entity_type` | `individual` or `institution` (companies, funds, the issuer) |
| `is_issuer_buyback` | the company trading its own shares |
| `transaction_date`, `transaction_type` | ISO date, Buy/Sell/… |
| `shares`, `avg_price`, `total_value`, `currency` | the numbers (CAD/USD) |
| `source`, `source_url`, `scraped_at` | provenance |

A new source = a new module yielding these. Nothing downstream changes.

---

## Watchlist

`companies.json` is keyed by **full legal company name** — the stable identifier.
Tickers are a secondary field because they collide across exchanges: `NFG` is New
Found Gold in Canada but National Fuel Gas in the US; `AE` is a gold explorer
while `AEO` is the apparel retailer.

```json
{ "name": "New Found Gold Corp.", "exchange": "TSXV", "ticker": "NFG", "country": "CA", "confirmed": true }
```

Exchange codes are MarketBeat's: `TSE`, `TSXV`, `NYSE`, `NASDAQ`.

**Add by name.** `issuers.py` resolves a typed name to candidates and the app shows
a picker — when a name is ambiguous you choose, it never silently guesses.

- Backend: TradingView's public symbol-search (covers TSX / TSX-V / CSE / US),
  isolated behind `search_issuers()` so SEDI's issuer search can replace it later.
- `GET /api/search?q=` → candidates; `POST /api/watchlist` → add one.

---

## The application window

A self-contained local web app — the server is Python stdlib only, no X11 needed —
rendering a scrollable feed of boxes, one per insider, grouped by company. Each
shows buy/sell/total counts, shares and dollars, a buy↔sell ratio bar, and who
traded (name, role, individual/institution/buyback badge).

```bash
insight              # serve on http://127.0.0.1:8765 + open a browser
insight --window     # chromeless desktop window
insight --port 9000
insight --no-browser # headless box: open the URL yourself
```

`--window` uses Chrome's `--app=` mode, and the window's lifetime owns the server —
close it and the server stops. It runs in a dedicated profile so it never disturbs
your main browser, and finds Chrome, Edge, Chromium or Brave automatically (falling
back to Playwright's Chromium) on all three platforms.

**Linux desktop entry:**

```bash
./install-desktop.sh              # add InSight to the application menu
./install-desktop.sh --uninstall
```

**macOS / Windows** — run `insight --window`, or pin it: a one-line `.command` file
or Automator app on macOS; a Start-Menu shortcut on Windows (the `insight.exe` shim
lives in the uv tools bin, `uv tool dir`).

Watchlist companies with no data appear as empty cards, so coverage gaps stay
visible.

### API

| Route | Purpose |
|---|---|
| `GET /api/data?months=N` | Companies view, filtered to the window |
| `GET /api/insiders` | Insiders view (`/api/people` is the pre-rename alias) |
| `GET /api/search?q=` | Issuer-name candidates |
| `GET /api/sedi-page?exchange=&ticker=` | A saved SEDI report page |
| `GET,POST /api/notes` | Per-company notes |
| `GET,POST /api/settings` | Theme preferences |
| `GET,POST /api/autostart` | Open-at-login toggle |
| `GET /api/notify/config` | Alarms + channel settings (a projection, see below) |
| `POST /api/notify/settings`, `/api/notify/test` | Channel setup, test send |
| `POST,DELETE /api/watchlist`, `/api/alarms` | Add / remove |
| `POST /api/refresh`, `GET /api/refresh/status` | Trigger and poll a scrape |

---

## Alarms & notifications (`notify.py`)

Set an alarm on a **company** ("any insider trade in ATH") or a **person**
("whenever Eric Sprott trades, anywhere") via the 🔔 button.

The two halves live apart on purpose: the **Alarms tab** lists what you watch
(split Companies / Insiders), while delivery setup sits behind **Settings ⚙ ▸
Notifications** — configured once, so it shouldn't sit in front of the list you
actually check. Both read the same `notify.json`; the tab warns when alarms exist
but no channel is enabled.

After every scrape, `evaluate_and_notify` fires any alarm whose target has a
transaction newer than when the alarm was set:

- **Email** — SMTP (e.g. a Gmail app password), HTML with the logo CID-embedded.
- **ntfy** — a free push topic, no credentials.
- No SMS: carrier email gateways are deprecated and unreliable.

State lives in `notify.json` **in the app folder, never the repo** — it holds the
SMTP password (masked in API responses). Sends are best-effort and wrapped, so a
notification failure never breaks a scrape, and an alarm's `seen` baseline only
advances once a channel actually delivered — a transient outage retries.

### Three bounded things

- **Alarms only look back `ALERT_HORIZON_DAYS` (90).** `seen` used to re-baseline
  to an alarm's *entire* history on every fire, so it grew forever — 103 alarms
  reached 28,604 keys and 3.1 MB, re-read and re-written every scrape. Bounding
  the scan makes older keys safe to forget (→ 1,731 keys, 0.2 MB); stale ones drop
  on the next scrape, so upgrades self-migrate. Trade-off accepted: a backfilled
  old filing won't alert, and an alert about last year's trade is noise, not news.
- **`/api/notify/config` publishes a projection.** Returning alarms verbatim
  shipped those `seen` keys to a browser that never reads them — 2.8 MB against
  56 KB of actual data, the app's largest payload. `public_config` whitelists what
  the UI renders (id, type, label, name, exchange, ticker, created) → ~16 KB. A
  whitelist, not a `seen`-shaped blocklist, so a new bookkeeping field can't
  silently start being published.
- **Every notification is traceable.** Title is always `InSight: <target>` —
  naming *what* fired, never a bare count. Each carries a monotonic index in the
  body (`#N`) and is appended to `notifications.log` (JSONL, append-only): index,
  timestamp, target, message lines, per-channel outcome. The next index derives
  from the log itself, and logging is best-effort.

---

## Open at login (`autostart.py`)

Writes the file the platform already looks for, rather than inventing a mechanism:

```
Linux    ~/.config/autostart/insight.desktop      XDG Desktop Entry
macOS    ~/Library/LaunchAgents/<label>.plist     launchd, RunAtLoad
Windows  %APPDATA%\…\Startup\InSight.cmd          Startup folder
```

All three are per-user files in the user's own home: no admin rights, no
system-wide daemon, and deleting the file is a complete uninstall — deliberate,
because something that starts itself at login should be switchable off even by
someone who no longer has the app to switch it off with. The Startup page shows the
exact path for that reason.

- **Absolute path, resolved at enable time.** A login session often has a different
  PATH than the terminal, and a bare command name is the classic way an autostart
  entry silently does nothing. Falls back to `python -m insight.app` in a source
  checkout.
- **The command is a list of arguments, never a joined string.** A home directory
  with a space is ordinary on macOS and Windows, and all three formats treat a bare
  space as an argument separator — joining then splitting turned
  `/Users/Jo Smith/.local/bin/insight` into two broken arguments and the entry
  silently never launched. Each format quotes its own way: the Desktop Entry
  `Exec=` key per spec, `start ""` with the path quoted on Windows, and the plist
  built with `plistlib` so `&`, `<` and `>` are escaped properly.
- **`RunAtLoad` but deliberately not `KeepAlive`** — this is an app the user may
  close, not a daemon to resurrect.
- **Windows uses `start ""`** so no console window lingers, and is written with
  `newline=""`: the content already carries CRLF, and Python's translation would
  otherwise produce `\r\r\n`.

Tests fake `sys.platform` and `Path.home()`, so all three platforms are covered
wherever they run, and the plist is parsed with `plistlib` rather than
string-matched.

---

## Themes

Every colour comes from a CSS variable, so a theme is just a re-declaration of the
same set under `[data-theme="id"]`. Ten ship, shelved by `mode`:

- **Dark** — Dark (the `:root` default), Midnight, Terminal, Caramel, Chic
- **Light** — Light, Newsprint, Sage, Lemon, Canadian

Terminal is the only one that also swaps `--font`, to a monospace stack.

Adding one means **three edits that must agree**, all test-enforced:

1. a `[data-theme="id"]` block declaring **every** variable — a missing one
   silently inherits the Dark value, which is how a light theme grows one
   unreadable dark patch;
2. an entry in the `THEMES` array in `webui/index.html` (id, name, description);
3. the id in `THEMES` in `settings.py`, which validates what gets saved.

`tests/webui/theme.test.mjs` enforces: stylesheet and picker agree; every theme
declares the full variable set; no colour is hardcoded outside a theme block;
every value parses as a colour; a declared `mode` matches its actual background
luminance; the accent never doubles as buy or sell.

Plus **WCAG contrast** — body text ≥ 7:1 (AAA); secondary text, accents, button
ink and buy/sell ≥ 4.5:1 (AA). Not decoration: Sage originally shipped a mid-green
`--buy` at 3.16:1 on green paper, and buy/sell carry the meaning of the whole app.

### Following the system

`settings.json` holds four fields, not one:

```json
{"theme": "dark", "auto": false, "auto_dark": "dark", "auto_light": "light"}
```

- `auto` off → paint `theme`. On → ask the OS via `prefers-color-scheme` and paint
  `auto_dark` or `auto_light`. That's why the two shelves earn their keep: while
  following, a click sets that shelf's pick, so Chic at night and Sage by day.
- `theme` is untouched throughout, so turning the toggle off restores the
  hand-picked theme.
- The backend validates each field against its own shelf — a light theme stored as
  `auto_dark` would make the app get *brighter* when the OS goes dark.
- Stored server-side, so it survives a cleared cache and follows the user between
  a browser tab and the `--window` app (different profiles).
- Also mirrored to localStorage and applied by `<script id="theme-boot">` in
  `<head>`: the server copy can't be read before first paint, so without that
  cache every load flashes the default. It caches the *preference*, not the
  resolved theme — the OS may have changed since, so the boot script re-resolves.

Two subtleties in the live-update path, both of which bit during development:

- the `MediaQueryList` is held in a module-level binding — an unreferenced one can
  be collected and take its listener with it;
- the theme is re-resolved on `visibilitychange`, because not every environment
  delivers `change` (Chromium under devtools colour-scheme emulation doesn't).

---

## Layout

```
pyproject.toml        packaging + the `insight` / `insight-scrape` commands
install-desktop.sh    Linux app-menu launcher installer
insight/
  app.py              local HTTP server + API + the --window launcher
  scrape.py           insight-scrape CLI (targets, output, prune commands)
  marketbeat.py       MarketBeat scraper (Playwright + stealth) + discovery
  sedi.py             SEDI scraper (headful, persistent profile, bot-walled)
  store.py            dated snapshots -> one deduplicated, incrementally folded store
  aggregate.py        records -> company / insider views (+ in-memory access cache)
  models.py           InsiderTransaction schema + parsing helpers
  notify.py           alarms + notifications (email / ntfy)
  notes.py            per-company user notes
  settings.py         theme preferences; kept apart from notify.json
  profiles.py         Chromium cache caps + pruning (keeps cookies / CAPTCHA)
  autostart.py        per-user 'open at login' entry, per OS convention
  issuers.py          name -> issuer-candidate resolver (+ watchlist add)
  paths.py            per-user data / config / profile dirs (cross-platform)
  companies.default.json  seed watchlist, copied to the app folder on first run
  webui/index.html    the single-page UI
```

The editable watchlist and all output live in the per-user app folder, not the repo.

---

## Tests

```bash
uv sync --group dev            # pytest, ruff, mypy, …
uv run pytest                  # everything, incl. the Node UI suite via a bridge
node --test tests/webui/       # the browser-UI suite alone
uv run ruff check insight tests
uv run mypy insight            # strict on the pure core; glue modules relaxed
```

Three layers, each covering what the one below it can't:

### 1. `tests/*.py` — pure logic (pytest)

Parsing, classification, aggregation math, average cost, history merge/dedup,
delisted filtering, watchlist add/remove, store folding, notes, settings, profile
pruning, autostart, path handling. No network, no browser.

Network/browser code is isolated behind pure helpers (`_extract_tickers`,
`_no_insider_page_kind`, `_row_to_record`, …) so it's tested with sample inputs
rather than live HTTP. Don't unit-test the live fetch.

`tests/test_app_http.py` covers the HTTP layer for real: it binds a
`ThreadingHTTPServer` on an ephemeral port and talks to it over loopback —
routing, status codes, and the JSON shapes the UI depends on. Every path the
handler touches (`DATA_DIR`, `CONFIG`, the `paths.*_file()` helpers) is
monkeypatched into `tmp_path`, so a test can never touch the developer's own app
folder, and `_do_refresh` is stubbed because the real job launches a browser.

### 2. `tests/webui/` — UI logic (Node's built-in runner)

All the app's interaction logic lives in `index.html`, so it gets its own suite —
on `node --test`, so there is still **no npm install, no `package.json`, no build
step**.

`harness.mjs` reads the real `index.html`, extracts its `<script>`, and evaluates
it in a `node:vm` against a small DOM stub, so the tests exercise shipped code
rather than a copy. Two gotchas when writing them:

- Top-level `function` declarations land on the vm context (`ctx`); `let`/`const`
  bindings (`STATE`, `BULLET`, `NAV_MAX`, arrow-function helpers) are lexically
  scoped and reached through `lex`, which the harness exposes via live getters.
- Arrays returned from the vm carry that realm's prototype — wrap in `Array.from()`
  before `assert.deepEqual`.

Covers bullet normalization and note escaping, timeline geometry (nothing clipped,
radii ordered by share count), the back-stack state machine (typing collapses to
one step, the cap drops the oldest), cross-link markup, the theme palette contract,
alarm grouping, and markup invariants — including that the preselected `<option>`
and `STATE.range` still agree, the same fact stated in two places.

### 3. `tests/test_e2e_browser.py` — real browser (Playwright)

The vm harness has no renderer, no focus model and no keyboard, so a keydown
handler can look perfect and never fire, and a layout can overflow a phone screen
with every unit test green. That gap is covered by driving the real page in
Chromium — already a scraper dependency, so nothing new is installed. Tests *skip*
rather than fail when the browser binary is missing; CI installs it explicitly.

Scope is deliberately narrow — only what needs a browser:

- **real keys** — Enter opening the next bullet, Backspace removing an empty one,
  Ctrl+Enter saving, Escape discarding;
- **real focus** — Tab / Shift+Tab cycling inside the settings dialog, focus
  returning to the ⚙ on close, Alt+← inert while the dialog is up;
- **computed styles** — a theme actually repainting the body, Terminal actually
  switching to monospace;
- **geometry** — no horizontal overflow at 375 px, the dialog fitting a phone
  screen, the timeline staying one thin row, the table scrolling inside its card.

Every page is watched for uncaught JS errors, failing the test on teardown.
Anything provable without a browser belongs in `tests/webui/` — these are ~6 s
against ~1 s for everything else.

### Concurrency

The server is a `ThreadingHTTPServer`, so any read-modify-write of a JSON file
needs a lock **plus a unique temp name** + `os.replace` (see `notes.py`,
`store.py`). A fixed temp name lets two writers interleave.
`tests/test_concurrency.py` proves it by racing overlapping writers — before the
fix, 30 concurrent note saves left 2 notes.

---

## CI

`.github/workflows/` runs on every push and PR:

- ruff (check + format), mypy, pytest;
- the Node UI suite — Node is installed explicitly, because the pytest bridge
  *skips* when node is missing, and without that step the UI tests would quietly
  stop running instead of failing;
- Chromium for the e2e tests, cached on the resolved Playwright version so the
  ~150 MB download happens once;
- `gitleaks` over the full history, a server-side backstop to the pre-commit hook.
