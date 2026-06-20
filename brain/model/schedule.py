"""Turn tonight's EG budget into an hour-by-hour feed plan.

Given the autarky-safe energy budget (from the simulation) and the learned
per-hour absorption capacities, allocate the budget across the feed window to
maximise expected uptake -- filling the hours the community can absorb most,
and marking the hours where we're deliberately probing above known uptake.

This replaces Phase 2's flat spread. The budget ceiling (autarky) is never
exceeded; the model only decides *how* to spend it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from brain.forecast.simulate import simulate_soc
from brain.model.capacity import CapacityModel


@dataclass(frozen=True, slots=True)
class HourPlan:
    hour: int
    ts: datetime
    feed_kwh: float
    capacity_kwh: float
    explore: bool


def hours_until(now: datetime, until: datetime, cap_hours: int = 48) -> list[datetime]:
    """Whole hours from `now` up to (not past) `until`.

    No fixed clock window: we feed across whatever hours run from now until the
    battery's trough (after which PV is recharging). The model decides which of
    those hours actually get energy -- daytime hours simply carry ~0 capacity.
    """
    t = now.replace(minute=0, second=0, microsecond=0)
    hours: list[datetime] = []
    while t < until and len(hours) < cap_hours:
        if t + timedelta(hours=1) > now:   # this hour still has time left
            hours.append(t)
        t += timedelta(hours=1)
    if not hours:                          # at/just before the trough -> feed now
        hours = [now.replace(minute=0, second=0, microsecond=0)]
    return hours


def plan_feed(
    budget_kwh: float,
    now: datetime,
    until: datetime,
    model: CapacityModel,
    max_per_hour_kwh: float,
    mode: str | None = None,
    aggressiveness: float | None = None,
) -> list[HourPlan]:
    """Allocate `budget_kwh` across the hours up to the trough, by UCB capacity."""
    hours = hours_until(now, until)
    caps = []
    for t in hours:
        cap, explore, _ = model.recommend_capacity(t, mode=mode, aggressiveness=aggressiveness)
        caps.append((t, min(cap, max_per_hour_kwh), explore))

    # Greedy water-fill: give the most to the hours that can absorb the most.
    order = sorted(range(len(caps)), key=lambda i: caps[i][1], reverse=True)
    alloc = [0.0] * len(caps)
    remaining = budget_kwh
    for i in order:
        if remaining <= 0:
            break
        give = min(caps[i][1], remaining)
        alloc[i] = give
        remaining -= give

    return [
        HourPlan(hour=caps[i][0].hour, ts=caps[i][0], feed_kwh=round(alloc[i], 4),
                 capacity_kwh=round(caps[i][1], 4), explore=caps[i][2] and alloc[i] > 0)
        for i in range(len(caps))
    ]


def plan_feed_autarky(
    now: datetime,
    soc_kwh: float,
    capacity_kwh: float,
    target_kwh: float,
    load_kw: float,
    pv_slots: tuple | list,
    model: CapacityModel,
    max_per_hour_kwh: float,
    mode: str | None = None,
    aggressiveness: float | None = None,
    horizon_h: float = 36.0,
) -> list[HourPlan]:
    """Per-hour feed plan under a hard autarky floor.

    For every hour in the horizon (daytime included) we may feed up to the EG's
    learned capacity that hour -- PROVIDED the simulated SoC never drops below
    `target_kwh` at any point in the horizon. Because the horizon spans all of
    tomorrow, a cloudy tomorrow (battery can't refill) makes the floor bind and
    feeding is held back; a sunny tomorrow frees up daytime surplus to feed.

    Greedy: fill the highest-absorption hours first, each to the most the floor
    allows (binary search), re-simulating the whole trajectory each step so the
    constraint accounts for every feed already committed.
    """
    hours = hours_until(now, now + timedelta(hours=horizon_h))
    caps: dict[datetime, float] = {}
    expl: dict[datetime, bool] = {}
    for h in hours:
        cap, explore, _ = model.recommend_capacity(h, mode=mode, aggressiveness=aggressiveness)
        caps[h] = max(0.0, min(cap, max_per_hour_kwh))
        expl[h] = explore

    feed: dict[datetime, float] = {h: 0.0 for h in hours}

    def floor_ok(trial: dict) -> bool:
        traj = simulate_soc(now, soc_kwh, capacity_kwh, load_kw, list(pv_slots),
                            feed_by_hour=trial, horizon_h=horizon_h)
        return traj.trough_soc_kwh >= target_kwh - 1e-6  # trough = global min SoC

    # If we can't even hold the floor with zero feed, there's nothing to give.
    if not floor_ok(feed):
        return [HourPlan(h.hour, h, 0.0, round(caps[h], 4), False) for h in hours]

    for h in sorted(hours, key=lambda x: caps[x], reverse=True):
        if caps[h] <= 1e-6:
            continue
        lo, hi = 0.0, caps[h]
        trial = dict(feed)
        for _ in range(20):                 # binary-search the max feasible feed at h
            mid = (lo + hi) / 2.0
            trial[h] = mid
            if floor_ok(trial):
                lo = mid
            else:
                hi = mid
        feed[h] = lo                        # lo is always feasible; never round UP
                                            # (that would tip the trough below floor)

    return [
        HourPlan(hour=h.hour, ts=h, feed_kwh=round(feed[h], 3),
                 capacity_kwh=round(caps[h], 4), explore=expl[h] and feed[h] > 1e-4)
        for h in hours
    ]


def feed_now_kw(plan: list[HourPlan], now: datetime) -> tuple[float, bool]:
    """The feed rate (kW) for the current hour, and whether it's a probe."""
    for p in plan:
        if p.ts.hour == now.hour and p.ts.date() == now.date():
            return p.feed_kwh, p.explore  # kWh over a 1h slot == avg kW
    return 0.0, False
