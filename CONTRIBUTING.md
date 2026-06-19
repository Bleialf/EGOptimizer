# Contributing to EGOptimizer

Thanks for your interest! EGOptimizer aims to stay small, honest, and safe
(it controls real home energy). Contributions of providers, model improvements,
docs, and HA polish are very welcome.

## Dev setup

The brain (Phases 1–3) runs on the **Python standard library** — no install
needed to develop or test it.

```bash
python --version            # 3.11+
python -m unittest discover -s tests        # run the full suite
```

Optional extras (later phases / tooling) live in `requirements.txt`.

## Running locally

```bash
python -m brain.ingest.run_import --all-in data/
python -m brain.model.train
python -m brain.api.server
```

## Tests

- Every behaviour change needs a test. We use stdlib `unittest`.
- Prefer **realistic scenarios** (e.g. sunny vs cloudy morning in
  `tests/test_simulate.py`) over trivial assertions.
- Safety-critical math (autarky reserve, simulation) must stay well covered.

## Adding a grid-operator provider

1. `brain/providers/<operator>.py` — subclass `Provider`, implement `parse()`
   yielding `EnergyRecord`s; tolerate partial/unsettled rows.
2. Register it in `brain/providers/__init__.py`.
3. Add a parser test with a small real-shaped fixture.

Nothing downstream should need changes — it all works on `EnergyRecord`.

## Code style

- Match the surrounding style; keep comments about *why*, not *what*.
- No new runtime dependencies in the brain core without discussion — the
  zero-dependency property is a feature.

## Safety

Anything touching the autarky reserve, the battery simulation, or (Phase 4)
inverter control gets extra scrutiny. When in doubt, fail safe: feed less.

## Commit / PR

- Small, focused PRs. Describe the behaviour change and how you tested it.
- Make sure `python -m unittest discover -s tests` is green.
