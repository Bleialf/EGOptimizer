"""Community-absorption analysis over the stored history.

Answers the questions that shape the whole project:
  * When (hour / weekday) does the EG actually absorb energy vs spill it?
  * How often is uptake *censored* -- i.e. we fed in and the EG took ALL of it,
    so we never learned the true ceiling? Censored intervals are where the
    model has exploration headroom ("would they have taken 7?").

Pure stdlib so it runs without installing anything.

Usage:
    python -m brain.analysis.absorption --db data/egoptimizer.sqlite
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from brain.storage import Store

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _bar(frac: float, width: int = 24) -> str:
    n = int(round(max(0.0, min(1.0, frac)) * width))
    return "#" * n + "." * (width - n)


def _rate(absorbed: float, feed: float) -> float:
    return absorbed / feed if feed > 1e-9 else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analyze EG absorption patterns.")
    ap.add_argument("--db", type=Path, default=Path("data/egoptimizer.sqlite"))
    args = ap.parse_args(argv)

    records = [r for r in Store(args.db).fetch_all() if r.eg_settled]
    fed = [r for r in records if r.feed_in_kwh > 1e-9]
    if not fed:
        print("No settled intervals with feed-in found. Import data first.")
        return 1

    tot_feed = sum(r.feed_in_kwh for r in fed)
    tot_abs = sum(r.eg_absorbed_kwh for r in fed)
    tot_spill = sum(r.eg_surplus_kwh for r in fed)
    censored = [r for r in fed if r.fully_absorbed]

    print("=" * 64)
    print("EG ABSORPTION ANALYSIS")
    print("=" * 64)
    print(f"Settled intervals with feed-in : {len(fed):>8}")
    print(f"Total fed in                   : {tot_feed:>8.1f} kWh")
    print(f"  -> absorbed by EG members    : {tot_abs:>8.1f} kWh  ({_rate(tot_abs, tot_feed):.0%})")
    print(f"  -> spilled to grid (surplus) : {tot_spill:>8.1f} kWh  ({_rate(tot_spill, tot_feed):.0%})")
    print(
        f"\nCensored intervals (EG took 100%): {len(censored):>8} "
        f"({len(censored) / len(fed):.0%} of fed intervals)"
    )
    print("  ^ these are exploration targets: true capacity was >= what we fed.")

    # ---- by hour of day -------------------------------------------------
    by_hour_feed: dict[int, float] = defaultdict(float)
    by_hour_abs: dict[int, float] = defaultdict(float)
    by_hour_cens: dict[int, int] = defaultdict(int)
    by_hour_n: dict[int, int] = defaultdict(int)
    for r in fed:
        h = r.timestamp.hour
        by_hour_feed[h] += r.feed_in_kwh
        by_hour_abs[h] += r.eg_absorbed_kwh
        by_hour_n[h] += 1
        if r.fully_absorbed:
            by_hour_cens[h] += 1

    print("\n" + "-" * 64)
    print("ABSORPTION RATE BY HOUR OF DAY  (absorbed / fed)")
    print("hour  rate  " + " " * 20 + "  fed kWh  censored%")
    print("-" * 64)
    for h in range(24):
        if by_hour_n[h] == 0:
            continue
        rate = _rate(by_hour_abs[h], by_hour_feed[h])
        cens = by_hour_cens[h] / by_hour_n[h]
        print(f"{h:>2}h  {rate:>4.0%}  {_bar(rate)}  {by_hour_feed[h]:>7.1f}  {cens:>6.0%}")

    # ---- by month (seasonality) ----------------------------------------
    by_mon_feed: dict[str, float] = defaultdict(float)
    by_mon_abs: dict[str, float] = defaultdict(float)
    by_mon_spill: dict[str, float] = defaultdict(float)
    by_mon_cens: dict[str, int] = defaultdict(int)
    by_mon_n: dict[str, int] = defaultdict(int)
    for r in fed:
        key = f"{r.timestamp.year:04d}-{r.timestamp.month:02d}"
        by_mon_feed[key] += r.feed_in_kwh
        by_mon_abs[key] += r.eg_absorbed_kwh
        by_mon_spill[key] += r.eg_surplus_kwh
        by_mon_n[key] += 1
        if r.fully_absorbed:
            by_mon_cens[key] += 1

    print("\n" + "-" * 64)
    print("ABSORPTION RATE BY MONTH  (seasonality)")
    print("month    rate" + " " * 22 + "fed kWh  absorbed  censored%")
    print("-" * 64)
    for key in sorted(by_mon_feed):
        rate = _rate(by_mon_abs[key], by_mon_feed[key])
        cens = by_mon_cens[key] / by_mon_n[key] if by_mon_n[key] else 0.0
        print(
            f"{key}  {rate:>4.0%}  {_bar(rate)}  {by_mon_feed[key]:>7.1f}  "
            f"{by_mon_abs[key]:>7.1f}  {cens:>6.0%}"
        )

    # ---- by weekday -----------------------------------------------------
    by_wd_feed: dict[int, float] = defaultdict(float)
    by_wd_abs: dict[int, float] = defaultdict(float)
    for r in fed:
        wd = r.timestamp.weekday()
        by_wd_feed[wd] += r.feed_in_kwh
        by_wd_abs[wd] += r.eg_absorbed_kwh

    print("\n" + "-" * 64)
    print("ABSORPTION RATE BY WEEKDAY")
    print("-" * 64)
    for wd in range(7):
        if by_wd_feed[wd] <= 1e-9:
            continue
        rate = _rate(by_wd_abs[wd], by_wd_feed[wd])
        print(f"{_WEEKDAYS[wd]}  {rate:>4.0%}  {_bar(rate)}  {by_wd_feed[wd]:>7.1f} kWh fed")

    print("\n" + "=" * 64)
    print("READING THIS: high censored% at an hour = the EG kept taking all we")
    print("gave -> headroom to probe higher. Low absorption% = community is")
    print("saturated there -> hold energy back for your own autarky instead.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
