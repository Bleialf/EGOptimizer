# EGOptimizer

**Optimise how much energy you feed into your Austrian energy community (EG) —
without ever compromising your own autarky — and learn to do it better over
time instead of blindly repeating the past.**

The output is simple: **how many kW to push into the grid right now.** It is
computed continuously from your battery state, tomorrow's PV forecast, your
live house load, and a model of what the community can actually absorb.

[![CI](https://github.com/bleialf/EGOptimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/bleialf/EGOptimizer/actions/workflows/ci.yml)
[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)

---

## Why this exists

In an energy community, the EG can only use what its members consume at that
moment. Feed in more than they can take and the surplus just spills to the grid.
The hard part: **you never see the ceiling.** The grid-operator data only tells
you how much the community absorbed *up to what you fed in* — it is **censored**.
If you always feed last week's amount, you can never discover that they might
have taken more.

EGOptimizer treats this as what it is — a **contextual bandit** (the right-sized
reinforcement learning for ~one decision per night):

1. **Autarky first.** A forward battery *simulation* decides how much energy is
   safe to give away tonight so your battery never drops below your morning
   target before tomorrow's PV takes over.
2. **Spend it where it lands.** A censored-aware model predicts community
   absorption per context (season × weekday-type × hour) and allocates the
   budget to the hours that absorb most.
3. **Probe to learn.** Where uptake was always fully absorbed (ceiling unseen),
   it deliberately feeds a little *more* — UCB-style exploration — to find out
   if the community will take it. That is how it improves week over week.

## How it decides (data flow)

```
 NetzNÖ CSV ─▶ ingest ─▶ SQLite ─▶ nightly train ─▶ capacity model
                                                          │
 Home Assistant ──POST state──▶  /recommend  ─────────────┤
 (SoC, live load,              (autarky simulation +      │
  Solcast hourly)               learned schedule)         ▼
            ◀──── feed_kW + budget + reasoning ◀──── recommendation
```

Home Assistant **calls** the brain (no HA credentials stored in the brain, HA
owns the orchestration). The brain is a small, dependency-free Python service.

## Architecture (hybrid)

- **`brain/`** — the Docker service: provider plugins, ingestion, SQLite store,
  the autarky simulation, the learning model, and the `/recommend` HTTP API.
  Keeping the ML out of Home Assistant means HA restarts/upgrades never disturb
  learning.
- **`custom_components/egoptimizer/`** — the HACS integration: a thin HA client
  that gathers state, calls the brain, and exposes the result as sensors you can
  automate and chart.

## Quickstart (brain, zero dependencies)

Phase 1–3 run on the Python standard library alone.

```powershell
mkdir data
copy "AT00...-Jahreseinspeisung-2026.csv" data\

python -m brain.ingest.run_import --all-in data\   # import (idempotent, dedups, drops outliers)
python -m brain.analysis.absorption                # when does the EG absorb vs spill?
python -m brain.model.train                        # fit the absorption model
python -m brain.analysis.evaluate                  # backtest: capture vs spill
python -m brain.api.server                         # serve POST /recommend on :8787
```

Or with Docker — pull the pre-built image from GitHub Container Registry:

```bash
docker pull ghcr.io/bleialf/egoptimizer:latest
docker run -d -p 8787:8787 -v "$PWD/data:/app/data" ghcr.io/bleialf/egoptimizer:latest
# or simply:  docker compose up -d
```

The image is built and published automatically by GitHub Actions on every push
to `main` and on version tags (`vX.Y.Z`) — see
[.github/workflows/docker-publish.yml](.github/workflows/docker-publish.yml).

## Home Assistant

Two ways to connect, both documented in **[docs/homeassistant.md](docs/homeassistant.md)**:

- **HACS integration** (recommended) — install this repo as a HACS custom
  repository, add the integration, point it at your brain URL and entities.
- **Manual** — a `rest_command` + automation + dashboard (no custom component).

## Roadmap

| Phase | What | Status |
|------:|------|:------:|
| 1 | Data foundation: provider plugins, NetzNÖ import, SQLite, absorption analysis | ✅ |
| 2 | Autarky reserve via forward battery **simulation** (trough-aware, target morning SoC) | ✅ |
| 3 | Censored demand model + **UCB exploration** → learned nightly schedule | ✅ |
| 4 | Close the loop: drive the Victron ESS grid setpoint, with guardrails | ⏳ |

## Adding another grid operator

1. Create `brain/providers/<operator>.py` with a `Provider` subclass implementing
   `parse()` (and optionally `fetch()` for automated pulls).
2. Register it in `brain/providers/__init__.py`.

Everything downstream works on the normalised `EnergyRecord` — nothing else
changes. See **[docs/architecture.md](docs/architecture.md)**.

## Documentation

- [docs/architecture.md](docs/architecture.md) — modules, data model, the math
- [docs/homeassistant.md](docs/homeassistant.md) — HA integration + dashboard
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, tests, adding providers

## Status & safety

EGOptimizer currently **recommends**; it does not yet control your inverter
(Phase 4). Autarky is enforced by your Victron system's own minimum-SoC floor —
the brain only ever decides how to spend energy *above* that floor. Use at your
own risk; see [LICENSE](LICENSE).

## License

MIT — see [LICENSE](LICENSE).
