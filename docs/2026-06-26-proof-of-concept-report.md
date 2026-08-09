# InSight — Proof of Concept Report (historical)

> **Historical record — 2026-06-26. Do not follow as instructions.**
>
> This is the original feasibility report, kept for the source investigation in
> §2 that still explains why the project is built the way it is. Everything about
> *running* it is obsolete:
>
> - `scrape_insider.py`, `requirements.txt` and the venv workflow are gone —
>   install with `uv`, run `insight` / `insight-scrape`.
> - CSV and `by_ticker/` outputs are no longer written. Only dated JSON, folded
>   into `data/store.json`.
> - **SEDI now works.** §2 concluded it was unusable; it is since implemented in
>   `insight/sedi.py`, running headful against a persistent profile so a CAPTCHA is
>   solved once by hand. The block described below was real — the fix was to stop
>   trying to automate past it.
>
> Current docs: **[README.md](../README.md)** ·
> **[DEVELOPER_README.md](../DEVELOPER_README.md)**

**Date:** 2026-06-26
**Objective:** Determine whether daily insider-transaction data for Canadian
stocks can be collected programmatically, and deliver a working proof of concept.

---

## 1. Summary

A runnable collector was built and verified live, pulling **71 real insider
transactions** across all 6 requested companies — individuals, officers/directors,
institutions, and the issuer itself (buybacks). Normalized to one source-agnostic
schema and written to dated files suitable for a daily pipeline.

The authoritative source — **SEDI (sedi.ca)** — could not be scraped from this
machine, so the PoC ran against **MarketBeat**.

---

## 2. Source investigation

| Source | Reachable from this host? | Notes |
|---|---|---|
| **SEDI** (`sedi.ca`) | ❌ Blocked | ShieldSquare/PerfDrive → **hCaptcha**. After a couple of requests the IP was flagged and *every* page returned the captcha. |
| **canadianinsider.com** | ❌ Blocked | Cloudflare bot wall (403 "Just a moment…"). |
| **insidertracking.com** | ❌ Blocked | Cloudflare bot wall. |
| **MarketBeat** (per-stock pages) | ✅ Reachable | HTTP 200, real data. **Used for the PoC.** |

### Root cause for the SEDI block

The egress IP was a **hosting/VPS IP** (Frantech/BuyVM, `198.98.121.x`), which
anti-bot systems score as high-risk. Both headless *and* headful automation were
challenged regardless of stealth (verified: `navigator.webdriver=false` +
`playwright-stealth` still got captcha'd).

SEDI scraping *does* work from a normal browser on a residential connection —
that is how the community
[SEDI bookmarklet](https://tomcardoso.github.io/sedi-bookmarklet/) operates. The
blocker was the IP/environment, not the approach.

*(This is what the shipped `sedi.py` acts on: run headful, let a human solve the
challenge once, and persist the profile.)*

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
  captured with name + role.

---

## 4. Limitations & risks identified

- **Coverage gap:** small **TSX-Venture** issuers are not on MarketBeat (e.g.
  *American Eagle Gold Corp*, TSXV:AE returned nothing — only the US apparel
  retailer *American Eagle Outfitters*, NYSE:AEO). Micro/small-caps require SEDI.
- **Ambiguous ticker:** "American Eagle" was read as NYSE:AEO as a placeholder.
- **Freshness/detail:** MarketBeat aggregates, may lag SEDI by days, and drops
  SEDI's richer fields (ownership type, nature-of-transaction codes,
  post-transaction balance).
- **Terms of use:** scraping MarketBeat may conflict with its ToS.

---

## 5. Recommended next steps *(as written then)*

1. **Authoritative SEDI source** — a second scraper module on the same schema,
   routed through a residential proxy or run headful with a persisted profile
   (one-time captcha solve). — **Done**, see `insight/sedi.py`.
2. **Persistence + change detection** — load dated outputs into a store and diff
   day-over-day to alert on *new* filings. — **Done**, see `store.py` and
   `notify.py`.
3. **Resolve the American Eagle ticker** with the requester.
4. **Optional managed fallback** — evaluate a paid API for Canadian coverage.
   — Researched in
   [`canadian-insider-data-research.md`](canadian-insider-data-research.md);
   conclusion was that every viable route is paid.
