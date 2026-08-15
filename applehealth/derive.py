"""Derived tables and views, rebuilt after every import.

Raw `records` is the wrong table to explore interactively: faceting five years
of heart-rate samples in Datasette takes hundreds of milliseconds per click.
`daily_metrics` pre-aggregates per metric and calendar day, which answers most
questions in single-digit milliseconds.

The views exist because three questions need SQL that is fiddly enough to be
worth writing once: which sleep stage covered which minutes, how much sleep a
given *night* held (a night spans two calendar dates), and what a workout
looked like with its statistics folded back in.
"""

from __future__ import annotations

import sqlite3

VIEWS = """
DROP VIEW IF EXISTS sleep_segments;
CREATE VIEW sleep_segments AS
SELECT id, local_date, start_local, end_local,
       replace(value_text, 'HKCategoryValueSleepAnalysis', '') AS stage,
       round((end_ts - start_ts) / 60.0, 1) AS minutes,
       date(datetime(start_local, '-12 hours')) AS night_date,
       source_name
FROM records
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
    connection.executescript(VIEWS)
    connection.execute("DROP TABLE IF EXISTS daily_metrics")
    connection.execute(DAILY_METRICS)
    connection.execute(
        "CREATE INDEX idx_daily_type_date ON daily_metrics (type_short, local_date)"
    )
    connection.execute(
        """
        UPDATE workouts SET n_points = (
            SELECT count(*) FROM workout_points p WHERE p.workout_id = workouts.id)
        """
    )
    connection.commit()
    return connection.execute("SELECT count(*) FROM daily_metrics").fetchone()[0]
