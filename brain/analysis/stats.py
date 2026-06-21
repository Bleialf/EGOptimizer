"""Compact EG-absorption statistics for the API (and Home Assistant sensors).

``absorption_stats`` distils the stored history into a small flat dict of
headline numbers -- how much the community absorbed vs spilled, how often we
were censored (EG took 100%), and the same for a recent window -- so the
integration can surface a few sensors without re-deriving anything. The
detailed by-hour/by-month breakdown stays in ``analysis/absorption.py`` (the
human-readable CLI report).

Pure stdlib; pure function of the records (no I/O).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from brain.records import EnergyRecord

RECENT_DAYS = 30


def _rate(absorbed: float, fed: float) -> float:
    return absorbed / fed if fed > 1e-9 else 0.0


def _block(fed: list[EnergyRecord]) -> dict:
    """Headline numbers for a set of settled, fed-in intervals."""
    total_fed = sum(r.feed_in_kwh for r in fed)
    total_abs = sum(r.eg_absorbed_kwh for r in fed)
    total_spill = sum(r.eg_surplus_kwh for r in fed)
    censored = sum(1 for r in fed if r.fully_absorbed)
    return {
        "intervals": len(fed),
        "fed_kwh": round(total_fed, 2),
        "absorbed_kwh": round(total_abs, 2),
        "surplus_kwh": round(total_spill, 2),
        "absorption_rate": round(_rate(total_abs, total_fed), 4),
        "censored_pct": round(censored / len(fed), 4) if fed else 0.0,
    }


def absorption_stats(records: list[EnergyRecord]) -> dict:
    """Distil stored records into a flat dict of EG-absorption headline stats."""
    settled = [r for r in records if r.eg_settled]
    fed = [r for r in settled if r.feed_in_kwh > 1e-9]

    out: dict = {
        "settled_intervals": len(settled),
        "fed_intervals": len(fed),
        "first_ts": min((r.timestamp for r in records), default=None),
        "last_ts": max((r.timestamp for r in records), default=None),
        "last_settled_ts": max((r.timestamp for r in settled), default=None),
        "recent_days": RECENT_DAYS,
        "all_time": _block(fed),
        "recent": _block([]),
        "best_hour": None,
        "best_hour_rate": 0.0,
        "worst_hour": None,
        "worst_hour_rate": 0.0,
    }

    if not fed:
        out["first_ts"] = out["first_ts"].isoformat() if out["first_ts"] else None
        out["last_ts"] = out["last_ts"].isoformat() if out["last_ts"] else None
        out["last_settled_ts"] = (
            out["last_settled_ts"].isoformat() if out["last_settled_ts"] else None
        )
        return out

    # recent window, anchored on the newest settled interval (deterministic)
    anchor = out["last_settled_ts"]
    cutoff = anchor - timedelta(days=RECENT_DAYS)
    out["recent"] = _block([r for r in fed if r.timestamp >= cutoff])

    # best / worst absorbing hour of day (by absorbed/fed rate)
    by_hour_fed: dict[int, float] = defaultdict(float)
    by_hour_abs: dict[int, float] = defaultdict(float)
    for r in fed:
        by_hour_fed[r.timestamp.hour] += r.feed_in_kwh
        by_hour_abs[r.timestamp.hour] += r.eg_absorbed_kwh
    hour_rates = {h: _rate(by_hour_abs[h], by_hour_fed[h]) for h in by_hour_fed}
    best = max(hour_rates, key=hour_rates.get)
    worst = min(hour_rates, key=hour_rates.get)
    out["best_hour"], out["best_hour_rate"] = best, round(hour_rates[best], 4)
    out["worst_hour"], out["worst_hour_rate"] = worst, round(hour_rates[worst], 4)

    # ISO-format timestamps last (kept as datetimes for the math above)
    out["first_ts"] = out["first_ts"].isoformat()
    out["last_ts"] = out["last_ts"].isoformat()
    out["last_settled_ts"] = out["last_settled_ts"].isoformat()
    return out
