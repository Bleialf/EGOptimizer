"""Reusable import logic, shared by the CLI and the HTTP upload endpoint.

Lets you import a CSV by path *or* by uploaded bytes -- so you never have to
drop files into a folder on the host; you can POST them to /import instead.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path

from brain.ingest.clean import filter_outliers
from brain.providers import get_provider
from brain.records import EnergyRecord
from brain.storage import Store


def import_records(
    records: Iterable[EnergyRecord], db: Path | str, max_interval_kwh: float
) -> dict:
    """Clean an in-memory record stream and upsert it; return a summary.

    Used by automated pulls (``/fetch``): a provider's ``fetch_records`` yields
    straight into here, no file roundtrip. Idempotent via the store's upsert.
    """
    kept, dropped = filter_outliers(records, max_interval_kwh)
    with Store(db) as store:
        written = store.upsert_many(kept)
        summary = store.summary()
    return {"imported": written, "dropped": len(dropped), "store": summary}


def import_path(
    path: Path | str, provider_name: str, db: Path | str, max_interval_kwh: float
) -> dict:
    """Parse one export file, clean it, upsert into the store; return a summary."""
    provider = get_provider(provider_name)
    kept, dropped = filter_outliers(provider.parse(Path(path)), max_interval_kwh)
    with Store(db) as store:
        written = store.upsert_many(kept)
        summary = store.summary()
    return {"imported": written, "dropped": len(dropped), "store": summary}


def import_bytes(
    data: bytes,
    filename: str,
    provider_name: str,
    db: Path | str,
    max_interval_kwh: float,
) -> dict:
    """Import an uploaded CSV. ``filename`` matters -- the metering-point id is
    parsed from it (NetzNÖ), so pass the original export name."""
    tmp = Path(tempfile.mkdtemp())
    f = tmp / (filename or "upload.csv")
    f.write_bytes(data)
    try:
        return import_path(f, provider_name, db, max_interval_kwh)
    finally:
        try:
            f.unlink()
            tmp.rmdir()
        except OSError:
            pass
