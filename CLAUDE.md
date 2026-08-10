# CLAUDE.md — InSight Project Guidelines

## Overview

**InSight** shows **insider stock transactions** for Canadian/US companies. A tiny
local HTTP server (`insight`) serves a single-page UI, backed by a Playwright
scraper (`insight-scrape`). Single-user and local — no accounts, no cloud, no paid
data services.

### Core features

- **Companies tab** — insider activity grouped per watchlist company.
- **Insiders tab** — the same data re-sliced by *person*, spanning every scraped
  company (watchlist or not). Served by `/api/insiders`; `/api/people` is kept as
  an alias for the old name.
- **Cross-links + back-stack** — an insider name opens that insider, a company on
  their card opens that company; Back (and Alt+←) walks up to 10 states.
- **Two data sources** — MarketBeat by default (unattended); **SEDI** on demand
  via ⛏ or `--source sedi` for TSXV/CSE names MarketBeat misses. See below.
- **Person-first search** — `insight-scrape --insider FAMILY_NAME` uses SEDI's
  cross-issuer insider search to list every company someone filed against and
  flag those missing from the watchlist. The only way to discover an untracked
  company; everything else here is company-first. Reports only, writes nothing.
- **Watchlist** — add by name (TradingView resolver) / remove.
- **Refresh** — re-scrape from the UI; optional "Scan all TSE (~215)".
- **Notes** — free-text per company (a bullet list in the UI), shown above that
  company's activity. User-authored; never touched by the scraper.
- **History accumulation** — each scrape writes a dated snapshot; `store.py` folds
  them deduped into one store, so the window deepens over time. Defaults to the
  **last 2 weeks**; wider ranges are one click away.
- **Delisting hygiene** — acquired/delisted tickers are detected during scrape,
  dropped from cache, hidden from views (self-healing).
- **Alarms** — per-company / per-person, notifying by email (SMTP) or ntfy push,
  evaluated after each scrape. The **Alarms tab** lists what's watched (split
  Companies / Insiders); delivery setup lives in **Settings ▸ Notifications**.
- **Open at login** — optional per-user autostart entry (XDG desktop file /
  launchd agent / Startup shortcut) running `insight --window`, toggled in
  **Settings ▸ Startup**.
- **Themes** — ten palettes, shelved dark/light (Dark, Midnight, Terminal,
  Caramel, Chic | Light, Newsprint, Sage, Lemon, Canadian), in **Settings ▸
  Appearance**, stored server-side. **Match my system** follows
  `prefers-color-scheme` using the pick from each shelf.

## Technical stack

- **Env / deps:** `uv` (dependency-groups; `uv sync --group dev`).
- **Language:** Python ≥3.12, strict type hints on the pure core.
- **Scrapers:** Playwright + playwright-stealth. MarketBeat headless; SEDI headful
  with a persistent profile (it is CAPTCHA-walled).
- **UI:** one self-contained `insight/webui/index.html` (vanilla JS) served by
  `http.server`. No build step, no frontend deps.
- **Storage:** plain JSON in a per-user app folder (`paths.py`). Each scrape writes
  a dated snapshot; `store.py` folds them **once** into a deduplicated `store.json`
  and thereafter reads only genuinely new snapshots, so startup never re-parses a
  pile that is mostly repetition. Deliberately **not** a database — in-memory
  caching beats SQLite at this scale; SQLite/FTS5 is the documented escalation path.

## Commands

```bash
uv run insight                 # serve + open browser
uv run insight --window        # chromeless desktop window
uv run insight-scrape          # scrape the watchlist (MarketBeat)
uv run insight-scrape --discover        # + MarketBeat's ~215 TSE universe
uv run insight-scrape --source sedi     # official SEDI; opens a visible browser
uv run insight-scrape --insider Sprott  # SEDI person search: which companies?
uv run insight-scrape --prune-snapshots      # drop folded snapshots, keep newest 2
uv run insight-scrape --prune-browser-cache  # clear Chromium caches (keeps cookies)

uv run pytest                  # everything, incl. the UI suite via a bridge
node --test tests/webui/       # browser-UI tests alone (Node's runner, no npm deps)
uv run playwright install chromium   # once, for the end-to-end tests
uv run ruff check insight tests
uv run ruff format insight tests
uv run mypy insight            # strict on the core
```

Typically installed editable (`uv tool install --editable .`): UI edits show on
reload, Python edits need an app restart.

## Conventions & standards

- **Tooling lives in `pyproject.toml`** (ruff, pytest, coverage, mypy) — no
  standalone config files. Ruff line length 100; lint set E/F/I/UP/B/C4/SIM/RUF.
  Pre-commit runs ruff, license headers, and gitleaks.
- **Type checking:** the pure core (`models`, `aggregate`, `issuers`, `scrape`,
  `paths`) is **mypy --strict clean**. Playwright/HTTP glue (`marketbeat`, `sedi`,
  `app`) is relaxed via per-module overrides. Keep new pure logic strict.
- **License:** Apache-2.0. Every `.py`/`.sh` gets the SPDX header from
  `.license-header.txt` (inserted by pre-commit) — don't hand-edit. The `.mjs` UI
  tests carry the same header with `//` comments, applied by hand since the hook
  only covers `.py`/`.sh`.
- **Source-agnostic model:** everything normalizes to `InsiderTransaction`
  (`models.py`). A new source = a new module yielding these; nothing downstream
  changes. Non-default sources tag their snapshot filename
  (`insider_sedi_*.json`) so they merge without clobbering.
- **SEDI is deliberately not automatic.** It is bot-walled, so it runs headful with
  a persistent profile and may need a human to solve one CAPTCHA. Never wire it
  into the daily timer, and never delete the profile's cookies — that jar *is* the
  solved challenge.
- **The watchlist is the source of truth for the Companies tab** (gated). The
  Insiders tab is intentionally *not* gated — it spans all scraped data. That
  asymmetry is why a cross-link from an insider to a company can land on nothing;
  the UI says so rather than showing an empty feed. Issuer buybacks are excluded
  from the Insiders tab (the "insider" is the company), so those rows don't link.
- **Tests are pure:** network/browser logic is isolated behind testable helpers
  (`_extract_tickers`, `_no_insider_page_kind`, `_row_to_record`, …). Test
  parsing/math/aggregation; don't unit-test the live fetch.
- **UI logic is tested too** (`tests/webui/`, Node's built-in runner — still no
  frontend deps). The harness evaluates the real `index.html` in a `node:vm`, so
  new pure UI logic (formatting, geometry, state machines) belongs there.
- **Anything needing a real browser** (keyboard, focus, computed styles, layout)
  goes in `tests/test_e2e_browser.py`. Keep it small — it is ~6× slower than the
  rest of the suite combined.
- **On-disk writes are concurrent:** the server is a `ThreadingHTTPServer`, so any
  read-modify-write of a JSON file needs a lock plus a unique temp name +
  `os.replace` (see `notes.py`, `store.py`). A fixed temp name lets two writers
  interleave.

## Architecture (data flow)

```
insight-scrape ──> marketbeat.scrape_many ──> models.InsiderTransaction
               └─> sedi.SediScraper       ──┘        │
                                                     │
                          scrape.write_outputs ──> data/insider_[sedi_]YYYY-MM-DD.json
                                                     │
                                store.sync ──> data/store.json (deduped; folds only new)
                                                     │
          app (HTTP) ──> aggregate.load_view / load_insiders_view
                            │  reads the store (cached), filters delisted + the window
                            ▼
                      webui/index.html  (Companies / Insiders tabs, client-side search)
```

## Layout

```
insight/
  app.py          local HTTP server + API (/api/data, /api/insiders, /api/watchlist,
                  /api/notes, /api/settings, /api/autostart, /api/sedi-page, /api/refresh)
  scrape.py       insight-scrape CLI (targets, output, --discover, --source, prunes)
  marketbeat.py   MarketBeat scraper + discovery + cache/delisted helpers
  sedi.py         SEDI scraper (headful, persistent profile, saved report pages)
  store.py        dated snapshots -> one deduplicated store, folded incrementally
  aggregate.py    records -> company/insider views (+ in-memory access cache)
  notes.py        per-company user notes (EXCH:TICKER -> text)
  settings.py     theme preferences; separate from notify.json
  profiles.py     Chromium cache caps + pruning (keeps cookies/CAPTCHA)
  autostart.py    per-user 'open at login' entry, per OS convention
  issuers.py      name -> issuer resolver (TradingView) + watchlist add/remove
  notify.py       alarms + notifications (email/ntfy), evaluated after each scrape
  models.py       InsiderTransaction schema + parsing helpers
  paths.py        per-user data/config/cache dirs (cross-platform)
  webui/index.html  the single-page UI
tests/            pytest + tests/webui/ (Node) + test_e2e_browser.py (Playwright)
```

See `DEVELOPER_README.md` for deeper docs (data sources, scheduling, delisting,
caching, storage).

## Themes

A theme is a `[data-theme="id"]` block re-declaring every CSS variable — nothing
else in the stylesheet knows which is active, and **no colour may be hardcoded
outside a theme block** (a literal can't be re-themed). Adding one means three
edits that must agree: the stylesheet block, the `THEMES` array in
`webui/index.html`, and `THEMES` in `insight/settings.py`. Tests enforce all three,
plus that every theme declares the complete variable set — a missing variable
silently inherits Dark's value — and WCAG contrast on text, accents and buy/sell.
