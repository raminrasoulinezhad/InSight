#!/usr/bin/env python3
# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

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

Run it daily (cron / Task Scheduler) to build a history.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from . import paths
from .marketbeat import discover_tickers, scrape_many
from .models import InsiderTransaction
from .sedi import SediScraper


def load_targets(path: Path, cli_tickers: list[str]) -> list[dict[str, str]]:
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
    results: dict[str, list[InsiderTransaction]],
    outdir: Path,
    run_date: str,
    source: str = "marketbeat",
) -> None:
    """Write the dated snapshot. Non-default sources get a filename tag
    (insider_sedi_YYYY-MM-DD.json) so they merge into the app's deduped view via
    the insider_*.json glob without clobbering another source's snapshot.

    Only JSON is written. Earlier versions also emitted a flat `.csv` and a
    `by_ticker/` directory of per-company CSVs, but nothing ever read them back:
    the app loads JSON, and each run restated the same rows, so the exports grew
    by ~5 MB and ~130 files per scrape (a real folder reached 305 MB across
    7,000+ files) purely as dead weight. Existing CSVs are left alone — delete
    them by hand when convenient.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    tag = "" if source == "marketbeat" else f"{source}_"

    all_rows: list[dict[str, Any]] = [r.to_dict() for recs in results.values() for r in recs]
    (outdir / f"insider_{tag}{run_date}.json").write_text(json.dumps(all_rows, indent=2))


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


def _prune(outdir: Path, keep: int) -> int:
    """Drop dated snapshots already folded into the consolidated store.

    Snapshots overwhelmingly restate each other, so once they are folded the
    originals are redundant bulk. Pruning only ever removes files the store has
    provably absorbed (see store.prune_folded); the newest `keep` are left as a
    hand-inspectable tail.
    """
    from .aggregate import _txn_key
    from .store import prune_folded

    removed, freed = prune_folded(outdir, _txn_key, keep=keep)
    if not removed:
        print(f"Nothing to prune in {outdir} (no folded snapshots beyond the newest {keep}).")
        return 0
    print(f"Pruned {len(removed)} folded snapshot(s) from {outdir}, freeing {freed / 1e6:.1f} MB.")
    print(f"Kept the newest {keep}. All records remain in {outdir}/store.json.")
    return 0


def _autoprune_snapshots(outdir: Path, keep: int) -> None:
    """Fold the new snapshot in and drop the ones it made redundant.

    Best-effort: a scrape that collected data successfully must not be reported
    as a failure because housekeeping tripped.
    """
    try:
        from .aggregate import _txn_key
        from .store import prune_folded

        removed, freed = prune_folded(outdir, _txn_key, keep=keep)
        if removed:
            print(
                f"Folded into store.json; removed {len(removed)} redundant snapshot(s), "
                f"freeing {freed / 1e6:.1f} MB (kept the newest {keep})."
            )
    except Exception as e:
        print(f"Snapshot cleanup skipped: {type(e).__name__}: {e}", file=sys.stderr)


def _prune_browser_caches(*dirs: Path, quiet: bool = False) -> int:
    """Reclaim Chromium cache from the given profiles. Best-effort.

    Only regenerable caches are removed (see profiles.DISPOSABLE); cookies and
    local storage — which for the SEDI profile hold the solved bot-wall
    challenge — are left alone.
    """
    from .profiles import IN_USE, prune_profile

    total = 0
    busy: list[str] = []
    for profile_dir in dirs:
        try:
            removed, freed = prune_profile(profile_dir)
        except Exception as e:  # housekeeping must never break a scrape
            print(f"Cache cleanup skipped for {profile_dir.name}: {e}", file=sys.stderr)
            continue
        if removed == [IN_USE]:
            busy.append(profile_dir.name)
            continue
        total += freed
        if removed and not quiet:
            print(f"{profile_dir.name}: freed {freed / 1e6:.1f} MB ({len(removed)} entries)")
    if not quiet:
        print(f"Reclaimed {total / 1e6:.1f} MB of browser cache." if total else "Nothing to clean.")
        for name in busy:
            print(f"Skipped {name}: a browser has it open. Close InSight and re-run.")
    elif total > 1_000_000:
        print(f"Reclaimed {total / 1e6:.0f} MB of browser cache.")
    return 0


def main(argv: list[str] | None = None) -> int:
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
        "--source",
        choices=["marketbeat", "sedi"],
        default="marketbeat",
        help="data source (default: marketbeat). 'sedi' = Canada's official SEDI, "
        "covers small TSX-V names but runs a visible browser and may need you to "
        "solve a one-time CAPTCHA",
    )
    ap.add_argument(
        "--months",
        type=int,
        default=24,
        help="SEDI date range to request, in months back from today (default: 24)",
    )
    ap.add_argument(
        "--capture",
        default=None,
        metavar="DIR",
        help="(sedi) dump each fetched page's HTML/screenshot to DIR for debugging selectors",
    )
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
    ap.add_argument(
        "--prune-snapshots",
        nargs="?",
        type=int,
        const=2,
        default=None,
        metavar="KEEP",
        help="reclaim disk: delete dated snapshots already folded into store.json, "
        "keeping the newest KEEP (default 2), then exit without scraping. No records "
        "are lost — the store holds the deduplicated union of everything ever scraped",
    )
    ap.add_argument(
        "--keep-snapshots",
        type=int,
        default=2,
        metavar="N",
        help="after scraping, delete dated snapshots already folded into store.json, "
        "keeping the newest N (default 2, matching --prune-snapshots). The store holds "
        "every record, so this only removes redundant copies. Pass a large number to keep all",
    )
    ap.add_argument(
        "--prune-browser-cache",
        action="store_true",
        help="reclaim disk: clear the Chromium caches in InSight's two browser profiles, "
        "then exit without scraping. Cookies and local storage are kept, so the solved "
        "SEDI CAPTCHA survives. Run this with the app and scraper closed",
    )
    args = ap.parse_args(argv)

    config = Path(args.config) if args.config else paths.config_file()
    outdir = Path(args.outdir) if args.outdir else paths.data_dir()
    cache_dir = None if args.no_cache else paths.cache_dir()

    if args.prune_snapshots is not None:
        return _prune(outdir, args.prune_snapshots)

    if args.prune_browser_cache:
        return _prune_browser_caches(paths.sedi_profile_dir(), paths.chrome_profile_dir())

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

    if args.source == "sedi":
        # SEDI is bot-walled: force a visible browser + persistent profile so a
        # one-time CAPTCHA solve sticks. Its delisted-detection doesn't apply
        # (no per-ticker insider URL to redirect), so no delisted file is passed.
        from .sedi import is_canadian

        capture = Path(args.capture) if args.capture else None
        targets = [t for t in targets if is_canadian(t)]  # SEDI is Canada-only
        print("Source: SEDI (a browser window will open; solve any CAPTCHA once).\n")
        results = scrape_many(
            targets,
            cache_dir=cache_dir,
            max_age_hours=args.max_age,
            force=args.force,
            scraper_factory=lambda: SediScraper(
                headless=False,
                profile_dir=paths.sedi_profile_dir(),
                months=args.months,
                capture_dir=capture,
                pages_dir=paths.sedi_pages_dir(),
            ),
            source="sedi",
        )
        # The browser has closed, so its caches are safe to drop. The session
        # cookie that carries the solved CAPTCHA is never touched.
        _prune_browser_caches(paths.sedi_profile_dir(), quiet=True)
    else:
        results = scrape_many(
            targets,
            headless=not args.headful,
            cache_dir=cache_dir,
            max_age_hours=args.max_age,
            force=args.force,
            delisted_path=paths.delisted_file(),
        )
    write_outputs(results, outdir, run_date, source=args.source)
    summarize(results)
    print(f"\nWrote: {outdir}/insider_{run_date}.json")

    # Fold the new snapshot into the store and drop the copies it made
    # redundant. Doing it here rather than behind a flag is the point: snapshots
    # restate each other, so left alone they grow without anyone noticing, and a
    # cleanup command nobody discovers is the same as no cleanup. It also leaves
    # the store warm, so the app's next cold start is already fast.
    _autoprune_snapshots(outdir, args.keep_snapshots)

    # Fire any alarms whose watched company/person has new transactions.
    try:
        from .notify import evaluate_and_notify

        res = evaluate_and_notify(paths.notify_file(), outdir)
        if res.get("sent"):
            print(f"Notifications: sent {res['sent']} alarm(s).")
        for err in res.get("errors", []):
            print(f"Notification error: {err}")
    except Exception as e:  # notifications must never break a scrape
        print(f"Notification step failed: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
