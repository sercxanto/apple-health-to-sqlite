"""Reconstruct one day's number from several sources without double-counting.

When an iPhone and an Apple Watch both record steps, `records` holds both sets
and a plain `sum()` adds them together — 19638 where the Health app shows
10110. Health does not sum; it splits the timeline, and where two sources
overlap it keeps only the one it trusts more. This module does the same, per
metric and calendar day, and writes the result into
`daily_metrics.deduplicated_value` beside the untouched `total`.

The rules, all of them heuristics the export cannot state (see AGENTS.md on the
catalogue boundary — this is the same kind of interpretation, kept off the
import path and never replacing a raw number):

* **Priority is per day.** The source that covers the most of a given day wins
  that day's overlaps; ties break on the source name so the answer is stable.
  A different source can win the next day, which is the point — people change
  devices, and a global ranking would get every day after the switch wrong.
* **Overlaps are prorated, not dropped whole.** A sample that is the winning
  source for half its span contributes half its value. This is what makes a
  complementary day — Watch all morning, iPhone all afternoon, little overlap —
  come back as the near-sum it should be, while a fully-overlapped day collapses
  to a single source.
* **A sample with no span counts in full.** A glass of water logged at one instant
  occupies no time, so nothing can overlap it. Two apps logging the same glass
  therefore both count: the export cannot tell that from two different glasses.
* **Only cumulative types are touched.** Averaging two sources' heart rates
  does not inflate anything, so discrete metrics keep `deduplicated_value` NULL.

`total` stays the honest sum: for two apps logging genuinely different meals,
the sum is the truth and this reconstruction is not. `deduplicated_value` is
"what Health would show", offered beside it, never over it.

`nightly_sleep` applies the same rules to the minutes of a night, with the sources
ranked per night. `sleep_segments` keeps every source's segments as recorded.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from itertools import groupby

# One sample as the sweep needs it: a half-open span [start, end) in epoch
# seconds carrying a cumulative value, tagged with the source that recorded it.
# The end is None when the export's end date could not be read.
Sample = tuple[float, float | None, float, str]
Interval = tuple[float, float]


def _union(intervals: list[Interval]) -> list[Interval]:
    """Merge a list of spans into disjoint, sorted spans."""
    spans = sorted((s, e) for s, e in intervals if e > s)
    merged: list[Interval] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _covered(intervals: list[Interval]) -> float:
    """Total length of a set of spans, counting overlap once."""
    return sum(e - s for s, e in _union(intervals))


def _overlap(span: Interval, region: list[Interval]) -> float:
    """How much of `span` falls inside the disjoint, sorted `region`."""
    start, end = span
    total = 0.0
    for rs, re in region:
        lo, hi = max(start, rs), min(end, re)
        if hi > lo:
            total += hi - lo
    return total


def deduplicated_value(samples: list[Sample]) -> float:
    """The day's value with each instant credited to one source only."""
    return sum(credited_values(samples), start=0.0)


def credited_values(samples: list[Sample]) -> list[float]:
    """What each sample contributes once overlaps are settled, in the same order.

    Sources are ranked by how much of the day they cover; each source's
    *winning region* is its own coverage minus every higher-ranked source's,
    so the timeline is partitioned with no instant counted twice. A sample then
    contributes its value scaled by the fraction of its span that lies in its
    source's winning region — the whole value when it stands alone, none of it
    when a better source covers all of it, a slice when they overlap.

    A sample with no span — logged at one instant, or with an end date that could
    not be read or lies before its start — has nothing to compare, so nothing
    shows it overlaps anything, and it counts in full.
    """
    by_source: dict[str, list[Interval]] = {}
    for start, end, _value, source in samples:
        if end is not None and end > start:
            by_source.setdefault(source, []).append((start, end))

    coverage = {source: _union(spans) for source, spans in by_source.items()}
    # Best-covered source first; the name breaks ties so the result never
    # depends on row order.
    ranked = sorted(coverage, key=lambda s: (-_covered(coverage[s]), s))

    winning: dict[str, list[Interval]] = {}
    claimed: list[Interval] = []
    for source in ranked:
        region = coverage[source]
        winning[source] = _subtract(region, claimed)
        claimed = _union(claimed + region)

    return [
        value * _overlap((start, end), winning[source]) / (end - start)
        if end is not None and end > start
        else value
        for start, end, value, source in samples
    ]


def _subtract(region: list[Interval], claimed: list[Interval]) -> list[Interval]:
    """`region` minus `claimed`, both turned into disjoint sorted spans first."""
    region = _union(region)
    claimed = _union(claimed)
    result: list[Interval] = []
    for start, end in region:
        cursor = start
        for cs, ce in claimed:
            if ce <= cursor:
                continue
            if cs >= end:
                break
            if cs > cursor:
                result.append((cursor, cs))
            cursor = max(cursor, ce)
        if cursor < end:
            result.append((cursor, end))
    return result


# Group by type_short/local_date/unit — the same key `daily_metrics` rows carry
# — and only where more than one source contributed, since a single-source day
# has nothing to reconcile. `type_short`+`local_date` is the indexed prefix, so
# each day's samples come back off `idx_records_short_date`.
_MULTI_SOURCE_DAYS = """
SELECT r.type_short, r.local_date, r.unit
FROM records r
JOIN quantity_types q ON q.type = r.type
WHERE q.aggregation = 'cumulative'
  AND r.value IS NOT NULL AND r.local_date IS NOT NULL
GROUP BY r.type_short, r.local_date, r.unit
HAVING count(DISTINCT r.source_name) > 1
"""

_DAYS_SAMPLES = """
SELECT start_ts, end_ts, value, source_name
FROM records
WHERE type_short = ? AND local_date = ? AND unit = ? AND value IS NOT NULL
"""


def build(connection: sqlite3.Connection) -> int:
    """Fill `daily_metrics.deduplicated_value` for every multi-source day.

    Returns the number of days reconciled. Left NULL everywhere else: a
    single-source day already reads right, and a discrete or uncatalogued type
    has no sum to deduplicate.
    """
    days = connection.execute(_MULTI_SOURCE_DAYS).fetchall()
    updates = []
    for type_short, local_date, unit in days:
        samples = connection.execute(
            _DAYS_SAMPLES, (type_short, local_date, unit)
        ).fetchall()
        value = deduplicated_value([tuple(row) for row in samples])
        updates.append((round(value, 3), type_short, local_date, unit))
    connection.executemany(
        "UPDATE daily_metrics SET deduplicated_value = ? "
        "WHERE type_short = ? AND local_date = ? AND unit = ?",
        updates,
    )
    return len(updates)


# A table rather than a view, because settling overlaps takes more than SQL. The
# same columns and the same rounding as the sum over `sleep_segments` it replaces,
# which it equals wherever one source recorded the night.
_NIGHTLY_SLEEP = """
CREATE TABLE nightly_sleep (
    night_date TEXT PRIMARY KEY,
    hours_asleep REAL, hours_in_bed REAL,
    min_deep REAL, min_rem REAL, min_awake REAL
)
"""

_INSERT_NIGHT = """
INSERT INTO nightly_sleep VALUES (
    ?, round(? / 60.0, 2), round(? / 60.0, 2), round(?, 0), round(?, 0), round(?, 0)
)
"""

# Segments without a span in minutes have an unreadable date, and say nothing about
# how long anyone slept.
_NIGHT_SEGMENTS = """
SELECT s.night_date, s.stage, r.start_ts, r.end_ts, s.minutes, s.source_name
FROM sleep_segments s
JOIN records r ON r.id = s.id
WHERE s.night_date IS NOT NULL AND s.stage IS NOT NULL AND s.minutes IS NOT NULL
ORDER BY s.night_date
"""


def _sleep_question(stage: str) -> str:
    """Which question a stage answers; overlaps only count within one question.

    Time in bed legitimately overlaps time asleep. An iPhone often records only
    the former, and more of it than a Watch records of the latter, so ranking the
    sources across everything would discard the Watch's sleep.
    """
    return "asleep" if stage.startswith("Asleep") else stage


def _night_minutes(segments: Iterable[tuple]) -> dict[str, float]:
    """Minutes per stage for one night, each moment credited to one source."""
    questions: dict[str, list[tuple]] = {}
    for segment in segments:
        questions.setdefault(_sleep_question(segment[1]), []).append(segment)

    minutes: dict[str, float] = {}
    for members in questions.values():
        credited = credited_values(
            [(start, end, value, source) for _, _, start, end, value, source in members]
        )
        for (_, stage, *_rest), value in zip(members, credited, strict=True):
            minutes[stage] = minutes.get(stage, 0.0) + value
    return minutes


def build_nightly_sleep(connection: sqlite3.Connection) -> int:
    """(Re)build `nightly_sleep`, returning the number of nights.

    A stage that won no time is NULL, the same as one nobody recorded.
    """
    # Up to 0.1.0 this was a view, and DROP refuses the wrong kind of object.
    existing = connection.execute(
        "SELECT type FROM sqlite_master WHERE name = 'nightly_sleep'"
    ).fetchone()
    if existing:
        connection.execute(f"DROP {existing[0]} nightly_sleep")
    connection.execute(_NIGHTLY_SLEEP)

    nights = []
    rows = connection.execute(_NIGHT_SEGMENTS)
    for night_date, segments in groupby(rows, key=lambda row: row[0]):
        minutes = _night_minutes(segments)
        asleep = sum(
            value for stage, value in minutes.items() if stage.startswith("Asleep")
        )
        nights.append(
            (
                night_date,
                asleep or None,
                minutes.get("InBed") or None,
                minutes.get("AsleepDeep") or None,
                minutes.get("AsleepREM") or None,
                minutes.get("Awake") or None,
            )
        )
    connection.executemany(_INSERT_NIGHT, nights)
    return len(nights)
