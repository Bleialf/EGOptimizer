"""Parser tests against the real NetzNOE export quirks."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from brain.providers.netznoe import NetzNoeProvider, _num

HEADER = (
    "Messzeitpunkt;Einspeisung (kWh);Qualität;Gemeinschaftsüberschuss (kWh);"
    "Qualität EG;Eigendeckung Teilnehmer (kWh);"
    "Eigendeckung Teilnehmer (kWh) 7Energy - BEG für erneuerbaren Strom;"
)

# Real-shaped rows: BOM on header line, ';' sep, German decimals, trailing ';',
# one settled row, one fully-absorbed (surplus 0) row, one unsettled (blank EG).
SAMPLE = (
    "﻿" + HEADER + "\n"
    "01.01.2026 12:45;0,384000;L1;0,009653;L2;0,374347;0,374347;\n"
    "01.01.2026 13:15;0,464000;L1;0,000000;L2;0,464000;0,464000;\n"
    "18.06.2026 23:45;0,130000;L1;;;;;\n"
)


def _write(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


class TestNumberParsing(unittest.TestCase):
    def test_german_decimal(self):
        self.assertAlmostEqual(_num("0,374347"), 0.374347)

    def test_thousands_and_decimal(self):
        self.assertAlmostEqual(_num("1.234,5"), 1234.5)

    def test_blank_is_none(self):
        self.assertIsNone(_num(""))
        self.assertIsNone(_num("   "))

    def test_zero(self):
        self.assertEqual(_num("0,000000"), 0.0)


class TestNetzNoeParse(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _parse(self, text: str, name: str):
        path = _write(self.tmp, name, text)
        return list(NetzNoeProvider().parse(path))

    def test_row_count_and_meter_id(self):
        name = "AT0020000000000000000000100487200-Jahreseinspeisung-2026.csv"
        recs = self._parse(SAMPLE, name)
        self.assertEqual(len(recs), 3)
        self.assertEqual(recs[0].meter_id, "AT0020000000000000000000100487200")
        self.assertEqual(recs[0].provider, "netznoe")

    def test_timestamp_and_bom(self):
        recs = self._parse(SAMPLE, "AT00200000000000000000001x.csv")
        self.assertEqual(recs[0].timestamp, datetime(2026, 1, 1, 12, 45))

    def test_energy_balance(self):
        # Einspeisung == absorbed + surplus for a settled row.
        r = self._parse(SAMPLE, "AT00200000000000000000001x.csv")[0]
        self.assertAlmostEqual(
            r.feed_in_kwh, (r.eg_absorbed_kwh or 0) + (r.eg_surplus_kwh or 0), places=5
        )

    def test_fully_absorbed_flag(self):
        # second row: surplus 0, feed > 0 -> censored / fully absorbed
        r = self._parse(SAMPLE, "AT00200000000000000000001x.csv")[1]
        self.assertTrue(r.fully_absorbed)
        self.assertTrue(r.eg_settled)

    def test_unsettled_row_keeps_feed_drops_eg(self):
        r = self._parse(SAMPLE, "AT00200000000000000000001x.csv")[2]
        self.assertAlmostEqual(r.feed_in_kwh, 0.130)
        self.assertIsNone(r.eg_absorbed_kwh)
        self.assertIsNone(r.eg_surplus_kwh)
        self.assertFalse(r.eg_settled)
        self.assertFalse(r.fully_absorbed)

    def test_meter_id_unknown_when_absent(self):
        recs = self._parse(SAMPLE, "weird-name.csv")
        self.assertEqual(recs[0].meter_id, "unknown")

    def test_bad_header_raises(self):
        with self.assertRaises(ValueError):
            self._parse("not;a;valid;header\n1;2;3;4\n", "AT00200000000000000000001x.csv")

    def test_blank_lines_skipped(self):
        recs = self._parse(SAMPLE.replace("\n", "\n\n"), "AT00200000000000000000001x.csv")
        self.assertEqual(len(recs), 3)


if __name__ == "__main__":
    unittest.main()
