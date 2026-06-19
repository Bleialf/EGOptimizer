"""Normalized data model shared across the brain service.

Every provider, regardless of source format, emits ``EnergyRecord`` objects.
Downstream code (storage, analysis, model, optimizer) only ever sees this
shape -- that is what makes adding new providers cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EnergyRecord:
    """One measurement interval for one meter.

    Intervals are 15 minutes for NetzNOE. Energy values are kWh *for that
    interval* (not cumulative). EG columns are ``None`` when the community
    allocation has not been settled yet (recent rows lag ~1 day).
    """

    timestamp: datetime          # interval END, naive local time (Europe/Vienna)
    meter_id: str                # metering point id, e.g. AT00200...0487200
    provider: str                # provider name, e.g. "netznoe"

    feed_in_kwh: float           # Einspeisung: total energy fed to the grid
    eg_absorbed_kwh: float | None  # Eigendeckung Teilnehmer: consumed by EG members
    eg_surplus_kwh: float | None   # Gemeinschaftsueberschuss: spilled past the EG

    quality: str | None = None     # feed-in quality flag (L1 measured / L2 estimated)
    eg_quality: str | None = None  # EG allocation quality flag

    @property
    def eg_settled(self) -> bool:
        """True once the community allocation for this interval is known."""
        return self.eg_absorbed_kwh is not None

    @property
    def fully_absorbed(self) -> bool:
        """True when the EG took essentially everything we fed in.

        This is the *censoring* signal: when surplus is ~0 but we fed in a
        positive amount, the community's true capacity is >= feed_in_kwh --
        we only know a lower bound, never the ceiling. This is exactly why
        the model needs censored-aware learning + exploration.
        """
        if not self.eg_settled or self.feed_in_kwh <= 0:
            return False
        return (self.eg_surplus_kwh or 0.0) < 1e-6
