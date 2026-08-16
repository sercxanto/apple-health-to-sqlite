"""Derived tables and views, rebuilt after every import.

Raw `records` is the wrong table to explore interactively: faceting five years
of heart-rate samples in Datasette takes hundreds of milliseconds per click.
`daily_metrics` pre-aggregates per metric and calendar day, which answers most
questions in single-digit milliseconds.

The views exist because a few questions need SQL that is fiddly enough to be
worth writing once: what a category record's value actually says, which sleep
stage covered which minutes, how much sleep a given *night* held (a night spans
two calendar dates), and what a workout looked like with its statistics folded
back in.
"""

from __future__ import annotations

import sqlite3

from . import catalog

VIEWS = """
-- Category records — sleep stages, symptoms, mindful sessions — keep their
-- value as the name the export uses, `HKCategoryValueSeverityModerate`. The
-- join turns that into `Moderate`, and adds where it sits on its scale where
-- there is one. Both joins are outer and the value falls back to the raw
-- name: a type Apple added after this catalogue was generated still shows up
-- here, just without the translation.
DROP VIEW IF EXISTS category_samples;
CREATE VIEW category_samples AS
SELECT r.id, r.type_short, r.type, t.family, t.topic,
       r.local_date, r.start_local, r.end_local,
       round((r.end_ts - r.start_ts) / 60.0, 1) AS minutes,
       coalesce(v.value_short, r.value_text) AS value_short,
       v.ordinal, r.value_text, r.source_name
FROM records r
LEFT JOIN category_types t ON t.type = r.type
LEFT JOIN category_values v ON v.value = r.value_text
WHERE r.type LIKE 'HKCategoryTypeIdentifier%';

-- The symptom diary: one row per entry the user tapped into Health. `severity`
-- is a name (Mild, Moderate, Severe); `ordinal` is the same thing as a number,
-- for sorting and plotting, and is NULL for the few symptom types whose values
-- are not a scale. Entries can span days, so `minutes` is often large.
DROP VIEW IF EXISTS symptoms;
CREATE VIEW symptoms AS
SELECT local_date, start_local, end_local, topic,
       type_short AS symptom, value_short AS severity, ordinal,
       minutes, source_name
FROM category_samples
WHERE family = 'Symptoms';

DROP VIEW IF EXISTS sleep_segments;
CREATE VIEW sleep_segments AS
SELECT id, local_date, start_local, end_local,
       value_short AS stage, minutes,
       date(datetime(start_local, '-12 hours')) AS night_date,
       source_name
FROM category_samples
WHERE type = 'HKCategoryTypeIdentifierSleepAnalysis';

DROP VIEW IF EXISTS nightly_sleep;
CREATE VIEW nightly_sleep AS
SELECT night_date,
       round(sum(CASE WHEN stage LIKE 'Asleep%' THEN minutes END) / 60.0, 2)
           AS hours_asleep,
       round(sum(CASE WHEN stage = 'InBed' THEN minutes END) / 60.0, 2)
           AS hours_in_bed,
       round(sum(CASE WHEN stage = 'AsleepDeep' THEN minutes END), 0) AS min_deep,
       round(sum(CASE WHEN stage = 'AsleepREM' THEN minutes END), 0) AS min_rem,
       round(sum(CASE WHEN stage = 'Awake' THEN minutes END), 0) AS min_awake
FROM sleep_segments
GROUP BY night_date;

DROP VIEW IF EXISTS workout_summary;
CREATE VIEW workout_summary AS
SELECT w.id, w.local_date, w.start_local, w.activity_short AS activity,
       round(w.duration_s / 60.0, 1) AS minutes,
       w.total_distance AS distance, w.total_distance_unit AS distance_unit,
       round(w.total_distance / (w.duration_s / 3600.0), 2) AS speed,
       w.total_energy_burned AS kcal,
       (SELECT average FROM workout_statistics s
         WHERE s.workout_id = w.id AND s.activity_uuid = ''
           AND s.type = 'HKQuantityTypeIdentifierHeartRate') AS avg_hr,
       (SELECT maximum FROM workout_statistics s
         WHERE s.workout_id = w.id AND s.activity_uuid = ''
           AND s.type = 'HKQuantityTypeIdentifierHeartRate') AS max_hr,
       w.n_points, w.source_name, w.device_name
FROM workouts w;
"""

# "What is actually in my export?" is the first question anyone asks, and
# answering it from `records` means a full scan plus a grouping temp b-tree:
# four seconds on a five-year database, which Datasette kills at its one-second
# default. Precomputing it here turns that into a lookup over a few dozen rows.
METRIC_SUMMARY = """
CREATE TABLE metric_summary AS
SELECT type_short, type, unit, source_name,
       count(*)          AS samples,
       min(local_date)   AS first_seen,
       max(local_date)   AS last_seen,
       min(start_ts)     AS first_ts,
       max(start_ts)     AS last_ts
FROM records
WHERE local_date IS NOT NULL
GROUP BY type, unit, source_name
"""

DAILY_METRICS = """
CREATE TABLE daily_metrics AS
SELECT type_short, type, local_date, unit,
       count(*)   AS n,
       sum(value) AS total,
       round(avg(value), 3) AS mean,
       min(value) AS min_value,
       max(value) AS max_value
FROM records
WHERE value IS NOT NULL AND local_date IS NOT NULL
GROUP BY type, local_date, unit
"""


def build(connection: sqlite3.Connection) -> int:
    """(Re)build `daily_metrics` and the views. Returns the aggregate row count.

    Rebuilt wholesale rather than updated incrementally: it is a few seconds on
    a five-year database, and correctness after a partial re-import is worth
    more than the saved time.
    """
    # Reference data, not derived data — but it belongs to the same rebuild,
    # because the views below read it and an upgraded catalogue should reach an
    # existing database without a reimport.
    catalog.install(connection)
    connection.executescript(VIEWS)
    connection.execute("DROP TABLE IF EXISTS daily_metrics")
    connection.execute(DAILY_METRICS)
    connection.execute(
        "CREATE INDEX idx_daily_type_date ON daily_metrics (type_short, local_date)"
    )
    connection.execute("DROP TABLE IF EXISTS metric_summary")
    connection.execute(METRIC_SUMMARY)
    connection.execute(
        """
        UPDATE workouts SET n_points = (
            SELECT count(*) FROM workout_points p WHERE p.workout_id = workouts.id)
        """
    )
    connection.commit()
    return connection.execute("SELECT count(*) FROM daily_metrics").fetchone()[0]
