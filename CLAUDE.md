# CLAUDE.md — InSight Project Guidelines

## Project Overview
**InSight** is a local desktop/web app that shows **insider stock transactions** for
Canadian/US companies. It runs a tiny local HTTP server (`insight`) serving a
single-page UI, backed by a Playwright scraper (`insight-scrape`) that collects
data from MarketBeat. Everything is single-user and local — no accounts, no
cloud, no paid data services.

### Core features
- **Companies tab** — insider activity grouped per watchlist company.
- **People tab** — the same data re-sliced by *person*, spanning every scraped
  company (watchlist or not), so you can follow one insider across companies.
- **Watchlist** — add companies by name (TradingView resolver) / remove them.
- **Refresh** — re-scrape from the UI; optional "Scan all TSE (~215)" to widen
  coverage via MarketBeat's exchange listing.
- **History accumulation** — each scrape writes a dated snapshot; the app merges
  all snapshots (deduped) so the window deepens over time.
- **Delisting hygiene** — acquired/delisted tickers are detected during scrape,
  dropped from cache, and hidden from views (self-healing).
- **Alarms** — per-company / per-person alarms that notify (email via SMTP, or
  ntfy push) when a new transaction appears; evaluated after each scrape.

## Technical stack
- **Env / deps:** `uv` (dependency-groups; `uv sync --group dev`).
- **Language:** Python ≥3.12, strict type hints on the pure core.
- **Scraper:** Playwright + playwright-stealth (Chromium, headless).
- **UI:** a single self-contained `insight/webui/index.html` (vanilla JS), served
  by `http.server`. No build step, no frontend deps.
- **Storage:** plain JSON snapshots under a per-user app folder (see `paths.py`).
  Deliberately **not** a database — in-memory caching beats SQLite at this scale;
  SQLite/FTS5 is the documented escalation path.

## Commands
```bash
uv run insight                 # serve + open browser
uv run insight --window        # chromeless desktop window
uv run insight-scrape          # scrape the watchlist
uv run insight-scrape --discover   # + MarketBeat's ~215 TSE universe

uv run pytest                  # tests (pure, no network/browser)
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
- **License headers:** every `.py`/`.sh` gets the SPDX PolyForm header (inserted
  by pre-commit). Don't hand-edit.
- **Source-agnostic model:** all data is normalized to `InsiderTransaction`
  (`models.py`). A new data source = a new module yielding these; nothing
  downstream changes.
- **The watchlist is the source of truth for the Companies tab** (gated). The
  People tab is intentionally *not* gated — it spans all scraped data.
- **Tests are pure**: network/browser logic is isolated behind testable helpers
  (`_extract_tickers`, `_no_insider_page_kind`, `_row_to_record`, …). Add tests
  for parsing/math/aggregation logic; don't unit-test the live fetch.

## Architecture (data flow)
```
insight-scrape ──> marketbeat.scrape_many ──> models.InsiderTransaction
                                                   │
                        scrape.write_outputs ──> data/insider_YYYY-MM-DD.json (dated snapshots)
                                                   │
        app (HTTP) ──> aggregate.load_view / load_people_view
                          │  merges all snapshots (cached), filters delisted + months
                          ▼
                    webui/index.html  (Companies / People tabs, client-side search)
```

## Layout
```
insight/
  app.py          local HTTP server + API (/api/data, /api/people, /api/watchlist, /api/refresh)
  scrape.py       insight-scrape CLI (targets, output, --discover)
  marketbeat.py   Playwright scraper + discovery + cache/delisted helpers
  aggregate.py    snapshots -> company/people views (+ in-memory access cache)
  issuers.py      name -> issuer resolver (TradingView) + watchlist add/remove
  notify.py       alarms + notifications (email/ntfy), evaluated after each scrape
  models.py       InsiderTransaction schema + parsing helpers
  paths.py        per-user data/config/cache dirs (cross-platform)
  webui/index.html  the single-page UI
tests/            pytest (parsing, math, aggregation, add/remove, cache)
```
See `DEVELOPER_README.md` for deeper docs (scheduling, delisting, caching,
data-source rationale).
