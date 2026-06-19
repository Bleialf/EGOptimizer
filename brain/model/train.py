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


def train_model(
    db: Path | str, out: Path | str, aggressiveness: float, mode: str = "explore"
) -> dict:
    """Fit and save the model; return a summary (reused by the /train endpoint)."""
    records = Store(db).fetch_all()
    model = CapacityModel(aggressiveness=aggressiveness, mode=mode).fit(records)
    model.save(out)
    n_explore = sum(1 for b in model.buckets.values() if b.max_was_censored or b.n < 5)
    return {
        "records": len(records),
        "buckets": len(model.buckets),
        "uncertain_buckets": n_explore,
        "saved": str(out),
    }


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Fit the EG absorption model.")
    ap.add_argument("--db", type=Path, default=Path(cfg["storage"]["db_path"]))
    ap.add_argument("--out", type=Path, default=Path(cfg["model"]["path"]))
    ap.add_argument("--aggressiveness", type=float,
                    default=cfg["model"]["exploration_aggressiveness"])
    ap.add_argument("--mode", default=cfg["model"]["mode"])
    args = ap.parse_args(argv)

    r = train_model(args.db, args.out, args.aggressiveness, args.mode)
    print(f"Trained on {r['records']} records -> {r['buckets']} context buckets.")
    print(f"  {r['uncertain_buckets']} buckets still uncertain (will probe higher).")
    print(f"  saved: {r['saved']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
