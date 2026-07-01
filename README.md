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

The scripts carry their Python deps inline ([PEP 723](https://peps.python.org/pep-0723/)),
so [`uv`](https://docs.astral.sh/uv/) provisions and caches an environment on
first run — no manual virtualenv to create or activate.

```bash
# one-time: download the Chromium browser binary Playwright drives
# (uv manages Python packages; the browser is a separate ~150 MB cache)
uv run --with playwright playwright install chromium

# scrape the watchlist in companies.json
uv run --script scrape_insider.py

# or ad-hoc tickers (EXCHANGE:TICKER)
uv run --script scrape_insider.py --tickers TSE:FNV TSE:CNQ NYSE:AEO

# show the browser (useful if your IP gets a bot challenge)
uv run --script scrape_insider.py --headful
```

<details>
<summary>Prefer pip + a virtualenv?</summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python scrape_insider.py
```
</details>

### Output (per run, stamped with the date)

```
data/insider_YYYY-MM-DD.json          all records (source-agnostic schema)
data/insider_YYYY-MM-DD.csv           same, flat CSV
data/by_ticker/TSE_FNV_YYYY-MM-DD.csv one CSV per company
```

### Example summary

```
TSE:FNV    8 txns  (buys=0 sells=8 institutional=0)  latest=2025-11-26
TSE:CNQ   15 txns  (buys=2 sells=13 institutional=0) latest=2026-03-24
TSE:ATH   27 txns  (buys=27 sells=0 institutional=27) latest=2026-05-29   <- issuer buyback
```

## The application window

A self-contained local web app (Python stdlib only — no extra deps, no X11
display required) renders the data as a scrollable feed of **boxes**, one per
insider/entity, grouped under each watchlist company. Each box shows the
buy / sell / total transaction counts, the shares and dollar amounts bought
and sold, a buy↔sell ratio bar, and who was trading (name, role, and an
individual / institution / buyback badge).

```bash
uv run --script app.py            # serve on http://127.0.0.1:8765 + open a browser
uv run --script app.py --window   # open as a standalone desktop window (chromeless)
uv run --script app.py --port 9000
uv run --script app.py --no-browser   # headless box: open the URL yourself
```

### Desktop launcher

`--window` opens the UI as a chromeless desktop window (via Chrome's `--app=`
mode) whose lifetime owns the server — close the window and the server stops.
The window runs in its own dedicated Chrome profile so it never disturbs your
main browser.

Install it as a normal app (icon in the app grid) that runs
`uv run --script app.py --window`:

```bash
./install-desktop.sh              # add InSight to your application menu
./install-desktop.sh --uninstall  # remove it
```

It reads the newest `data/insider_YYYY-MM-DD.json`. Re-run `scrape_insider.py`
to refresh the data, then reload the page. Watchlist companies with no data
(e.g. uncovered TSX-V names) appear as empty cards so coverage gaps are visible.

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

The transactions need only a 1–2 day freshness, so a daily cron is plenty:

```cron
30 6 * * *  cd /home/ramin/workspaces/InSight && ~/.local/bin/uv run --script scrape_insider.py >> data/cron.log 2>&1
```

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

## Layout

```
app.py                the application window (local web server + UI)
scrape_insider.py     CLI entry point (config, output, summary)
companies.json        the watchlist
webui/index.html      the single-page UI (scrollable insider boxes)
insight/
  models.py           InsiderTransaction schema + parsing helpers
  marketbeat.py       MarketBeat scraper (Playwright + stealth)
  aggregate.py        flat records -> per-company / per-insider boxes
  issuers.py          name -> issuer-candidate resolver (+ watchlist add)
data/                 dated outputs
```

Adding a new source = a new module that yields `InsiderTransaction` objects;
nothing downstream changes.
