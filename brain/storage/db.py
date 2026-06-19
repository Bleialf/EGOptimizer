"""SQLite storage for normalized energy records.

One table keyed by (provider, meter_id, timestamp). Re-importing an export is
idempotent: an interval that was previously unsettled (EG columns NULL) gets
upgraded in place once the allocation arrives in a later export.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from brain.records import EnergyRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS energy_records (
    provider        TEXT    NOT NULL,
    meter_id        TEXT    NOT NULL,
    ts              TEXT    NOT NULL,          -- ISO 8601, interval end
    feed_in_kwh     REAL    NOT NULL,
    eg_absorbed_kwh REAL,                      -- NULL until EG allocation settled
    eg_surplus_kwh  REAL,
    quality         TEXT,
    eg_quality      TEXT,
    PRIMARY KEY (provider, meter_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_records_ts ON energy_records (ts);

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at  TEXT    NOT NULL,   -- when the recommendation was made (from request)
    request     TEXT    NOT NULL,   -- raw request JSON (state HA sent us)
    response    TEXT    NOT NULL,   -- raw response JSON (what we recommended)
    feed_kw     REAL,               -- recommended grid feed-in for the interval
    eg_budget_kwh REAL,             -- tonight's EG energy budget
    explore     INTEGER             -- 1 if this was an exploration probe
);
CREATE INDEX IF NOT EXISTS idx_decisions_at ON decisions (decided_at);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_many(self, records: Iterable[EnergyRecord]) -> int:
        """Insert or update records. Returns number of rows written."""
        rows = [
            (
                r.provider,
                r.meter_id,
                r.timestamp.isoformat(),
                r.feed_in_kwh,
                r.eg_absorbed_kwh,
                r.eg_surplus_kwh,
                r.quality,
                r.eg_quality,
            )
            for r in records
        ]
        cur = self.conn.executemany(
            """
            INSERT INTO energy_records
                (provider, meter_id, ts, feed_in_kwh, eg_absorbed_kwh,
                 eg_surplus_kwh, quality, eg_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, meter_id, ts) DO UPDATE SET
                feed_in_kwh     = excluded.feed_in_kwh,
                eg_absorbed_kwh = COALESCE(excluded.eg_absorbed_kwh, energy_records.eg_absorbed_kwh),
                eg_surplus_kwh  = COALESCE(excluded.eg_surplus_kwh,  energy_records.eg_surplus_kwh),
                quality         = excluded.quality,
                eg_quality      = COALESCE(excluded.eg_quality, energy_records.eg_quality)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def log_decision(
        self,
        decided_at: str,
        request: str,
        response: str,
        feed_kw: float | None,
        eg_budget_kwh: float | None,
        explore: bool,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO decisions
                (decided_at, request, response, feed_kw, eg_budget_kwh, explore)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (decided_at, request, response, feed_kw, eg_budget_kwh, int(explore)),
        )
        self.conn.commit()
        return cur.lastrowid

    def fetch_all(
        self, provider: str | None = None, meter_id: str | None = None
    ) -> list[EnergyRecord]:
        sql = "SELECT * FROM energy_records"
        clauses, params = [], []
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if meter_id:
            clauses.append("meter_id = ?")
            params.append(meter_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts"
        return [self._row_to_record(r) for r in self.conn.execute(sql, params)]

    def delete_before(self, cutoff_iso: str) -> int:
        """Delete energy records with ts < cutoff (ISO). Returns rows removed."""
        cur = self.conn.execute("DELETE FROM energy_records WHERE ts < ?", (cutoff_iso,))
        self.conn.commit()
        return cur.rowcount

    def summary(self) -> dict:
        row = self.conn.execute(
            """
            SELECT COUNT(*)                                   AS n,
                   MIN(ts)                                    AS first_ts,
                   MAX(ts)                                    AS last_ts,
                   SUM(eg_absorbed_kwh IS NOT NULL)           AS settled,
                   MAX(CASE WHEN eg_absorbed_kwh IS NOT NULL THEN ts END) AS last_settled_ts
            FROM energy_records
            """
        ).fetchone()
        return dict(row) if row else {}

    @staticmethod
    def _row_to_record(r: sqlite3.Row) -> EnergyRecord:
        return EnergyRecord(
            timestamp=datetime.fromisoformat(r["ts"]),
            meter_id=r["meter_id"],
            provider=r["provider"],
            feed_in_kwh=r["feed_in_kwh"],
            eg_absorbed_kwh=r["eg_absorbed_kwh"],
            eg_surplus_kwh=r["eg_surplus_kwh"],
            quality=r["quality"],
            eg_quality=r["eg_quality"],
        )

    def close(self) -> None:
        self.conn.close()
