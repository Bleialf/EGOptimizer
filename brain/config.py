"""Configuration with safe built-in defaults.

Everything runs without a config file (defaults below) and without PyYAML.
If ``brain/config.yaml`` exists *and* PyYAML is installed, it is deep-merged
over the defaults. This keeps Phase 1/2 zero-dependency while allowing real
config later.
"""

from __future__ import annotations

import copy
from pathlib import Path

DEFAULTS: dict = {
    "storage": {"db_path": "data/egoptimizer.sqlite"},
    "battery": {
        "capacity_kwh": 10.0,
        "max_charge_kw": 5.0,
        "max_discharge_kw": 5.0,
        "inverter": "victron",
    },
    "autarky": {
        # THE user knob: desired battery SoC floor BY MORNING. "Feed optimally
        # but don't drop below this % in the morning." Sent per-request from HA
        # (an input_number you can slide); this is the fallback.
        "target_morning_soc_pct": 50.0,
        # Victron's physical hard floor. Used only to clamp the target -- we
        # never enforce SoC (Victron does).
        "hard_min_soc_pct": 10.0,
        # When "morning" is (local hour). The reserve guarantees the target by
        # this time; overnight PV is accumulated up to here.
        "morning_hour": 7,
        # Average overnight household draw (kW), fallback when HA doesn't send
        # a measured (smoothed) value. Used as the simulation's load rate.
        "night_load_kw": 0.4,
        # Minimum simulation horizon (h). The service extends it to the end of
        # the PV forecast so the autarky floor accounts for ALL of tomorrow's
        # recharge (a cloudy tomorrow then holds feeding back). It never extends
        # blindly past the forecast (that would assume 0 PV -> false trough).
        "sim_horizon_h": 24.0,
    },
    # No fixed feed window: timing comes from the learned per-hour absorption
    # model (where the EG actually takes energy) bounded by the autarky trough
    # (feed only while the battery is heading to its low point).
    "api": {"host": "0.0.0.0", "port": 8787},
    "model": {
        # "explore": probe above known uptake to keep learning (overfeed a bit).
        # "locked":  stop probing, feed exactly the learned typical uptake.
        # Train in "explore" for a while, then flip to "locked" (config or an HA
        # switch via the request's "mode" field).
        "mode": "explore",
        "exploration": "ucb",
        "exploration_aggressiveness": 0.15,  # how hard to probe while exploring
        # Recency half-life (days): a bucket's learned ceiling halves every this
        # many days unless reconfirmed, and recent observations weigh more. Lets
        # the model adapt UP and DOWN and re-explore contexts gone quiet.
        # 0 = legacy all-time max (no forgetting).
        "learn_half_life_days": 45.0,
        "path": "data/model.json",
    },
    "ingest": {
        # Sanity ceiling for a single 15-min feed-in interval (kWh). Anything
        # larger is a meter artifact (e.g. NetzNOE's init spike) and is dropped.
        # 5 kWh/15min == 20 kW sustained, above any realistic home export.
        "max_interval_kwh": 5.0,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path = "brain/config.yaml") -> dict:
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return copy.deepcopy(DEFAULTS)
    with path.open("r", encoding="utf-8") as fh:
        user = yaml.safe_load(fh) or {}
    return _deep_merge(DEFAULTS, user)
