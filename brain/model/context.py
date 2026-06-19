"""Context bucketing for the demand model.

We learn community absorption capacity per *context*, because the data shows it
swings hugely with season (winter ~100% absorbed, summer saturated), weekday
type (Fridays absorbed far more than Sundays), and hour of day.

Bucket = (season, daytype, hour). Coarse enough that each bucket gets real data
over a year, fine enough to capture the patterns that matter.
"""

from __future__ import annotations

from datetime import datetime

_SEASON = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
}


def season(month: int) -> str:
    return _SEASON[month]


def daytype(weekday: int) -> str:
    # weekday(): Mon=0 .. Sun=6
    return "weekend" if weekday >= 5 else "weekday"


def bucket_key(ts: datetime) -> str:
    return f"{season(ts.month)}|{daytype(ts.weekday())}|{ts.hour:02d}"
