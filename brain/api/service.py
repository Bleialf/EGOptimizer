"""The recommend() core: state in -> grid feed-in recommendation out.

Home Assistant POSTs the current state; this returns how much to put into the
grid right now, plus tonight's EG budget and the reasoning, and logs the
decision so the Phase-3 bandit can later join it against actual EG uptake.

The autarky budget comes from a forward battery simulation (see reserve.py /
simulate.py): we never assume a fixed "morning" time -- the trough is found
from the PV curve, so a cloudy morning correctly pushes it later in the day.

min SoC note: Victron enforces the SoC floor physically -- we do NOT clamp or
control it. The target only bounds the *usable* energy in the math.

This is Phase 2: autarky reserve + a transparent static spread of the budget
across the feed window. Phase 3 replaces the spread with the learned,
absorption-aware schedule and UCB exploration.
"""

from __future__ import annotations

import json
from datetime import datetime

from brain.forecast.reserve import ReserveInputs, compute_reserve
from brain.model.capacity import CapacityModel
from brain.model.context import bucket_key
from brain.model.schedule import feed_now_kw, plan_feed
from brain.storage import Store

VERSION = "phase3-bandit"


def _f(state: dict, key: str, default: float) -> float:
    v = state.get(key)
    return float(v) if v is not None else float(default)


def _first(*values) -> float:
    for v in values:
        if v is not None:
            return float(v)
    return 0.0


def _hours_left_in_window(now: datetime, start_h: int, end_h: int) -> float:
    """Hours from `now` until the feed window's end. 0 if outside the window.

    Window wraps midnight when start_h > end_h (e.g. 19:00 -> 07:00).
    """
    h = now.hour + now.minute / 60.0
    wraps = start_h > end_h
    in_window = (h >= start_h or h < end_h) if wraps else (start_h <= h < end_h)
    if not in_window:
        return 0.0
    end = end_h + (24 if (wraps and h >= start_h) else 0)
    return max(0.0, end - h)


def _downsample(points, every_min: int = 60) -> list[dict]:
    """Hourly trajectory for HA charting (keep it compact)."""
    step = max(1, every_min // 15)
    return [
        {"t": p.t.isoformat(timespec="minutes"), "soc_pct": p.soc_pct, "pv_kw": p.pv_kw}
        for p in points[::step]
    ]


def recommend(
    state: dict,
    config: dict,
    store: Store | None = None,
    model: CapacityModel | None = None,
) -> dict:
    bat = config["battery"]
    aut = config["autarky"]
    win = config["feed_window"]

    now = (
        datetime.fromisoformat(state["timestamp"])
        if state.get("timestamp")
        else datetime.now()
    )

    capacity = _f(state, "capacity_kwh", bat["capacity_kwh"])
    soc = _f(state, "soc_pct", 0.0)
    target_morning = _f(state, "target_morning_soc_pct", aut["target_morning_soc_pct"])
    hard_min = _f(state, "hard_min_soc_pct", aut["hard_min_soc_pct"])
    # Overnight house draw: live smoothed load preferred, then config fallback.
    load_kw = _first(state.get("night_load_kw"), state.get("load_now_kw"), aut["night_load_kw"])

    res = compute_reserve(
        ReserveInputs(
            now=now,
            soc_pct=soc,
            capacity_kwh=capacity,
            target_morning_soc_pct=target_morning,
            hard_min_soc_pct=hard_min,
            load_kw=load_kw,
            pv_slots=tuple(state.get("pv_forecast") or ()),
            horizon_h=float(aut.get("sim_horizon_h", 24.0)),
        )
    )

    start_h, end_h = int(win["start_hour"]), int(win["end_hour"])
    hours_left = _hours_left_in_window(now, start_h, end_h)
    mode = state.get("mode") or config["model"].get("mode", "explore")
    explore = False
    feed_plan: list[dict] = []
    confidence = "no_model"
    context_obs = 0
    cur_cap = None
    cur_stats = None

    if res.eg_budget_kwh <= 0:
        feed_kw, status = 0.0, "no_budget"
        note = "No budget after autarky reserve -> feed 0."
    elif hours_left <= 0:
        feed_kw, status = 0.0, "holding"
        note = "Outside feed window -> hold (EG saturated in daytime)."
    elif model is not None and model.buckets:
        # Phase 3: spend the budget where the community absorbs most. "explore"
        # probes higher to learn; "locked" feeds the learned uptake (no overshoot).
        plan = plan_feed(res.eg_budget_kwh, now, start_h, end_h, model,
                         bat["max_discharge_kw"], mode=mode)
        feed_plan = [
            {"time": p.ts.isoformat(timespec="minutes"), "hour": p.hour,
             "feed_kwh": p.feed_kwh, "capacity_kwh": p.capacity_kwh, "explore": p.explore}
            for p in plan
        ]
        feed_kwh, explore = feed_now_kw(plan, now)
        feed_kw = min(feed_kwh, bat["max_discharge_kw"])
        # How confident are we about THIS hour's context?
        cur_cap, _, cur_stats = model.recommend_capacity(now, mode=mode)
        context_obs = cur_stats.n if cur_stats else 0
        if mode == "locked":
            confidence = "locked"
        elif explore:
            confidence = "probing"
        else:
            confidence = "confident"
        status = "feeding" if feed_kw > 0 else "holding"
        note = (
            f"Learned plan ({mode}): feed {feed_kw:.2f} kW this hour "
            + {"probing": "(PROBING above known uptake to learn).",
               "confident": "(confident: feeding the known ceiling).",
               "locked": "(locked: feeding exactly the learned uptake)."}[confidence]
        )
    else:
        # Phase 2 fallback when no model is trained yet: flat spread.
        feed_kw = min(res.eg_budget_kwh / hours_left, bat["max_discharge_kw"])
        status, confidence = ("feeding" if feed_kw > 0 else "holding"), "no_model"
        note = f"No model yet; flat spread of {res.eg_budget_kwh:.1f} kWh over {hours_left:.1f} h."

    # When/when's the next planned feed, for a clear "what happens next".
    next_feed = next((p["time"] for p in feed_plan if p["feed_kwh"] > 0), None)

    response = {
        "version": VERSION,
        "decided_at": now.isoformat(timespec="minutes"),
        "feed_kw": round(feed_kw, 3),               # <-- the headline value
        "status": status,                            # feeding | probing-via confidence | holding | no_budget
        "confidence": confidence,                    # probing | confident | locked | no_model
        "explore": explore,                          # bool: are we overfeeding to learn right now?
        "mode": mode,
        "eg_budget_kwh": res.eg_budget_kwh,
        "planned_tonight_kwh": round(sum(p["feed_kwh"] for p in feed_plan), 3),
        "context_observations": context_obs,         # how much data backs this hour
        "next_feed_time": next_feed,
        "target_morning_soc_pct": res.effective_target_pct,
        "trough_soc_pct": res.trough_soc_pct,
        "trough_time": res.trough_time,
        "pv_takeover_time": res.pv_takeover_time,
        "load_kw": round(load_kw, 3),
        "rationale": f"{res.rationale} {note}",
        "feed_plan": feed_plan,                      # full hour-by-hour schedule
        "soc_forecast": _downsample(res.trajectory.points),
        # Structured trace of WHY this decision happened -- for debugging.
        "debug": {
            "inputs": {
                "soc_pct": soc, "capacity_kwh": capacity, "load_kw": round(load_kw, 3),
                "target_morning_soc_pct": res.effective_target_pct,
                "hard_min_soc_pct": hard_min, "mode": mode,
                "in_feed_window": hours_left > 0, "hours_left_in_window": round(hours_left, 2),
            },
            "autarky": {
                "eg_budget_kwh": res.eg_budget_kwh,
                "trough_soc_pct": res.trough_soc_pct, "trough_time": res.trough_time,
                "pv_takeover_time": res.pv_takeover_time,
                "reserve_note": res.rationale,
            },
            "context": {
                "bucket": bucket_key(now),
                "observations": context_obs,
                "max_absorbed_kwh": getattr(cur_stats, "max_absorbed", None),
                "mean_absorbed_kwh": round(cur_stats.mean_absorbed, 4) if cur_stats else None,
                "best_was_censored": getattr(cur_stats, "max_was_censored", None),
                "recommended_capacity_kwh": cur_cap,
            },
        },
    }

    if store is not None:
        # don't persist the (large) arrays in the decision log
        logged = {k: v for k, v in response.items() if k not in ("soc_forecast", "feed_plan")}
        store.log_decision(
            decided_at=response["decided_at"],
            request=json.dumps(state, default=str),
            response=json.dumps(logged),
            feed_kw=response["feed_kw"],
            eg_budget_kwh=response["eg_budget_kwh"],
            explore=explore,
        )
    return response
