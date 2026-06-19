"""Evaluate the learning loop.

Two views:

1. BACKTEST -- split the history in time, fit the model on the early part, and
   measure how well it predicts community capacity on the later part vs a naive
   "repeat the average" baseline. Because uptake is censored, we score only on
   *uncensored* test hours (where the true ceiling was actually observed).

2. REWARD-JOIN -- join each logged recommendation to the NetzNOE uptake that
   later settled for that hour, so you can see whether probes paid off (the EG
   absorbed the extra) or found the ceiling (surplus appeared).

    python -m brain.analysis.evaluate              # backtest
    python -m brain.analysis.evaluate --join       # decisions vs outcomes
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from brain.config import load_config
from brain.model.capacity import CapacityModel, aggregate_hourly
from brain.model.context import bucket_key
from brain.storage import Store


def backtest(db: Path, train_frac: float = 0.7) -> dict:
    obs = [o for o in aggregate_hourly(Store(db).fetch_all()) if o.feed_kwh > 1e-6]
    obs.sort(key=lambda o: o.ts)
    if len(obs) < 50:
        print("Not enough data to backtest.")
        return {}
    cut = int(len(obs) * train_frac)
    train, test = obs[:cut], obs[cut:]

    model = CapacityModel(aggressiveness=0.15).fit_from_obs(train)
    # naive baseline: per-bucket mean absorbed from train
    base_sum, base_n = {}, {}
    for o in train:
        k = bucket_key(o.ts)
        base_sum[k] = base_sum.get(k, 0.0) + o.absorbed_kwh
        base_n[k] = base_n.get(k, 0) + 1

    # Score CAPTURE vs SPILL on uncensored test hours, where the true ceiling
    # WAS observed (== absorbed). Capture = min(feed, ceiling); spill = excess.
    m_cap = m_spill = b_cap = b_spill = 0.0
    scored = probes = censored = 0
    for o in test:
        rec, explore, _ = model.recommend_capacity(o.ts)
        if explore:
            probes += 1
        if o.censored:
            censored += 1
            continue
        ceiling = o.absorbed_kwh
        base = base_sum.get(bucket_key(o.ts), 0.0) / base_n.get(bucket_key(o.ts), 1)
        m_cap += min(rec, ceiling); m_spill += max(0.0, rec - ceiling)
        b_cap += min(base, ceiling); b_spill += max(0.0, base - ceiling)
        scored += 1

    res = {
        "train": len(train), "test": len(test), "scored_uncensored": scored,
        "model_capture": round(m_cap, 1), "baseline_capture": round(b_cap, 1),
        "model_spill": round(m_spill, 1), "baseline_spill": round(b_spill, 1),
        "probe_rate": round(probes / len(test), 3),
        "test_censored": censored,
    }
    print("=" * 60)
    print("BACKTEST  (fit early -> predict late; score capture vs spill)")
    print("=" * 60)
    print(f"train hours: {res['train']}   test hours: {res['test']}")
    print(f"scored on {scored} uncensored test hours "
          f"({censored} censored test hours can't be scored offline)")
    print(f"  captured uptake : model {res['model_capture']} vs baseline "
          f"{res['baseline_capture']} kWh")
    print(f"  spill (overshoot): model {res['model_spill']} vs baseline "
          f"{res['baseline_spill']} kWh")
    print(f"  exploration rate: {res['probe_rate']:.0%} of test contexts")
    print("\nNOTE: exploration's real payoff -- discovering capacity on the")
    print(f"{censored} CENSORED hours where we historically fed too little to see")
    print("the ceiling -- cannot be measured from past data. It only shows up")
    print("live, as probes reveal the EG could take more. Offline we can only")
    print("confirm the model captures >= the naive baseline at a modest spill cost.")
    return res


def join_decisions(db: Path) -> None:
    store = Store(db)
    decisions = store.conn.execute(
        "SELECT decided_at, feed_kw, explore FROM decisions ORDER BY decided_at"
    ).fetchall()
    if not decisions:
        print("No decisions logged yet.")
        return
    recs = {(r.timestamp.date(), r.timestamp.hour): r for r in store.fetch_all() if r.eg_settled}
    print("decided_at         feed_kw  probe  actual_absorbed  surplus  outcome")
    print("-" * 78)
    for d in decisions:
        t = datetime.fromisoformat(d["decided_at"])
        hour = [r for (dt, h), r in recs.items() if dt == t.date() and h == t.hour]
        if not hour:
            outcome = "not settled yet"
            print(f"{d['decided_at']:18} {d['feed_kw']:>6}   {bool(d['explore'])!s:>5}  {'--':>15}  {'--':>7}  {outcome}")
            continue
        r = hour[0]
        absorbed = r.eg_absorbed_kwh or 0.0
        surplus = r.eg_surplus_kwh or 0.0
        if d["explore"]:
            outcome = "probe ABSORBED (ceiling higher!)" if surplus < 1e-6 else "probe found ceiling"
        else:
            outcome = "exploit ok" if surplus < 1e-6 else "exploit spilled"
        print(f"{d['decided_at']:18} {d['feed_kw']:>6}   {bool(d['explore'])!s:>5}  {absorbed:>15.3f}  {surplus:>7.3f}  {outcome}")


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Evaluate the learning loop.")
    ap.add_argument("--db", type=Path, default=Path(cfg["storage"]["db_path"]))
    ap.add_argument("--join", action="store_true", help="join logged decisions to outcomes")
    ap.add_argument("--train-frac", type=float, default=0.7)
    args = ap.parse_args(argv)
    if args.join:
        join_decisions(args.db)
    else:
        backtest(args.db, args.train_frac)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
