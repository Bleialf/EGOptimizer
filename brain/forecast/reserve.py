"""Autarky reserve calculator -- the guardrail that always comes first.

The user states a simple goal: "feed optimally, but never let the battery drop
below X% before tomorrow's PV takes over." We answer it by *simulating* the
battery forward through the night (see ``simulate.py``): the lowest point of
that curve -- the trough -- is the moment of greatest risk, and it moves with
the weather (a cloudy morning pushes the trough later into the day, when PV
finally ramps).

Key identity: every kWh fed to the EG before the trough lowers the trough 1:1.
So the energy free to offer tonight is exactly:

    eg_budget = simulated_trough_SoC (with no EG feed) - target_SoC      (>= 0)

min SoC note: Victron enforces its physical floor; we never control SoC. The
``hard_min_soc_pct`` only clamps the user's target. All energy in kWh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from brain.forecast.simulate import Trajectory, simulate_soc


@dataclass(frozen=True, slots=True)
class ReserveInputs:
    now: datetime
    soc_pct: float
    capacity_kwh: float

    target_morning_soc_pct: float   # the user's knob: floor to protect at the trough
    hard_min_soc_pct: float = 10.0  # Victron's physical floor; clamps the target

    load_kw: float = 0.4            # overnight house draw (live, smoothed)
    pv_slots: tuple = ()            # hourly Solcast forecast (P10) for charging
    horizon_h: float = 24.0


@dataclass(frozen=True, slots=True)
class ReserveResult:
    eg_budget_kwh: float
    effective_target_pct: float
    trough_soc_pct: float            # simulated trough WITHOUT feeding (natural low)
    trough_time: str                 # ISO; when the battery is closest to empty
    pv_takeover_time: str | None     # ISO; when PV first covers the house load
    trajectory: Trajectory
    rationale: str


def compute_reserve(i: ReserveInputs) -> ReserveResult:
    cap = max(i.capacity_kwh, 1e-9)
    target_pct = max(i.target_morning_soc_pct, i.hard_min_soc_pct)
    target_kwh = cap * target_pct / 100.0
    soc_now_kwh = cap * i.soc_pct / 100.0

    # Simulate the natural curve with NO EG feed to find the trough.
    traj = simulate_soc(
        now=i.now,
        soc_kwh=soc_now_kwh,
        capacity_kwh=cap,
        load_kw=i.load_kw,
        pv_slots=list(i.pv_slots),
        feed_kw=0.0,
        horizon_h=i.horizon_h,
    )

    eg_budget = max(0.0, traj.trough_soc_kwh - target_kwh)
    takeover = traj.pv_takeover_time.isoformat(timespec="minutes") if traj.pv_takeover_time else None

    if eg_budget <= 0:
        why = (
            f"Hold: natural trough {traj.trough_soc_pct:.0f}% at "
            f"{traj.trough_time:%a %H:%M} is already at/below the {target_pct:.0f}% "
            f"target (house draw {i.load_kw:.2f} kW until PV takes over"
            + (f" ~{takeover[-5:]}" if takeover else " beyond horizon") + ")."
        )
    else:
        why = (
            f"Feed up to {eg_budget:.1f} kWh: trough lands {traj.trough_soc_pct:.0f}% "
            f"at {traj.trough_time:%a %H:%M}; keep {target_pct:.0f}% there. "
            f"PV covers the house from "
            + (f"{takeover[-5:]}." if takeover else "beyond the horizon.")
        )

    return ReserveResult(
        eg_budget_kwh=round(eg_budget, 3),
        effective_target_pct=round(target_pct, 1),
        trough_soc_pct=traj.trough_soc_pct,
        trough_time=traj.trough_time.isoformat(timespec="minutes"),
        pv_takeover_time=takeover,
        trajectory=traj,
        rationale=why,
    )
