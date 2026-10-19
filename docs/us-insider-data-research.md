# US insider data: what SEDI is to Canada, EDGAR is to the US

Evaluated live on 2026-10-18 against the 14 US-listed companies on the watchlist.
Companion to `canadian-insider-data-research.md`. **Built on 2026-10-19 as
`insight/edgar.py`**; see the EDGAR section of `DEVELOPER_README.md`.

## Bottom line

**SEC EDGAR is the answer, and it is far easier than SEDI.** Every US insider
trade is filed with the SEC as a Form 4, as structured XML, on a free public API
with no account, no key, no CAPTCHA and no bot wall. The only requirement is a
`User-Agent` naming the tool and a contact email, and staying under 10 requests
per second. It is the official source, so it is a superset of what MarketBeat and
OpenInsider show.

| | **SEC EDGAR** | OpenInsider | MarketBeat (today) |
|---|---|---|---|
| Source | Official (the filings themselves) | Republishes EDGAR | Own aggregation |
| Access | JSON + XML API, documented | HTML table scrape | HTML scrape, headless browser |
| Account / key | None (contact email in User-Agent) | None | None |
| Bot wall | None (rate limit only) | None | Occasional |
| Detail | Every transaction code (buy, sale, grant, exercise, tax withholding, gift), role, title, 10% owner flag, holdings after | Buys and sales, title, holdings after | Buy/sell, coarse role |
| Person search | Yes (owner's own filing list) | Yes (insider page) | No |
| Speed | ~4 s for 14 companies' filing lists; ~0.3 s per filing | <0.1 s per company | Slow (browser) |

## What was measured

Two years of filings, `2024-10-18` onward.

**Filing lists** (one request per company, 15 requests, 4.3 s, no errors):

| Ticker | Form 4s in 2 years | Note |
|---|---|---|
| NEM | 134 | |
| SLB | 118 | |
| SCCO | 98 | |
| BKSY | 93 | |
| HYMC | 84 | |
| DVN | 75 | |
| EXE | 53 | |
| GUTS | 41 | |
| VKTX | 37 | |
| ABCL | 34 | |
| CQP | 8 | |
| BHP | 4 | Foreign private issuer: exempt from Form 4 |
| RIO | 0 | Foreign private issuer |
| BHST | 0 | Foreign private issuer; BioHarvest is Canadian, so SEDI is its source |

**Full parse of NEM, HYMC, VKTX** (255 filings, 255 requests, 74 s at 5/s, 0
failures, 355 transactions):

| | EDGAR buys+sales | OpenInsider | MarketBeat |
|---|---|---|---|
| NEM | 53 | 50 (all 50 in EDGAR) | 20 (17 in EDGAR) |
| HYMC | 54 | 43 | 36 (34 in EDGAR) |
| VKTX | 39 | 20 | **0** |

Findings:

- **MarketBeat mixes in US Congress members' trades.** NEM's MarketBeat-only rows
  were Val Hoyle and Ro Khanna, who are not company insiders.
- **OpenInsider's default screen hides 10% owners' buys.** EDGAR shows 31
  open-market buys by Eric Sprott in HYCROFT (`isTenPercentOwner`) that
  OpenInsider did not list.
- The smaller OpenInsider-in-EDGAR overlaps for HYMC and VKTX look like
  OpenInsider merging same-day transactions into one row, not missing data.
- EDGAR carries everything SEDI does: grants (`A`), tax withholding (`F`),
  option exercises (`M`), gifts (`G`), alongside open-market buys (`P`) and
  sales (`S`). The existing alarm rule (skip grants and exercises) maps directly.

**Person-first search works** (the SEDI "Find companies" equivalent): a
reporting owner has their own CIK, and their filing list names each issuer. Eric
Sprott: CIK 1491714, 22 Forms 3/4/5 from 2022 to 2026, all for HYCROFT (his other
holdings are Canadian, i.e. SEDI). 23 requests.

## How it works (the requests)

All with `User-Agent: InSight personal research <contact email>`.

1. Ticker to CIK: `https://www.sec.gov/files/company_tickers.json` (one file,
   ~10,400 tickers; cache it).
2. A company's filings: `https://data.sec.gov/submissions/CIK##########.json`.
   `filings.recent` holds the latest ~1,000 (enough for two years even for NEM);
   older pages are listed in `filings.files`.
3. One Form 4: `https://www.sec.gov/Archives/edgar/data/<CIK>/<accession without
   dashes>/<primaryDocument without the xslF345X0n/ prefix>`. The prefix is the
   rendered HTML; without it is the raw XML.
4. Parse `reportingOwner` (name, CIK, isDirector / isOfficer /
   isTenPercentOwner, officerTitle) and each
   `nonDerivativeTable/nonDerivativeTransaction` (transactionDate,
   transactionCode, transactionShares, transactionPricePerShare,
   transactionAcquiredDisposedCode).
5. Person search: `submissions/CIK<owner>.json`, then for each accession
   `<accession>-index-headers.html`, whose `ISSUER:` section names the company.

Incremental: remember the accession numbers already parsed; a later run fetches
only the filing list plus new filings, typically a few dozen requests.

## Rules to respect

- Declare the User-Agent with a real contact email (requests without one get a
  403 "Request Rate Threshold Exceeded" page; verified).
- Stay under 10 requests/second; 5/s is a comfortable margin.
- The contact email belongs in `.env` (`INSIGHT_SEC_CONTACT`), never in the repo.

## Other options considered

- **OpenInsider**: works instantly, no rules beyond politeness, but it is an
  unofficial republisher of EDGAR, misses some rows by default, and is HTML to
  scrape. A fallback at most.
- **SEC Insider Transactions Data Sets**: the same data as quarterly bulk TSV
  files. Good for a one-off backfill of many companies, too stale (quarterly)
  for alerts.
- **Finnhub and similar APIs**: need an account and key, and are secondary
  sources. Not needed while EDGAR is free and primary.
