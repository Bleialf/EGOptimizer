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

from brain.model.capacity import CapacityModel


@dataclass(frozen=True, slots=True)
class HourPlan:
    hour: int
    ts: datetime
    feed_kwh: float
    capacity_kwh: float
    explore: bool


def _window_hours(now: datetime, start_h: int, end_h: int) -> list[datetime]:
    """The remaining whole hours inside the feed window, from `now` forward."""
    wraps = start_h > end_h
    hours = []
    t = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(24):
        h = t.hour
        inside = (h >= start_h or h < end_h) if wraps else (start_h <= h < end_h)
        if inside and t >= now.replace(minute=0, second=0, microsecond=0):
            hours.append(t)
        t += timedelta(hours=1)
        if len(hours) >= 24:
            break
    # stop at the first exit from the window after we've entered it
    trimmed = []
    for t in hours:
        h = t.hour
        inside = (h >= start_h or h < end_h) if wraps else (start_h <= h < end_h)
        if inside:
            trimmed.append(t)
    return trimmed


def plan_feed(
    budget_kwh: float,
    now: datetime,
    start_h: int,
    end_h: int,
    model: CapacityModel,
    max_per_hour_kwh: float,
    mode: str | None = None,
) -> list[HourPlan]:
    """Allocate `budget_kwh` across the window's hours by UCB capacity."""
    hours = _window_hours(now, start_h, end_h)
    caps = []
    for t in hours:
        cap, explore, _ = model.recommend_capacity(t, mode=mode)
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


def feed_now_kw(plan: list[HourPlan], now: datetime) -> tuple[float, bool]:
    """The feed rate (kW) for the current hour, and whether it's a probe."""
    for p in plan:
        if p.ts.hour == now.hour and p.ts.date() == now.date():
            return p.feed_kwh, p.explore  # kWh over a 1h slot == avg kW
    return 0.0, False
