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
from brain.forecast.simulate import simulate_soc
from brain.model.capacity import CapacityModel
from brain.model.context import bucket_key
from brain.model.schedule import feed_now_kw, hours_until, plan_feed_autarky
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


def _horizon_to_forecast_end(now, pv_slots, min_h: float = 24.0, cap_h: float = 48.0) -> float:
    """Hours to simulate: extend to the last PV slot (+1h) so the floor sees all
    of tomorrow's recharge, but never past the forecast (that would assume 0 PV
    and drain to a false trough). Clamped to [min_h, cap_h]."""
    last = None
    for s in pv_slots or ():
        try:
            dt = datetime.fromisoformat(
                str(s["period_start"]).replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (KeyError, ValueError, TypeError):
            continue
        if last is None or dt > last:
            last = dt
    if last is None:
        return min_h
    span = (last - now).total_seconds() / 3600.0 + 1.0
    return max(min_h, min(span, cap_h))


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

    # Simulate to the END of the PV forecast (covers all of tomorrow), so the
    # autarky floor accounts for tomorrow's recharge -- a cloudy tomorrow keeps
    # the SoC low and holds feeding back. We do NOT extend blindly past the
    # forecast (that would assume 0 PV and drain to a false trough).
    pv_slots = tuple(state.get("pv_forecast") or ())
    horizon_h = _horizon_to_forecast_end(now, pv_slots, float(aut.get("sim_horizon_h", 24.0)))

    res = compute_reserve(
        ReserveInputs(
            now=now,
            soc_pct=soc,
            capacity_kwh=capacity,
            target_morning_soc_pct=target_morning,
            hard_min_soc_pct=hard_min,
            load_kw=load_kw,
            pv_slots=pv_slots,
            horizon_h=horizon_h,
        )
    )

    # Per-hour, autarky-bounded plan: we may feed ANY hour (daytime included) up
    # to the EG's learned capacity, as long as the simulated SoC never drops
    # below the morning target across a horizon spanning all of tomorrow. So a
    # cloudy tomorrow (battery can't refill) holds energy back; a sunny one frees
    # daytime surplus. Recomputed every cycle from the LIVE SoC, so extra night
    # demand (a party) pulls feeding back on its own.
    aggressiveness = state.get("exploration_aggressiveness")
    mode = state.get("mode") or config["model"].get("mode", "explore")
    soc_kwh = capacity * soc / 100.0
    target_kwh = capacity * res.effective_target_pct / 100.0
    explore = False
    feed_plan: list[dict] = []
    confidence = "no_model"
    context_obs = 0
    cur_cap = None
    cur_stats = None
    decision_path = "no_budget"
    model_bucket_count = len(model.buckets) if model is not None else 0
    planned_total = 0.0
    # Trajectory to DISPLAY. Default = the no-feed baseline; once we have a plan
    # we re-simulate WITH its feeds so the trough/forecast reflect what will
    # actually happen (the plan drives SoC down to the target -- showing the
    # no-feed trough would wrongly read high and contradict the trimmed hours).
    disp_traj = res.trajectory

    if model is not None and model.buckets:
        plan = plan_feed_autarky(
            now, soc_kwh, capacity, target_kwh, load_kw, pv_slots, model,
            bat["max_discharge_kw"], mode=mode, aggressiveness=aggressiveness,
            horizon_h=horizon_h,
        )
        feed_plan = [
            {"time": p.ts.isoformat(timespec="minutes"), "hour": p.hour,
             "feed_kwh": p.feed_kwh, "capacity_kwh": p.capacity_kwh, "explore": p.explore}
            for p in plan
        ]
        planned_total = sum(p.feed_kwh for p in plan)
        if planned_total > 1e-6:
            disp_traj = simulate_soc(
                now, soc_kwh, capacity, load_kw, pv_slots,
                feed_by_hour={p.ts: p.feed_kwh for p in plan}, horizon_h=horizon_h,
            )
        feed_kwh, explore = feed_now_kw(plan, now)
        feed_kw = min(feed_kwh, bat["max_discharge_kw"])
        cur_cap, _, cur_stats = model.recommend_capacity(now, mode=mode, aggressiveness=aggressiveness)
        context_obs = cur_stats.n if cur_stats else 0
        confidence = "locked" if mode == "locked" else ("probing" if explore else "confident")
        if planned_total <= 1e-6:
            feed_kw, status, decision_path = 0.0, "no_budget", "budget_blocked"
            note = (f"Autarky floor holds everything back: the battery can't stay "
                    f">= {res.effective_target_pct:.0f}% through tomorrow "
                    f"(low SoC and/or weak PV forecast) -> feed 0.")
        elif feed_kw > 0:
            status, decision_path = "feeding", "learned_plan"
            note = (
                f"Autarky-safe plan ({mode}): feed {feed_kw:.2f} kW now "
                + {"probing": "(PROBING above known uptake to learn).",
                   "confident": "(confident: feeding the known ceiling).",
                   "locked": "(locked: feeding the learned uptake)."}[confidence]
                + f" {planned_total:.1f} kWh planned; SoC held >= "
                f"{res.effective_target_pct:.0f}% across the horizon."
            )
        else:
            status, decision_path = "holding", "learned_plan"
            note = (f"Autarky-safe plan ({mode}): EG absorbs ~nothing this hour -> "
                    f"hold; {planned_total:.1f} kWh planned for higher-uptake hours.")
    else:
        # Fallback when no model is trained yet: flat spread of the overnight
        # headroom until the trough.
        confidence = "no_model"
        budget = max(res.eg_budget_kwh, 0.0)
        if budget <= 1e-6:
            feed_kw, status, decision_path = 0.0, "no_budget", "budget_blocked"
            note = "No budget after autarky reserve -> feed 0."
        else:
            trough_dt = datetime.fromisoformat(res.trough_time)
            hrs = max(1, len(hours_until(now, trough_dt)))
            feed_kw = min(budget / hrs, bat["max_discharge_kw"])
            planned_total = budget
            status, decision_path = ("feeding" if feed_kw > 0 else "holding"), "fallback_no_model"
            note = f"No model yet; flat spread of {budget:.1f} kWh until the trough."

    # When/when's the next planned feed, for a clear "what happens next".
    next_feed = next((p["time"] for p in feed_plan if p["feed_kwh"] > 0), None)

    # Displayed trough/forecast come from the PLANNED trajectory (with feeds),
    # so they reflect what will actually happen, not the no-feed baseline.
    disp_trough_pct = disp_traj.trough_soc_pct
    disp_trough_iso = disp_traj.trough_time.isoformat(timespec="minutes")
    disp_pv_iso = (disp_traj.pv_takeover_time.isoformat(timespec="minutes")
                   if disp_traj.pv_takeover_time else None)

    # Plain-language "what it's doing" sentence. Kept well under HA's 255-char
    # sensor-state limit so it never gets truncated on the dashboard.
    _hhmm = lambda iso: iso[11:16] if iso and len(iso) >= 16 else "—"
    target = res.effective_target_pct
    solar = (f"solar takes over at {_hhmm(disp_pv_iso)}"
             if disp_pv_iso else "no solar recovery in forecast")
    low = f"lowest ~{disp_trough_pct:.0f}% at {_hhmm(disp_trough_iso)}, then {solar}"
    probe_phrase = {
        "probing": " (testing a bit higher to learn)",
        "confident": " (the known amount)",
        "locked": " (locked to the learned amount)",
        "no_model": "",
    }.get(confidence, "")

    if status == "feeding":
        reasoning = (f"Giving {feed_kw:.2f} kW now{probe_phrase}. Tonight: "
                     f"{planned_total:.1f} kWh total, keeping the battery at least "
                     f"{target:.0f}% by morning — {low}.")
    elif status == "no_budget":
        reasoning = (f"Nothing to give — staying at least {target:.0f}% by morning "
                     f"leaves no spare ({low}).")
    else:  # holding
        reasoning = (f"Waiting — the community takes almost nothing this hour. "
                     f"{planned_total:.1f} kWh planned for later, keeping at least "
                     f"{target:.0f}% by morning ({low}).")

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
        "trough_soc_pct": disp_trough_pct,           # AFTER the plan's feeds
        "trough_time": disp_trough_iso,
        "pv_takeover_time": disp_pv_iso,
        "load_kw": round(load_kw, 3),
        "rationale": reasoning,
        "feed_plan": feed_plan,                      # full hour-by-hour schedule
        "soc_forecast": _downsample(disp_traj.points),
        # Structured trace of WHY this decision happened -- for debugging.
        "debug": {
            "decision": {
                "path": decision_path,
                "status": status,
                "confidence": confidence,
                "note": note,
            },
            "inputs": {
                "soc_pct": soc, "capacity_kwh": capacity,
                "load_kw": round(load_kw, 3),     # the (base/overnight) load used for the sim
                "load_now_kw": _f(state, "load_now_kw", load_kw),  # immediate draw, for reference
                "target_morning_soc_pct": res.effective_target_pct,
                "hard_min_soc_pct": hard_min, "mode": mode,
                "exploration_aggressiveness": aggressiveness,
                "plan_until": res.trough_time,   # feed across now -> trough
            },
            "model": {
                "loaded": model is not None,
                "bucket_count": model_bucket_count,
                "has_current_bucket": bool(model is not None and bucket_key(now) in model.buckets),
            },
            "autarky": {
                "eg_budget_kwh": res.eg_budget_kwh,                 # overnight headroom (no-feed)
                "trough_soc_pct": disp_trough_pct,                 # AFTER the plan's feeds
                "trough_time": disp_trough_iso,
                "trough_soc_pct_nofeed": res.trough_soc_pct,        # baseline, for reference
                "pv_takeover_time": disp_pv_iso,
                "reserve_note": res.rationale,
            },
            "context": {
                "bucket": bucket_key(now),
                "observations": context_obs,                       # raw nights of data
                "effective_recent_obs": round(cur_stats.weight, 2) if cur_stats else None,
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
