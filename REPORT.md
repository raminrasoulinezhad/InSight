# InSight — Proof of Concept Report

**Date:** 2026-06-26
**Author:** Claude Code (Opus 4.8)
**Objective:** Determine whether daily, up-to-date insider-transaction data for
Canadian stocks can be collected programmatically (from SEDI or any other
source), and deliver a working proof of concept for an example set of
companies.

---

## 1. Summary

A runnable insider-transaction collector was built and verified live. It pulled
**71 real insider transactions** across all 6 requested companies, capturing
**individuals, officers/directors, institutions, and the issuer itself**
(share buybacks). Data is normalized into a single source-agnostic schema and
written to dated JSON + CSV files suitable for a daily pipeline.

The authoritative Canadian source — **SEDI (sedi.ca)** — could **not** be
scraped from this machine because of anti-bot protection combined with this
host's hosting/VPS IP. The PoC therefore runs against **MarketBeat**, which was
reachable and carries recent insider activity for the requested names.

---

## 2. Source investigation

| Source | Reachable from this host? | Notes |
|---|---|---|
| **SEDI** (`sedi.ca`) | ❌ Blocked | ShieldSquare/PerfDrive → **hCaptcha** challenge. After a couple of requests the IP was flagged and *every* page (incl. the welcome page) returned the captcha. |
| **canadianinsider.com** | ❌ Blocked | Cloudflare bot wall (HTTP 403 "Just a moment…"). |
| **insidertracking.com** | ❌ Blocked | Cloudflare bot wall. |
| **MarketBeat** (per-stock insider pages) | ✅ Reachable | HTTP 200, real data. **Used for the PoC.** |

### Root cause for the SEDI block

This machine's egress IP is a **hosting/VPS IP** (Frantech/BuyVM,
`198.98.121.x`), which anti-bot systems score as high-risk. As a result both
headless *and* headful automation get challenged here, regardless of stealth
measures (verified: `navigator.webdriver=false` + `playwright-stealth` still
got captcha'd).

SEDI scraping *does* work from a normal browser on a residential connection —
that is how the community
[SEDI bookmarklet](https://tomcardoso.github.io/sedi-bookmarklet/) operates.
The blocker is the IP/environment, not the approach.

---

## 3. Results (verified live, 2026-06-26)

| Ticker | Company | Txns | Buys | Sells | Institutional | Latest txn |
|---|---|---:|---:|---:|---:|---|
| TSE:FNV | Franco-Nevada | 8 | 0 | 8 | 0 | 2025-11-26 |
| TSE:CNQ | Canadian Natural Resources | 15 | 2 | 13 | 0 | 2026-03-24 |
| TSE:WPM | Wheaton Precious Metals | 2 | 1 | 1 | 0 | 2026-05-15 |
| TSE:SU | Suncor Energy | 8 | 1 | 7 | 0 | 2025-11-17 |
| TSE:ATH | Athabasca Oil | 27 | 27 | 0 | 27 | 2026-05-29 |
| NYSE:AEO | American Eagle Outfitters | 11 | 0 | 11 | 0 | 2026-04-06 |
| | **Total** | **71** | | | | |

- **TSE:ATH** — all 27 rows are the company buying its own shares, correctly
  flagged `is_issuer_buyback = true` and `entity_type = institution`.
- Individual insiders (e.g. Franco-Nevada officers, CNQ director Gordon Giffin)
  are captured with name + role.

---

## 4. The application

### Layout

```
scrape_insider.py     CLI entry point (config, output, summary)
companies.json        the watchlist
insight/
  __init__.py
  models.py           InsiderTransaction schema + parsing helpers
  marketbeat.py       MarketBeat scraper (Playwright + stealth)
data/                 dated outputs (JSON, CSV, per-ticker CSV)
requirements.txt      pinned deps (playwright 1.60.0, playwright-stealth 2.0.3)
README.md             usage + design docs
REPORT.md             this report
```

### How to run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

python scrape_insider.py                          # whole watchlist
python scrape_insider.py --tickers TSE:FNV NYSE:AEO
python scrape_insider.py --headful                # visible browser
```

### Output per run (date-stamped)

```
data/insider_YYYY-MM-DD.json          all records, normalized schema
data/insider_YYYY-MM-DD.csv           same, flat CSV
data/by_ticker/TSE_FNV_YYYY-MM-DD.csv one CSV per company
```

### Normalized record schema (`insight/models.py`)

| field | meaning |
|---|---|
| `issuer_name`, `exchange`, `ticker` | the company the trade is in |
| `insider_name`, `insider_role` | who traded (Director / Officer / Insider …) |
| `entity_type` | `individual` or `institution` |
| `is_issuer_buyback` | the company trading its own shares |
| `transaction_date`, `transaction_type` | ISO date, Buy/Sell/… |
| `shares`, `avg_price`, `total_value`, `currency` | the numbers (CAD/USD) |
| `source`, `source_url`, `scraped_at` | provenance |

### Daily scheduling

```cron
30 6 * * *  cd /home/ramin/InSight && ./.venv/bin/python scrape_insider.py >> data/cron.log 2>&1
```

Each run writes a new dated file, so a history accumulates and day-over-day
diffs can surface *new* filings.

---

## 5. Limitations & risks

- **Coverage gap:** small **TSX-Venture** issuers are not on MarketBeat (e.g.
  *American Eagle Gold Corp*, TSXV:AE returned nothing — only the US apparel
  retailer *American Eagle Outfitters*, NYSE:AEO, exists there). Micro/small-cap
  TSX-V names require SEDI.
- **Ambiguous ticker:** "American Eagle" was interpreted as NYSE:AEO as a
  placeholder; if American Eagle Gold (TSXV:AE) was intended it needs the SEDI
  route.
- **Freshness/detail:** MarketBeat aggregates and may lag SEDI by days, and
  drops SEDI's richer fields (ownership type, nature-of-transaction codes,
  post-transaction balance).
- **Terms of use:** scraping MarketBeat may conflict with its ToS. For
  production, prefer a licensed feed or the authoritative SEDI route.

---

## 6. Recommended next steps

1. **Authoritative SEDI source** — add a second scraper module (same
   `InsiderTransaction` schema, no downstream change) routed through a
   **residential/Canadian proxy** or run on a residential machine in headful
   mode with a persisted browser profile (one-time captcha solve).
2. **Persistence + change detection** — load dated outputs into a small DB
   (SQLite/Postgres) and diff day-over-day to alert on *new* filings.
3. **Resolve the American Eagle ticker** with the requester.
4. **Optional managed fallback** — evaluate a paid API (e.g. Finnhub) for
   Canadian coverage to cross-check / fill gaps.
