# Research: comprehensive, person-searchable Canadian insider data

_Deep-research report, 2026-06-30. Fan-out web search → fetch 20 sources → 51 claims → 25 adversarially verified (21 confirmed / 4 refuted)._

## Question

What is the best programmatically-accessible data source for Canadian insider-trading
filings that covers **all reporting issuers — including TSX Venture (TSXV) and CSE
small-caps** — and can be queried by **insider (person) name across all companies**?

Context: InSight currently scrapes MarketBeat's per-stock insider-trades pages, but
MarketBeat only enumerates ~215 major TSE large-caps and under-covers small-cap venture
names — exactly the names users need.

## Bottom line

There is **no free, official, person-searchable bulk feed** for Canadian insider data.
The authoritative comprehensive source is the CSA's **SEDI** (sedi.ca), but it is
CAPTCHA/bot-walled, its Terms prohibit scraping, and it exposes no API or export.
**Every viable programmatic route is paid.** For a small app, the realistic path is a
**paid third-party aggregator that already licenses SEDI data**.

## Verified findings

### The official systems

- **SEDI — not SEDAR+ — holds Canadian insider reports.** SEDAR+ launched 2023-07-25
  but **explicitly deferred** SEDI and NRD to a future phase that **still has not
  launched as of mid-2026**. _(high confidence, unanimous)_
- **SEDI is the only official source with cross-issuer person-name search** ("Insider
  family name" / "given names" fields) and covers **all reporting issuers incl. small
  caps**. Operational as of 2026-06-30. _(high, unanimous)_
- **SEDAR+ cannot substitute** — its search has no insider/person-name field (only
  issuer/profile name, document type, date range, full-text). _(high, unanimous)_
- **No official API / bulk / machine-readable export** from either system. SEDAR+
  public users are capped at 30 documents per batch; only a UI CSV export of the
  result *list* exists. _(high, unanimous)_
- **Official bulk machine-readable data is paid-only** via a CSA/Alberta Securities
  Commission data-distribution license, **no advertised price** (contact CSA Service
  Desk 1-800-219-5381). Unlike the free US SEC EDGAR. _(medium)_
- **Scraping is barred.** SEDAR+ Terms of Use prohibit automated collection, database
  construction, and mass distribution; both sedi.ca and sedarplus.ca sit behind CAPTCHA
  + Radware/ShieldSquare (perfdrive) bot walls. _(high)_

### Paid aggregators / data APIs

| Source | CA small-cap coverage | Person-level search | API / access | Verdict |
|---|---|---|---|---|
| **Avantis AI** | ✅ ingests *"all insider transactions reported… in Canada on the SEDI system"* | ✅ every SEDI field searchable | ⚠️ no public API docs; export is UI (Excel/PDF); **custom pricing** (sales@avantisai.com, 2-wk trial) | **Best coverage**, programmatic fit unverified |
| **Intrinio** | ❌ SEC/US-only | ✅ genuine by-owner endpoint | ✅ documented v2 REST API | Great API, **wrong country** |
| **Nasdaq Data Link (Quandl)** | ❌ only US SEC forms 3/4/5 (SHARADAR SF2) | — | ✅ | No Canadian insider product |
| **Finnhub** | ⚠️ a search snippet claimed its insider endpoint sources **Canadian SEDI data**, but this **was not verified** (docs page unreadable) | possibly | ✅ affordable dev REST API, free tier | **Unverified but promising — cheapest potential path** |
| **Canadian Insider / INK Research** | ✅ (SEDI aggregator) | ✅ | ⚠️ ~C$590/yr "Ultra Club"; Excel/downloads gated; API not confirmed | Not fully evaluated |

## Refuted claims (do NOT act on these)

- Avantis AI has an *official* real-time CSA/SEDAR+ partnership — **refuted (1-2)**;
  treat Avantis as a licensed reseller, not an official CSA path.
- SEDAR+ already consolidated SEDI/insider disclosure — **refuted (1-2)**.
- SEDAR+ has no API "per the OSC page" — **refuted (1-2)** as overreach.
- Filings are distributed primarily as PDFs, not structured data — **refuted (0-3)**.

## Caveats

- **Time-sensitivity:** SEDI is confirmed operational as of 2026-06-30, but the CSA has
  an unscheduled future SEDAR+ phase that will eventually absorb SEDI. When that lands,
  endpoints, ToU, and any API surface could change — **re-verify before building**.
- Bulk-license pricing rests partly on a dated (2014) securities-lawyer blog; the
  enduring "paid-only, no advertised price" posture is corroborated by current CSA fee
  guides, but the exact current cost/licensor is not pinned down.
- Avantis is the strongest **coverage** match; its **programmatic** fit (API endpoints,
  auth, rate limits, by-name query) is **unverified**.
- Third-party aggregators surfaced but not individually verified: canadianinsider.com /
  INK Research, ceo.ca (appears to wrap SEDI), TSXInsider, InsiderScreener. Finnhub was
  in scope but produced no surviving verified claim.

## Open questions

1. Actual current price, licensor, format (feed/SFTP/API), and update frequency of the
   CSA/ASC SEDAR+ Data Distribution Service — obtainable only via the CSA Service Desk.
2. Does Avantis AI expose a real programmatic API (endpoints, auth, rate limits,
   by-insider-name query) suitable for a small Python app, and at what price?
   — resolvable via sales@avantisai.com + 2-week trial.
3. Do Canada-native aggregators (INK/canadianinsider.com, ceo.ca, TSXInsider,
   InsiderScreener, **Finnhub**) offer person-name search, verified TSXV/CSE coverage,
   and an affordable documented API? **Finnhub is the top candidate to probe directly.**
4. When will the SEDAR+ phase absorbing SEDI launch, and will it add a free official
   API/bulk export (like SEC EDGAR)?

## Suggested next step (when revisiting)

Directly probe **Finnhub's** `/stock/insider-transactions` API (free tier) with a
TSXV/CSE ticker and a person name to confirm whether it genuinely returns Canadian SEDI
data and supports person-level search. It's the cheapest potentially-viable path and
the only major unknown left. If it fails, the fallback is Avantis AI (sales-gated).

## Primary sources

- SEDI insider search — https://www.sedi.ca/sedi/SVTSelectSediInsider
- CSA: deferral of new SEDAR+ filing system — https://www.securities-administrators.ca/news/canadian-securities-regulators-defer-launch-of-new-sedar-filing-system/
- CSA SEDAR+ search/download help — https://systems.securities-administrators.ca/onlinehelp/general-help/search-sedar/search-and-download-documents/
- OSC SEDAR+ — https://www.osc.ca/en/industry/sedarplus
- Avantis AI data partners — https://www.avantisai.com/data-partners · help — https://help.avantisai.com/en/kb/insider-trades-sedi-and-sec
- Intrinio insider-by-owner endpoint — https://docs.intrinio.com/documentation/web_api/insider_transaction_filings_by_owner_v2
- Nasdaq Data Link docs — https://docs.data.nasdaq.com/ · Nasdaq Basic Canada — https://www.nasdaq.com/solutions/data/equities/nasdaq-basic-canada
- "Canadian Securities Filings Are Behind a Paywall" (A. Cameron-Huff) — https://www.cameronhuff.com/blog/canadian-securities-sedar-sedi-paywall/
