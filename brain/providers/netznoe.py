"""NetzNOE (Netz Niederoesterreich) provider.

Parses the *Jahreseinspeisung* CSV export from the NetzNOE smart-meter portal.

Format quirks handled here:
  * UTF-8 BOM on the first byte
  * ``;`` field separator
  * German decimal comma ("0,374347")
  * timestamps "DD.MM.YYYY HH:MM" (interval END), local time Europe/Vienna
  * trailing empty field (line ends with ``;``)
  * recent rows where the EG allocation columns are blank (not yet settled)

Header (columns we use):
  0 Messzeitpunkt
  1 Einspeisung (kWh)                         -> feed_in_kwh
  2 Qualitaet                                 -> quality
  3 Gemeinschaftsueberschuss (kWh)            -> eg_surplus_kwh
  4 Qualitaet EG                              -> eg_quality
  5 Eigendeckung Teilnehmer (kWh)             -> eg_absorbed_kwh
  6 Eigendeckung Teilnehmer (kWh) <EG name>   -> (duplicate, ignored)
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from brain.providers.base import Provider
from brain.records import EnergyRecord

# Metering-point id is embedded in the export filename, e.g.
# "AT0020000000000000000000100487200-Jahreseinspeisung-2026.csv"
_METER_RE = re.compile(r"(AT\d{30,})", re.IGNORECASE)


def _num(raw: str) -> float | None:
    """Parse a German-formatted number; blank -> None."""
    raw = raw.strip()
    if not raw:
        return None
    return float(raw.replace(".", "").replace(",", "."))


def _meter_id_from_name(path: Path) -> str:
    m = _METER_RE.search(path.name)
    return m.group(1) if m else "unknown"


class NetzNoeProvider(Provider):
    name = "netznoe"

    def parse(self, source: Path) -> Iterator[EnergyRecord]:
        meter_id = _meter_id_from_name(source)
        # utf-8-sig transparently strips the BOM.
        with source.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, delimiter=";")
            header = next(reader, None)
            if not header or not header[0].lstrip().startswith("Messzeitpunkt"):
                raise ValueError(
                    f"{source.name}: unexpected header, not a NetzNOE Einspeisung export: {header!r}"
                )
            for row in reader:
                if not row or not row[0].strip():
                    continue
                ts = datetime.strptime(row[0].strip(), "%d.%m.%Y %H:%M")
                yield EnergyRecord(
                    timestamp=ts,
                    meter_id=meter_id,
                    provider=self.name,
                    feed_in_kwh=_num(row[1]) or 0.0,
                    eg_surplus_kwh=_num(row[3]) if len(row) > 3 else None,
                    eg_absorbed_kwh=_num(row[5]) if len(row) > 5 else None,
                    quality=(row[2].strip() or None) if len(row) > 2 else None,
                    eg_quality=(row[4].strip() or None) if len(row) > 4 else None,
                )
