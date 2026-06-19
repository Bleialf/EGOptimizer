"""Provider registry.

Adding a new grid operator = implement a Provider subclass in this package
and add one line to ``_PROVIDERS`` below. Nothing else in the pipeline changes.
"""

from __future__ import annotations

from brain.providers.base import Provider
from brain.providers.netznoe import NetzNoeProvider

_PROVIDERS: dict[str, type[Provider]] = {
    NetzNoeProvider.name: NetzNoeProvider,
}


def get_provider(name: str) -> Provider:
    try:
        return _PROVIDERS[name]()
    except KeyError:
        known = ", ".join(sorted(_PROVIDERS)) or "(none)"
        raise SystemExit(f"Unknown provider {name!r}. Known providers: {known}")


def available() -> list[str]:
    return sorted(_PROVIDERS)
