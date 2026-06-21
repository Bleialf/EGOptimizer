"""Provider plugin interface.

A provider knows how to turn one data source (a grid operator's export, an
API, ...) into normalized ``EnergyRecord`` objects. To add a new operator,
implement ``parse`` (and optionally ``fetch`` for automated pulls) in a new
module under ``providers/`` and register it in ``providers/__init__.py``.

The rest of the pipeline never imports a concrete provider -- it asks the
registry for one by name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from brain.records import EnergyRecord


class Provider(ABC):
    """Base class for all data providers.

    A provider can ingest data two ways, and implements whichever its operator
    supports:

      * ``parse(file)`` -- normalize a manually exported file (every provider).
      * ``fetch_records(...)`` -- pull straight from the operator's API/portal
        for unattended daily updates (providers that have a reachable API).

    To add a new operator: subclass this, implement ``parse`` (and, if the
    operator has an API, ``fetch_records`` + ``credential_fields``) in a module
    under ``providers/``, and register it in ``providers/__init__.py``. Nothing
    else in the pipeline changes.
    """

    #: short, stable identifier stored on every record (e.g. "netznoe")
    name: str = "base"

    @abstractmethod
    def parse(self, source: Path) -> Iterator[EnergyRecord]:
        """Yield normalized records from a local source file.

        Implementations must be tolerant of partial rows (recent intervals
        where the EG allocation is not settled yet) and emit ``None`` for the
        unsettled EG columns rather than skipping the row -- the feed-in value
        is still useful.
        """
        raise NotImplementedError

    @classmethod
    def credential_fields(cls) -> list[str]:
        """Names of the secrets ``fetch_records`` needs (e.g. ["user", "pwd"]).

        Returned to the integration so it can render the right login fields
        generically. Empty == this provider has no automated fetch.
        """
        return []

    def fetch_records(
        self,
        *,
        credentials: dict[str, str],
        since: date | None = None,
        until: date | None = None,
    ) -> Iterator[EnergyRecord]:
        """Pull normalized records directly from the operator's API.

        ``credentials`` carries the fields named by ``credential_fields``.
        ``since``/``until`` bound the pull (inclusive, by calendar day in the
        operator's local time); ``None`` lets the provider choose a sensible
        default window. Re-pulling overlapping days is expected and safe -- the
        store upserts by (provider, meter_id, ts) and upgrades unsettled rows
        once the EG allocation lands (a day or two later).

        Optional. Providers that only support manual export leave this
        unimplemented.
        """
        raise NotImplementedError(
            f"Provider {self.name!r} has no automated fetch; import a file manually."
        )

    def fetch(self, dest_dir: Path) -> Path:
        """Download the latest export *file* to ``dest_dir`` and return its path.

        Optional, for operators whose only programmatic access is a file
        download (then paired with ``parse``). Providers with a structured API
        implement ``fetch_records`` instead.
        """
        raise NotImplementedError(
            f"Provider {self.name!r} has no automated file fetch; import a file manually."
        )
