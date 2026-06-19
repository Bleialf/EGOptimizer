"""Import a provider export into the local store.

Usage:
    python -m brain.ingest.run_import --provider netznoe --file data/<export>.csv
    python -m brain.ingest.run_import --file data/<export>.csv          # default provider
    python -m brain.ingest.run_import --provider netznoe --all-in data/  # every *.csv in a dir

Idempotent: re-running upgrades previously-unsettled EG values in place.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from brain.config import load_config
from brain.ingest.clean import filter_outliers
from brain.providers import available, get_provider
from brain.storage import Store

DEFAULT_DB = Path("data/egoptimizer.sqlite")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import grid-operator exports into the store.")
    ap.add_argument("--provider", default="netznoe", help=f"one of: {', '.join(available())}")
    ap.add_argument("--file", type=Path, help="single export file to import")
    ap.add_argument("--all-in", type=Path, help="import every *.csv in this directory")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"sqlite path (default {DEFAULT_DB})")
    args = ap.parse_args(argv)

    if not args.file and not args.all_in:
        ap.error("provide --file or --all-in")

    files: list[Path] = []
    if args.file:
        files.append(args.file)
    if args.all_in:
        files.extend(sorted(args.all_in.glob("*.csv")))
    missing = [f for f in files if not f.exists()]
    if missing:
        ap.error(f"file(s) not found: {', '.join(map(str, missing))}")

    provider = get_provider(args.provider)
    max_interval = load_config()["ingest"]["max_interval_kwh"]
    total, total_dropped = 0, 0
    with Store(args.db) as store:
        for f in files:
            kept, dropped = filter_outliers(provider.parse(f), max_interval)
            written = store.upsert_many(kept)
            total += written
            total_dropped += len(dropped)
            extra = f" ({len(dropped)} outlier(s) dropped)" if dropped else ""
            print(f"  {f.name}: {written} intervals{extra}")
        s = store.summary()
    if total_dropped:
        print(f"Dropped {total_dropped} implausible interval(s) > {max_interval} kWh/15min.")

    print(f"\nImported {total} intervals via '{args.provider}' into {args.db}")
    if s.get("n"):
        print(
            f"Store now holds {s['n']} intervals "
            f"({s['first_ts']} -> {s['last_ts']}); "
            f"{s['settled']} EG-settled (last settled: {s['last_settled_ts']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
