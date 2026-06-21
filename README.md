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

## How the learning works (the reinforcement-learning bit)

EGOptimizer is a **censored-aware contextual bandit** — the right size of RL for
~one decision per night. There's no neural net and no gradient training; the
"policy" is a per-context table the brain refits nightly.

**Context.** Every hour is bucketed as `season | weekday-type | hour` (e.g.
`summer | weekday | 22`). Absorption swings hugely across these — winter nights
take almost everything, summer middays are saturated — so each bucket is learned
separately.

**The censored signal (the crux).** The grid-operator data only tells you how
much the community absorbed *up to what you fed in*. Two cases per bucket:
- **Fully absorbed** (no surplus) → you only learn the ceiling is *at least*
  what you fed. The true ceiling is **unseen / censored**.
- **Surplus left over** → the community took what it could and rejected the
  rest, so you **saw the ceiling**.

**Explore vs exploit (UCB-flavoured).**
- Censored or too-few recent observations → **explore**: offer a bit *above* the
  best seen (`known_max × (1 + aggressiveness)`) to discover the real ceiling.
- Ceiling seen and enough recent data → **exploit**: aim right at it.
- `locked` mode stops probing entirely and just feeds the learned typical uptake.

**Reward & adaptation (v0.7).** Each night's outcome (absorbed vs. surplus)
updates the bucket. Observations are **recency-weighted with a half-life** (default
45 days): recent nights count more, and a bucket's ceiling **decays toward recent
reality if it isn't reconfirmed** — so the model adapts **down** as well as up
(e.g. if the community starts taking less), and a context that's gone quiet loses
confidence and gets **re-explored**. Set the half-life to 0 for a non-forgetting
all-time ceiling.

**Autarky is not part of the reward** — it's a hard constraint applied *after*
the bandit proposes capacities: a forward battery simulation guarantees the
planned feed never drops your SoC below the morning target before PV takes over
(see the planner). So the learner optimises EG uptake; the simulation keeps you
safe.

## Architecture (hybrid)

- **`brain/`** — the Docker service: provider plugins, ingestion, SQLite store,
  the autarky simulation, the learning model, and the `/recommend` HTTP API.
  Keeping the ML out of Home Assistant means HA restarts/upgrades never disturb
  learning.
- **`custom_components/egoptimizer/`** — the HACS integration: a thin HA client
  that gathers state, calls the brain, and exposes the result as sensors you can
  automate and chart.

## Installation

Two parts: the **brain** (a small service, runs anywhere) and the **Home
Assistant integration** that talks to it.

### 1. Run the brain (Docker / Portainer)

No host folders, no CSVs on disk — state lives in a Docker-managed named volume:

```bash
docker volume create egoptimizer_data
docker run -d --name egoptimizer -p 8787:8787 \
  -v egoptimizer_data:/app/data --restart unless-stopped \
  ghcr.io/bleialf/egoptimizer:latest
```

In **Portainer**: Stacks → Add stack → paste [docker-compose.yml](docker-compose.yml) → deploy.

Check it: `curl http://localhost:8787/health` → `{"status": "ok"}`.

**Upload your data** (instead of placing files in a folder) — and train in one call:

```bash
curl -X POST "http://localhost:8787/import?filename=$(basename EXPORT.csv)&train=1" \
     --data-binary @EXPORT.csv
```

…or from Home Assistant call the **`egoptimizer.import_csv`** service (below).
Re-upload new exports any time; imports dedup and retrain.

### 2. Install the Home Assistant integration (HACS)

1. **HACS → ⋮ → Custom repositories** → add `https://github.com/Bleialf/EGOptimizer`,
   category **Integration**.
2. Search **EGOptimizer** in HACS, **Download**, then **restart** Home Assistant.
3. **Settings → Devices & Services → Add Integration → EGOptimizer**.
4. Enter your **brain URL** (e.g. `http://192.168.x.x:8787`), battery capacity,
   and pick your **SoC**, **house-load**, and **Solcast** entities. Every field
   has an inline explanation.
5. On the integration, click **Configure** to:
   - **Upload data** — drag in your CSV export(s) right in the UI (no folders, no
     Developer Tools); the model retrains automatically.
   - **Settings** — change connection, battery, entities, retention.
   - **Delete old data** — purge intervals older than your retention period.

That's it — you'll get the `sensor.egoptimizer_feed_setpoint` value, sliders for
target-SoC and exploration, an explore/locked switch, and the dashboard in
[docs/homeassistant.md](docs/homeassistant.md). When to feed is decided by the
learned absorption model + battery simulation — there's no time window to set.

> No HACS / prefer YAML? The same result via `rest_command` + automation is in
> [docs/homeassistant.md](docs/homeassistant.md).

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

Or with Docker (named volume, no host folders) — see [Installation](#installation)
above. The image is built and published automatically by GitHub Actions on every
push to `main` and on version tags (`vX.Y.Z`) — see
[.github/workflows/docker-publish.yml](.github/workflows/docker-publish.yml).

Endpoints: `POST /recommend`, `POST /import` (upload a CSV), `POST /train`,
`GET /health`, `GET /decisions`.

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
| 3.5 | **Automated daily data pull** from the operator's portal (no manual CSV exports) | ✅ |
| 4 | Close the loop: drive the Victron ESS grid setpoint, with guardrails | ⏳ |

## Automated data updates (no more manual CSV exports)

Once your history is imported, the brain can keep itself up to date by pulling
straight from your grid operator's smart-meter portal:

- Put your portal login in the HA integration (**Options → Settings → Grid
  portal username/password**) and pick a daily fetch hour.
- Each day the brain logs in, downloads recent days, **de-duplicates** (re-pulling
  the unsettled tail so the energy-community split is upgraded as it lands ~1–2
  days late), and retrains.
- Trigger it on demand with the **"Fetch grid data now"** button or the
  `egoptimizer.fetch` service; the **"Last grid fetch"** sensor confirms it ran,
  and the **EG-absorption sensors** show how much the community is taking.

Direct API: `POST /fetch {"provider","credentials","since?","until?","train?"}`
and `GET /stats`. Credentials are used per-call and never stored by the brain
(or kept on the brain host via `NETZNOE_USER` / `NETZNOE_PWD` env vars instead).

## Adding another grid operator

1. Create `brain/providers/<operator>.py` with a `Provider` subclass implementing
   `parse()` for manual exports — and, if the operator has an API, `fetch_records()`
   + `credential_fields()` for unattended daily pulls (one file, both paths).
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
