#!/usr/bin/env python3
# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""InSight — collect insider transactions for a watchlist of stocks.

Usage (installed via `uv tool install`, or `uv run insight-scrape` from a
checkout):
    insight-scrape                      # use the watchlist (companies.json)
    insight-scrape --tickers TSE:FNV TSE:CNQ
    insight-scrape --headful            # show the browser
    insight-scrape --outdir ./data      # override where output is written

By default the watchlist and output live in the per-user app folder (see
insight.paths). Outputs, per run (stamped with today's date):
    data/insider_YYYY-MM-DD.json   all records, one source-agnostic schema
    data/insider_YYYY-MM-DD.csv    same, flat CSV for spreadsheets/DBs
    data/by_ticker/<EXCH>_<TKR>_YYYY-MM-DD.csv   one file per company

Run it daily (cron / Task Scheduler) to build a history.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

from . import paths
from .marketbeat import discover_tickers, scrape_many
from .models import InsiderTransaction

CSV_FIELDS = [
    "issuer_name",
    "exchange",
    "ticker",
    "insider_name",
    "insider_role",
    "entity_type",
    "transaction_date",
    "transaction_type",
    "shares",
    "avg_price",
    "total_value",
    "currency",
    "is_issuer_buyback",
    "source",
    "source_url",
    "scraped_at",
]


def load_targets(path: Path, cli_tickers: list[str]) -> list[dict]:
    if cli_tickers:
        targets = []
        for spec in cli_tickers:
            exch, _, tk = spec.partition(":")
            if not tk:
                exch, tk = "TSE", exch  # default exchange
            targets.append({"name": tk, "exchange": exch, "ticker": tk})
        return targets
    data = json.loads(path.read_text())
    return [c for c in data["companies"] if not c.get("name", "").startswith("_")]


def write_outputs(
    results: dict[str, list[InsiderTransaction]], outdir: Path, run_date: str
) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    by_ticker_dir = outdir / "by_ticker"
    by_ticker_dir.mkdir(exist_ok=True)

    all_rows: list[dict] = []
    for key, recs in results.items():
        rows = [r.to_dict() for r in recs]
        all_rows.extend(rows)
        # per-company CSV (only when there's data)
        if rows:
            safe = key.replace(":", "_")
            with (by_ticker_dir / f"{safe}_{run_date}.csv").open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
                w.writeheader()
                w.writerows(rows)

    # combined JSON
    (outdir / f"insider_{run_date}.json").write_text(json.dumps(all_rows, indent=2))
    # combined CSV
    with (outdir / f"insider_{run_date}.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(all_rows)


def summarize(results: dict[str, list[InsiderTransaction]]) -> None:
    total = sum(len(v) for v in results.values())
    covered = sum(1 for v in results.values() if v)
    print("\n=== Summary ===")
    print(f"Companies covered : {covered}/{len(results)}")
    print(f"Total transactions: {total}")
    for key, recs in results.items():
        if not recs:
            continue
        buys = sum(1 for r in recs if r.transaction_type.lower() == "buy")
        sells = sum(1 for r in recs if r.transaction_type.lower() == "sell")
        inst = sum(1 for r in recs if r.entity_type == "institution")
        latest = max((r.transaction_date for r in recs if r.transaction_date), default="?")
        print(
            f"  {key:12s} {len(recs):3d} txns  "
            f"(buys={buys} sells={sells} institutional={inst})  "
            f"latest={latest}"
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Collect insider transactions.")
    ap.add_argument("--config", default=None, help="watchlist JSON (default: per-user app folder)")
    ap.add_argument("--tickers", nargs="*", default=[], help="EXCH:TICKER specs, overrides config")
    ap.add_argument(
        "--discover",
        nargs="*",
        default=None,
        metavar="EXCH",
        help="auto-discover MarketBeat's ticker universe for these exchanges "
        "(default TSE if none given) and scrape them in addition to the watchlist; "
        "e.g. --discover TSE",
    )
    ap.add_argument("--outdir", default=None, help="output dir (default: per-user app folder)")
    ap.add_argument(
        "--headful", action="store_true", help="run a visible browser (helps on flagged IPs)"
    )
    ap.add_argument(
        "--max-age",
        type=float,
        default=12.0,
        help="reuse cached company data younger than this many hours (default: 12)",
    )
    ap.add_argument("--force", action="store_true", help="ignore the cache and re-fetch everything")
    ap.add_argument("--no-cache", action="store_true", help="do not use the company cache")
    args = ap.parse_args(argv)

    config = Path(args.config) if args.config else paths.config_file()
    outdir = Path(args.outdir) if args.outdir else paths.data_dir()
    cache_dir = None if args.no_cache else paths.cache_dir()

    targets = load_targets(config, args.tickers)
    run_date = date.today().isoformat()
    print(f"InSight insider scrape — {run_date}")

    # Optionally widen the universe with MarketBeat's per-exchange ticker lists,
    # merged with (and de-duplicated against) the watchlist/CLI targets.
    if args.discover is not None:
        print("Discovering ticker universe from MarketBeat…")
        seen = {f"{t['exchange'].upper()}:{t['ticker'].upper()}" for t in targets}
        for d in discover_tickers(args.discover or ["TSE"]):
            key = f"{d['exchange']}:{d['ticker']}"
            if key not in seen:
                targets.append(d)
                seen.add(key)

    if len(targets) <= 25:
        print(f"Targets: {', '.join(t['exchange'] + ':' + t['ticker'] for t in targets)}\n")
    else:
        print(f"Targets: {len(targets)} companies\n")

    results = scrape_many(
        targets,
        headless=not args.headful,
        cache_dir=cache_dir,
        max_age_hours=args.max_age,
        force=args.force,
        delisted_path=paths.delisted_file(),
    )
    write_outputs(results, outdir, run_date)
    summarize(results)
    print(f"\nWrote: {outdir}/insider_{run_date}.json (+ .csv, by_ticker/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
