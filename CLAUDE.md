# CLAUDE.md — InSight Project Guidelines

## Project Overview
**InSight** is a local desktop/web app that shows **insider stock transactions** for
Canadian/US companies. It runs a tiny local HTTP server (`insight`) serving a
single-page UI, backed by a Playwright scraper (`insight-scrape`) that collects
data from MarketBeat. Everything is single-user and local — no accounts, no
cloud, no paid data services.

### Core features
- **Companies tab** — insider activity grouped per watchlist company.
- **Insiders tab** — the same data re-sliced by *person*, spanning every scraped
  company (watchlist or not), so you can follow one insider across companies.
  Served by `/api/insiders`; `/api/people` is kept as an alias for the old name.
- **Cross-links + back-stack** — an insider name on a company opens that insider,
  a company on an insider's card opens that company; a Back button (and Alt+←)
  walks up to 10 previous states back.
- **Watchlist** — add companies by name (TradingView resolver) / remove them.
- **Refresh** — re-scrape from the UI; optional "Scan all TSE (~215)" to widen
  coverage via MarketBeat's exchange listing.
- **Notes** — free-text notes per company (a bullet list in the UI), shown above
  that company's activity. Purely user-authored; never touched by the scraper.
- **History accumulation** — each scrape writes a dated snapshot; the app folds
  all snapshots (deduped) into one store so the window deepens over time. The
  view defaults to the **last 2 weeks**; wider ranges are one click away.
- **Delisting hygiene** — acquired/delisted tickers are detected during scrape,
  dropped from cache, and hidden from views (self-healing).
- **Alarms** — per-company / per-person alarms that notify (email via SMTP, or
  ntfy push) when a new transaction appears; evaluated after each scrape. The
  **Alarms tab** lists what's watched (split Companies / Insiders); the delivery
  setup lives in **Settings ▸ Notifications**.
- **Themes** — ten palettes, shelved dark/light (Dark, Midnight, Terminal,
  Caramel, Chic | Light, Newsprint, Sage, Lemon, Canadian), chosen in
  **Settings ▸ Appearance** and stored server-side. **Match my system** follows
  `prefers-color-scheme`, using the user's pick from each shelf.

## Technical stack
- **Env / deps:** `uv` (dependency-groups; `uv sync --group dev`).
- **Language:** Python ≥3.12, strict type hints on the pure core.
- **Scraper:** Playwright + playwright-stealth (Chromium, headless).
- **UI:** a single self-contained `insight/webui/index.html` (vanilla JS), served
  by `http.server`. No build step, no frontend deps.
- **Storage:** plain JSON under a per-user app folder (see `paths.py`). Each
  scrape writes a dated snapshot; `store.py` folds them **once** into a single
  deduplicated `store.json` and thereafter reads only genuinely new snapshots, so
  startup never re-parses a pile that is mostly repetition. Deliberately **not** a
  database — in-memory caching beats SQLite at this scale; SQLite/FTS5 is the
  documented escalation path.

## Commands
```bash
uv run insight                 # serve + open browser
uv run insight --window        # chromeless desktop window
uv run insight-scrape          # scrape the watchlist
uv run insight-scrape --discover   # + MarketBeat's ~215 TSE universe
uv run insight-scrape --prune-snapshots   # drop folded snapshots, keep newest 2
uv run insight-scrape --prune-browser-cache  # clear Chromium caches (keeps cookies)

uv run pytest                  # tests (pure, no network/browser; runs the UI suite too)
node --test tests/webui/       # browser-UI tests alone (Node's runner, no npm deps)
uv run ruff check insight tests
uv run ruff format insight tests
uv run mypy insight            # strict on the core
```
The app is typically installed editable (`uv tool install --editable .`), so
source edits are live: UI changes show on browser reload; Python changes need an
app restart.

## Conventions & standards
- **Tooling lives in `pyproject.toml`** (ruff, pytest, coverage, mypy) — no
  standalone config files. Ruff line length 100; the lint set is E/F/I/UP/B/C4/
  SIM/RUF. Pre-commit runs ruff, license headers, and gitleaks.
- **Type checking:** the pure core (`models`, `aggregate`, `issuers`, `scrape`,
  `paths`) is **mypy --strict clean**. The Playwright/HTTP glue (`marketbeat`,
  `app`) is relaxed via per-module overrides — the binding-layer analogue of
  MoneyTor's `ui.*` relaxation. Keep new pure logic strict.
- **License:** Apache-2.0. Every `.py`/`.sh` gets the SPDX header from
  `.license-header.txt` (inserted by pre-commit). Don't hand-edit; the `.mjs`
  UI tests carry the same header with `//` comments, applied by hand since the
  hook only covers `.py`/`.sh`.
- **Source-agnostic model:** all data is normalized to `InsiderTransaction`
  (`models.py`). A new data source = a new module yielding these; nothing
  downstream changes.
- **The watchlist is the source of truth for the Companies tab** (gated). The
  Insiders tab is intentionally *not* gated — it spans all scraped data. That
  asymmetry is why a cross-link from an insider to a company can land on nothing;
  the UI says so rather than showing an empty feed. Issuer buybacks are excluded
  from the Insiders tab (the "insider" is the company), so those rows don't link.
- **Tests are pure**: network/browser logic is isolated behind testable helpers
  (`_extract_tickers`, `_no_insider_page_kind`, `_row_to_record`, …). Add tests
  for parsing/math/aggregation logic; don't unit-test the live fetch.
- **UI logic is tested too** (`tests/webui/`, Node's built-in runner — still no
  frontend deps). The harness evaluates the real `index.html` in a `node:vm`, so
  new pure UI logic (formatting, geometry, state machines) belongs there.
- **On-disk writes are concurrent**: the server is a `ThreadingHTTPServer`, so
  any read-modify-write of a JSON file needs a lock plus a unique temp name +
  `os.replace` (see `notes.py`, `store.py`). A fixed temp name lets two writers
  interleave.

## Architecture (data flow)
```
insight-scrape ──> marketbeat.scrape_many ──> models.InsiderTransaction
                                                   │
                        scrape.write_outputs ──> data/insider_YYYY-MM-DD.json (dated snapshots)
                                                   │
                              store.sync ──> data/store.json (deduped; folds only new snapshots)
                                                   │
        app (HTTP) ──> aggregate.load_view / load_insiders_view
                          │  reads the store (cached), filters delisted + the date window
                          ▼
                    webui/index.html  (Companies / Insiders tabs, client-side search)
```

## Layout
```
insight/
  app.py          local HTTP server + API (/api/data, /api/insiders, /api/watchlist,
                  /api/notes, /api/settings, /api/refresh)
  scrape.py       insight-scrape CLI (targets, output, --discover, prune commands)
  marketbeat.py   Playwright scraper + discovery + cache/delisted helpers
  store.py        dated snapshots -> one deduplicated store, folded incrementally
  aggregate.py    records -> company/people views (+ in-memory access cache)
  notes.py        per-company user notes (EXCH:TICKER -> text)
  settings.py     app preferences (theme); separate from notify.json
  profiles.py     Chromium profile cache caps + pruning (keeps cookies/CAPTCHA)
  issuers.py      name -> issuer resolver (TradingView) + watchlist add/remove
  notify.py       alarms + notifications (email/ntfy), evaluated after each scrape
  models.py       InsiderTransaction schema + parsing helpers
  paths.py        per-user data/config/cache dirs (cross-platform)
  webui/index.html  the single-page UI
tests/            pytest (parsing, math, aggregation, add/remove, cache)
```
See `DEVELOPER_README.md` for deeper docs (scheduling, delisting, caching,
data-source rationale).

## Themes
A theme is a `[data-theme="id"]` block re-declaring every CSS variable — nothing
else in the stylesheet knows which is active, and **no colour may be hardcoded
outside a theme block** (a literal can't be re-themed). Adding one means three
edits that must agree: the stylesheet block, the `THEMES` array in
`webui/index.html`, and `THEMES` in `insight/settings.py`. Tests enforce all
three, plus that every theme declares the complete variable set — a missing
variable silently inherits Dark's value.
