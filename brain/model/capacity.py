"""Censored-aware community-absorption model with UCB exploration.

The learning signal is CENSORED: we only ever observe how much the EG absorbed
*up to what we fed in*. When an interval was fully absorbed (surplus ~ 0), the
community's true capacity is only known to be >= what we fed -- never the
ceiling. When there was surplus, we DID see the ceiling (they took what they
could and rejected the rest), so that observation is uncensored.

Per context bucket we therefore track the best uptake we've seen and whether
that best was censored, and produce a recommended capacity:

    if best uptake was censored (or too few obs):  probe ABOVE it (explore)
    else (we've seen the ceiling):                 aim at it (exploit)

This is a contextual bandit with an Upper-Confidence-Bound flavour -- the
right-sized reinforcement learning for ~1 decision/night. No heavy deps.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from brain.model.context import bucket_key
from brain.records import EnergyRecord

# A bucket needs at least this many hourly observations before we trust its
# uncensored ceiling enough to stop probing.
MIN_CONFIDENT_OBS = 5
# Smallest probe (kWh/h) for a context we have no data on yet.
COLD_START_PROBE_KWH = 0.2


@dataclass
class BucketStats:
    n: int = 0                    # raw hourly observations with feed-in (display)
    max_absorbed: float = 0.0     # recency-DECAYED best uptake (kWh in an hour)
    max_was_censored: bool = False  # was that best fully absorbed (ceiling unseen)?
    sum_absorbed: float = 0.0     # recency-WEIGHTED sum of absorbed
    n_censored: int = 0
    weight: float = 0.0           # sum of recency weights (effective recent obs)

    @property
    def mean_absorbed(self) -> float:
        return self.sum_absorbed / self.weight if self.weight else 0.0


@dataclass
class HourObs:
    date: str
    hour: int
    ts: datetime
    feed_kwh: float
    absorbed_kwh: float
    surplus_kwh: float

    @property
    def censored(self) -> bool:
        return self.feed_kwh > 1e-6 and self.surplus_kwh < 1e-6


def aggregate_hourly(records: list[EnergyRecord]) -> list[HourObs]:
    """Sum 15-min settled records into per-(date, hour) observations."""
    acc: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    rep_ts: dict[tuple, datetime] = {}
    for r in records:
        if not r.eg_settled:
            continue
        key = (r.timestamp.date(), r.timestamp.hour)
        acc[key][0] += r.feed_in_kwh
        acc[key][1] += r.eg_absorbed_kwh or 0.0
        acc[key][2] += r.eg_surplus_kwh or 0.0
        rep_ts.setdefault(key, r.timestamp.replace(minute=0))
    out = []
    for (d, h), (feed, absorbed, surplus) in acc.items():
        out.append(HourObs(str(d), h, rep_ts[(d, h)], feed, absorbed, surplus))
    return out


class CapacityModel:
    """Per-bucket absorption capacity with UCB exploration."""

    def __init__(self, aggressiveness: float = 0.15, mode: str = "explore",
                 half_life_days: float = 45.0):
        self.aggressiveness = aggressiveness
        self.mode = mode  # "explore" (probe to learn) | "locked" (feed what's taken)
        # Recency half-life: an observation's weight halves every this-many days,
        # and a bucket's ceiling DECAYS toward recent reality if not reconfirmed.
        # So the model adapts UP and DOWN, weights recent days more, and lets a
        # context that's gone quiet for a while become "unsure" -> re-explored.
        # 0 disables decay (legacy all-time max).
        self.half_life_days = half_life_days
        self.buckets: dict[str, BucketStats] = {}

    # ---- training -------------------------------------------------------
    def fit(self, records: list[EnergyRecord]) -> "CapacityModel":
        return self.fit_from_obs(aggregate_hourly(records))

    def fit_from_obs(self, observations: list[HourObs]) -> "CapacityModel":
        """Fit from pre-aggregated hourly observations, recency-weighted.

        Per bucket we track a recency-DECAYED ceiling (so an old high uptake
        fades unless reconfirmed) and recency-weighted sums (recent days count
        more). The reference 'now' is the latest observation.
        """
        self.buckets = {}
        fed = sorted((o for o in observations if o.feed_kwh > 1e-6), key=lambda o: o.ts)
        if not fed:
            return self
        ref = fed[-1].ts
        per_day = 0.5 ** (1.0 / self.half_life_days) if self.half_life_days > 0 else 1.0

        grouped: dict[str, list[HourObs]] = defaultdict(list)
        for o in fed:
            grouped[bucket_key(o.ts)].append(o)

        for key, obs_list in grouped.items():
            b = BucketStats()
            mx, mx_cens, last = 0.0, False, None
            for o in obs_list:
                if last is not None:                       # decay running ceiling
                    mx *= per_day ** max(0, (o.ts - last).days)
                if o.absorbed_kwh > mx:                     # new (or recovered) ceiling
                    mx, mx_cens = o.absorbed_kwh, o.censored
                last = o.ts
                w = per_day ** max(0, (ref - o.ts).days)    # recency weight
                b.n += 1
                b.weight += w
                b.sum_absorbed += w * o.absorbed_kwh
                if o.censored:
                    b.n_censored += 1
            mx *= per_day ** max(0, (ref - last).days)       # fade if quiet since
            b.max_absorbed, b.max_was_censored = round(mx, 4), mx_cens
            self.buckets[key] = b
        return self

    # ---- inference ------------------------------------------------------
    def recommend_capacity(
        self, ts: datetime, mode: str | None = None, aggressiveness: float | None = None
    ) -> tuple[float, bool, BucketStats | None]:
        """Recommended feed ceiling (kWh for that hour) and whether it's a probe.

        mode "locked": never probe -- feed the learned *typical* uptake (mean
        of what the EG actually absorbed), so we give exactly what's taken with
        minimal spill. Unknown contexts get nothing (no probing when locked).
        mode "explore": probe above the best-seen uptake wherever the ceiling is
        still unknown (censored) or we have too few observations.
        """
        mode = mode or self.mode
        b = self.buckets.get(bucket_key(ts))

        if mode == "locked":
            if b is None or b.n == 0:
                return 0.0, False, b
            return round(b.mean_absorbed, 4), False, b

        if b is None or b.weight <= 0:
            return COLD_START_PROBE_KWH, True, b  # unknown/forgotten context -> probe
        aggr = self.aggressiveness if aggressiveness is None else aggressiveness
        # Confidence uses the EFFECTIVE recent observation count (sum of recency
        # weights), so a bucket that's gone quiet decays back to "unsure" and is
        # re-explored, and the ceiling can correct downward over time.
        unsure = b.max_was_censored or b.weight < MIN_CONFIDENT_OBS
        if unsure:
            cap = b.max_absorbed * (1.0 + aggr)
            # ensure a probe even when best-seen was tiny/zero but censored
            cap = max(cap, b.max_absorbed + COLD_START_PROBE_KWH)
            return round(cap, 4), True, b
        return round(b.max_absorbed, 4), False, b  # ceiling known -> exploit

    # ---- persistence ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "aggressiveness": self.aggressiveness,
            "mode": self.mode,
            "half_life_days": self.half_life_days,
            "buckets": {k: asdict(v) for k, v in self.buckets.items()},
        }

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict) -> "CapacityModel":
        m = cls(aggressiveness=d.get("aggressiveness", 0.15), mode=d.get("mode", "explore"),
                half_life_days=d.get("half_life_days", 45.0))
        # tolerate older saved buckets without the recency fields
        valid = {f.name for f in __import__("dataclasses").fields(BucketStats)}
        m.buckets = {k: BucketStats(**{kk: vv for kk, vv in v.items() if kk in valid})
                     for k, v in d.get("buckets", {}).items()}
        return m

    @classmethod
    def load(cls, path: Path | str) -> "CapacityModel | None":
        p = Path(path)
        if not p.exists():
            return None
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
