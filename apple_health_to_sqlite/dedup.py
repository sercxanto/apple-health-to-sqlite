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
* **Only cumulative types are touched.** Averaging two sources' heart rates
  does not inflate anything, so discrete metrics keep `deduplicated_value` NULL.

`total` stays the honest sum: for two apps logging genuinely different meals,
the sum is the truth and this reconstruction is not. `deduplicated_value` is
"what Health would show", offered beside it, never over it.
"""

from __future__ import annotations

import sqlite3

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
    """The day's value with each instant credited to one source only.

    Sources are ranked by how much of the day they cover; each source's
    *winning region* is its own coverage minus every higher-ranked source's,
    so the timeline is partitioned with no instant counted twice. A sample then
    contributes its value scaled by the fraction of its span that lies in its
    source's winning region — the whole value when it stands alone, none of it
    when a better source covers all of it, a slice when they overlap.

    A sample whose end date could not be read has no span to compare, so nothing
    shows it overlaps anything, and it counts in full.
    """
    placed = [
        (start, end, value, source)
        for start, end, value, source in samples
        if end is not None
    ]
    unplaced = sum(value for _start, end, value, _source in samples if end is None)

    by_source: dict[str, list[Interval]] = {}
    for start, end, _value, source in placed:
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

    total = float(unplaced)
    for start, end, value, source in placed:
        span = end - start
        if span <= 0:
            # An instantaneous cumulative sample (rare): all or nothing, on
            # whether its instant belongs to this source.
            if _overlap((start, start + 1e-9), winning[source]) > 0:
                total += value
            continue
        total += value * _overlap((start, end), winning[source]) / span
    return total


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
