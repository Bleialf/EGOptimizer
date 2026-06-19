# Architecture

EGOptimizer is a **hybrid**: a dependency-light Python "brain" (deployable as a
Docker container) does ingestion, storage, simulation and learning; Home
Assistant calls it and handles live state + actuation.

```
┌──────────────────────────── brain/ (Docker) ─────────────────────────────┐
│                                                                           │
│  providers/        ingest/         storage/        forecast/              │
│  ├ base.Provider   ├ run_import    └ Store         ├ simulate.simulate_soc │
│  └ netznoe         └ clean             (sqlite)    └ reserve.compute_reserve│
│        │              │                  ▲               ▲                 │
│        ▼              ▼                  │               │                 │
│   EnergyRecord ──► filter_outliers ──► energy_records    │                 │
│                                          │               │                 │
│   model/                                 ▼               │                 │
│   ├ context.bucket_key       aggregate_hourly            │                 │
│   ├ capacity.CapacityModel ◄── fit ──────┘               │                 │
│   ├ schedule.plan_feed                                   │                 │
│   └ train (nightly) ──► data/model.json                  │                 │
│                                                          │                 │
│  api/                                                    │                 │
│  ├ service.recommend ◄── reserve + model ────────────────┘                 │
│  └ server  (POST /recommend, GET /health, /decisions)                      │
│                                   ▲   │                                    │
└───────────────────────────────────┼───┼────────────────────────────────────┘
                                    │   │ feed_kw, budget, trough, reasoning
              state (SoC, load,     │   ▼
              Solcast hourly) ──────┘  Home Assistant  (HACS integration)
                                       sensors • dashboard • (Phase 4) Victron
```

## The normalised data model

Everything flows as `EnergyRecord` (see [brain/records.py](../brain/records.py)):
one 15-minute interval per meter, where

```
feed_in_kwh = eg_absorbed_kwh + eg_surplus_kwh    (when settled)
```

`eg_absorbed_kwh` is the learning signal (what the community took);
`eg_surplus_kwh ≈ 0` while `feed_in_kwh > 0` means the interval was **censored**
(true capacity ≥ what we fed). EG columns are `None` until the allocation
settles (~1 day lag in the NetzNÖ export).

## The autarky simulation (`forecast/`)

`compute_reserve` runs `simulate_soc` forward from now over a 24 h horizon,
draining the battery for house load and charging it from the hourly Solcast
(P10) forecast. The lowest point — the **trough** — is the moment of greatest
risk; it is *discovered* from the PV curve, so a cloudy morning correctly pushes
it later in the day. Because every kWh fed before the trough lowers it 1:1:

```
eg_budget = simulated_trough_SoC (no feed) − target_SoC      (clamped ≥ 0)
```

Victron enforces the physical minimum SoC; the brain only spends energy above
the user's morning target.

## The learning model (`model/`)

A **contextual bandit**. Context = `season | weekday-type | hour`. Per bucket we
track the best uptake seen and whether it was censored:

- **explore** mode: if the ceiling is unseen (censored) or data is thin, feed
  `max_absorbed × (1 + aggressiveness)` — a UCB-style probe.
- **locked** mode: feed `mean_absorbed` — exactly the typical uptake, no probe.

`schedule.plan_feed` water-fills the autarky budget into the window's hours by
capacity. `train` refits nightly from the store; the API hot-loads
`data/model.json` per request.

Why not deep RL? ~1 decision/night → too few episodes. The bandit learns from
the same signal without the data starvation, and the safety floor is
deterministic, not learned. See the backtest in
[brain/analysis/evaluate.py](../brain/analysis/evaluate.py).

## The API (`api/`)

`recommend(state, config, store, model)` is a pure function: state in →
recommendation out. `server.py` is a stdlib `http.server` adapter (swap for
FastAPI without touching the core). Every decision is logged to the `decisions`
table for the reward-join.

## Extending: new providers

Implement `Provider.parse()` in `brain/providers/<name>.py`, register it in
`providers/__init__.py`. The pipeline only ever sees `EnergyRecord`.

## Module map

| Path | Responsibility |
|------|----------------|
| `brain/records.py` | normalised `EnergyRecord` |
| `brain/providers/` | source plugins (NetzNÖ today) |
| `brain/ingest/` | import CLI + outlier cleaning |
| `brain/storage/` | SQLite store (idempotent upsert, decisions log) |
| `brain/forecast/` | battery simulation + autarky reserve |
| `brain/model/` | context, censored capacity model, scheduler, training |
| `brain/api/` | `recommend()` + HTTP server |
| `brain/analysis/` | absorption analysis, backtest, reward-join |
| `custom_components/egoptimizer/` | HACS integration (HA side) |
