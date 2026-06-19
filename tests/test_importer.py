"""Import via bytes (the upload path) + by file path."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.ingest.importer import import_bytes, import_path

HEADER = (
    "Messzeitpunkt;Einspeisung (kWh);Qualität;Gemeinschaftsüberschuss (kWh);"
    "Qualität EG;Eigendeckung Teilnehmer (kWh);Eigendeckung Teilnehmer (kWh) X;"
)
CSV = (
    "﻿" + HEADER + "\n"
    "24.07.2025 00:00;7,556000;L1;;;;;\n"            # meter-init outlier -> dropped
    "24.07.2025 12:45;0,384000;L1;0,009653;L2;0,374347;0,374347;\n"
    "24.07.2025 13:00;0,629000;L1;0,019535;L2;0,609465;0,609465;\n"
)
NAME = "AT0020000000000000000000100487200-Jahreseinspeisung-2025.csv"


class TestImporter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t.sqlite"

    def tearDown(self):
        self._tmp.cleanup()

    def test_import_bytes_stores_and_drops_outlier(self):
        r = import_bytes(CSV.encode("utf-8"), NAME, "netznoe", self.db, 5.0)
        self.assertEqual(r["imported"], 2)   # the 7.556 row is dropped
        self.assertEqual(r["dropped"], 1)
        self.assertEqual(r["store"]["n"], 2)

    def test_import_bytes_parses_meter_id_from_filename(self):
        import_bytes(CSV.encode("utf-8"), NAME, "netznoe", self.db, 5.0)
        from brain.storage import Store
        store = Store(self.db)
        try:
            recs = store.fetch_all()
            self.assertTrue(all(r.meter_id == "AT0020000000000000000000100487200" for r in recs))
        finally:
            store.close()

    def test_import_path_equivalent(self):
        p = Path(self._tmp.name) / NAME
        p.write_text(CSV, encoding="utf-8")
        r = import_path(p, "netznoe", self.db, 5.0)
        self.assertEqual(r["imported"], 2)

    def test_reimport_is_idempotent(self):
        import_bytes(CSV.encode("utf-8"), NAME, "netznoe", self.db, 5.0)
        r = import_bytes(CSV.encode("utf-8"), NAME, "netznoe", self.db, 5.0)
        self.assertEqual(r["store"]["n"], 2)  # no duplicates


if __name__ == "__main__":
    unittest.main()
