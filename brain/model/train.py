"""Train (refit) the absorption model from the stored history.

Run nightly (or after each import). Reads all settled records, fits the
per-bucket capacity model, and saves it to data/model.json for the API to load.

    python -m brain.model.train
    python -m brain.model.train --db data/egoptimizer.sqlite --out data/model.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from brain.config import load_config
from brain.model.capacity import CapacityModel
from brain.storage import Store


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Fit the EG absorption model.")
    ap.add_argument("--db", type=Path, default=Path(cfg["storage"]["db_path"]))
    ap.add_argument("--out", type=Path, default=Path("data/model.json"))
    ap.add_argument("--aggressiveness", type=float,
                    default=cfg["model"]["exploration_aggressiveness"])
    args = ap.parse_args(argv)

    records = Store(args.db).fetch_all()
    model = CapacityModel(aggressiveness=args.aggressiveness).fit(records)
    model.save(args.out)

    n_buckets = len(model.buckets)
    n_explore = sum(
        1 for b in model.buckets.values() if b.max_was_censored or b.n < 5
    )
    print(f"Trained on {len(records)} records -> {n_buckets} context buckets.")
    print(f"  {n_explore} buckets still uncertain (will probe higher).")
    print(f"  saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
