"""Data cleaning for ingestion.

Provider-agnostic sanity filters applied before records hit the store. Right
now: drop physically-impossible feed-in spikes (e.g. NetzNOE's meter-init
artifact of 7.556 kWh in a single 15-min interval).
"""

from __future__ import annotations

from collections.abc import Iterable

from brain.records import EnergyRecord


def filter_outliers(
    records: Iterable[EnergyRecord], max_interval_kwh: float
) -> tuple[list[EnergyRecord], list[EnergyRecord]]:
    """Split records into (kept, dropped) by a per-interval feed-in ceiling."""
    kept, dropped = [], []
    for r in records:
        (dropped if r.feed_in_kwh > max_interval_kwh else kept).append(r)
    return kept, dropped
