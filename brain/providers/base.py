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
from pathlib import Path

from brain.records import EnergyRecord


class Provider(ABC):
    """Base class for all data providers."""

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

    def fetch(self, dest_dir: Path) -> Path:
        """Download the latest export to ``dest_dir`` and return its path.

        Optional. Providers that only support manual export (NetzNOE today)
        leave this unimplemented; the ingest CLI then requires an explicit
        ``--file``. Automated fetching is a later phase.
        """
        raise NotImplementedError(
            f"Provider {self.name!r} has no automated fetch yet; import a file manually."
        )
