"""Import every module in the HACS integration against a real Home Assistant.

This catches bad imports (wrong module paths, removed/renamed HA helpers,
invalid selector configs built at import time) that the unit tests and HACS
structure validation do NOT -- the failure mode that shipped a broken
`device_info` import and a missing manifest dependency.

Run locally:  pip install homeassistant && python scripts/check_integration_imports.py
"""

from __future__ import annotations

import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PKG = "custom_components.egoptimizer"
PKG_DIR = ROOT / "custom_components" / "egoptimizer"


def main() -> int:
    modules = sorted(p.stem for p in PKG_DIR.glob("*.py"))
    failed: list[tuple[str, Exception]] = []
    for stem in modules:
        name = PKG if stem == "__init__" else f"{PKG}.{stem}"
        try:
            importlib.import_module(name)
            print(f"ok   : {name}")
        except Exception as exc:  # noqa: BLE001 -- report every failure, don't stop
            print(f"FAIL : {name} -> {exc!r}")
            failed.append((name, exc))

    if failed:
        print(f"\n{len(failed)} module(s) failed to import:")
        for name, exc in failed:
            print(f"  - {name}: {exc!r}")
        return 1
    print(f"\nAll {len(modules)} integration modules imported cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
