"""Forward battery simulation.

Steps the battery state through time from `now`, drawing it down for house
load (and optionally EG feed) and charging it from the hourly PV forecast. The
point of lowest charge -- the *trough* -- is where the battery comes closest to
empty, which happens in the morning when PV finally ramps up enough to cover
the house and the battery stops discharging.

We do NOT assume a fixed "morning" time: the trough is discovered from the PV
curve, so it shifts correctly with season and weather.

PV slots accept Solcast's native ``detailedHourly`` entries (period_start +
pv_estimate10 kWh-per-hour, P10 preferred) or our own ``pv_kwh`` key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class SimPoint:
    t: datetime
    soc_kwh: float
    soc_pct: float
    pv_kw: float
    load_kw: float


@dataclass(frozen=True, slots=True)
class Trajectory:
    points: list[SimPoint]
    trough_soc_kwh: float
    trough_soc_pct: float
    trough_time: datetime
    pv_takeover_time: datetime | None  # first time PV >= load (battery stops net-draining)


def _pv_table(pv_slots: list[dict] | None) -> dict[tuple, float]:
    """Map (date, hour) -> PV energy (kWh) for that hour, P10 preferred."""
    table: dict[tuple, float] = {}
    for slot in pv_slots or []:
        try:
            start = datetime.fromisoformat(
                str(slot["period_start"]).replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (KeyError, ValueError, TypeError):
            continue
        pv = slot.get("pv_kwh")
        if pv is None:
            pv = slot.get("pv_estimate10", slot.get("pv_estimate", 0.0))
        table[(start.date(), start.hour)] = float(pv)
    return table


def simulate_soc(
    now: datetime,
    soc_kwh: float,
    capacity_kwh: float,
    load_kw: float,
    pv_slots: list[dict] | None,
    feed_kw: float = 0.0,
    horizon_h: float = 24.0,
    step_min: int = 15,
    feed_by_hour: dict | None = None,
) -> Trajectory:
    """Simulate the SoC curve from `now` over `horizon_h`.

    A 1-hour Solcast slot's kWh is treated as average kW over that hour.
    EG discharge is either a constant `feed_kw`, or — when `feed_by_hour` is
    given — a per-hour schedule {hour_start_datetime: kWh-for-that-hour} (the
    kWh is treated as an average kW over the hour). The per-hour form is what
    the autarky planner uses to test a candidate plan against the SoC floor.
    """
    cap = max(capacity_kwh, 1e-9)
    table = _pv_table(pv_slots)
    step_h = step_min / 60.0
    steps = int(round(horizon_h / step_h))

    soc = max(0.0, min(soc_kwh, cap))
    points: list[SimPoint] = []
    takeover: datetime | None = None
    t = now
    for idx in range(steps + 1):
        pv_kw = table.get((t.date(), t.hour), 0.0)  # kWh/h == avg kW
        if feed_by_hour is not None:
            this_feed = feed_by_hour.get(t.replace(minute=0, second=0, microsecond=0), 0.0)
        else:
            this_feed = feed_kw
        points.append(
            SimPoint(t, round(soc, 3), round(100.0 * soc / cap, 1),
                     round(pv_kw, 3), round(load_kw, 3))
        )
        if takeover is None and idx > 0 and pv_kw >= load_kw:
            takeover = t
        net_kw = pv_kw - load_kw - this_feed
        soc = max(0.0, min(cap, soc + net_kw * step_h))
        t += timedelta(minutes=step_min)

    trough = min(points, key=lambda p: p.soc_kwh)
    return Trajectory(
        points=points,
        trough_soc_kwh=trough.soc_kwh,
        trough_soc_pct=trough.soc_pct,
        trough_time=trough.t,
        pv_takeover_time=takeover,
    )
