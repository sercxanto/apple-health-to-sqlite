"""Database schema, connection setup, and row identity.

Every table uses a **content-addressed primary key**: a 64-bit hash of the
fields that identify the row (type, source, time span, value). Combined with
`INSERT OR IGNORE`, that makes importing idempotent — running a fresh export
over an existing database adds only what is new, which is the whole point once
the export is large enough that re-importing from scratch is annoying.

Child rows key on their parent's id plus a local sequence number, so they stay
consistent when the parent is skipped as a duplicate.
"""

from __future__ import annotations

import hashlib
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS export_meta (
    id INTEGER PRIMARY KEY,
    source_file TEXT, locale TEXT, export_date TEXT,
    healthkit_export_version INTEGER, imported_at TEXT,
    me_date_of_birth TEXT, me_biological_sex TEXT, me_blood_type TEXT,
    me_skin_type TEXT
);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL, type_short TEXT,
    unit TEXT, value REAL, value_text TEXT,
    source_name TEXT, source_version TEXT, device TEXT, device_name TEXT,
    creation_date TEXT, start_date TEXT, end_date TEXT,
    start_local TEXT, end_local TEXT, local_date TEXT,
    start_ts INTEGER, end_ts INTEGER, duration_s REAL,
    correlation_id INTEGER
);

CREATE TABLE IF NOT EXISTS record_metadata (
    record_id INTEGER, key TEXT, value TEXT,
    PRIMARY KEY (record_id, key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS hrv_beats (
    record_id INTEGER, seq INTEGER, bpm REAL, time TEXT, ts INTEGER,
    PRIMARY KEY (record_id, seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS correlations (
    id INTEGER PRIMARY KEY,
    type TEXT, type_short TEXT, source_name TEXT, source_version TEXT,
    device TEXT, creation_date TEXT, start_date TEXT, end_date TEXT,
    local_date TEXT, start_ts INTEGER, end_ts INTEGER
);

CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY,
    activity_type TEXT, activity_short TEXT,
    duration REAL, duration_unit TEXT,
    total_distance REAL, total_distance_unit TEXT,
    total_energy_burned REAL, total_energy_burned_unit TEXT,
    source_name TEXT, source_version TEXT, device TEXT, device_name TEXT,
    creation_date TEXT, start_date TEXT, end_date TEXT,
    start_local TEXT, local_date TEXT,
    start_ts INTEGER, end_ts INTEGER, duration_s REAL,
    route_file TEXT, n_points INTEGER
);

CREATE TABLE IF NOT EXISTS workout_metadata (
    workout_id INTEGER, key TEXT, value TEXT,
    PRIMARY KEY (workout_id, key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workout_events (
    workout_id INTEGER, seq INTEGER, type TEXT, type_short TEXT,
    date TEXT, ts INTEGER, duration REAL, duration_unit TEXT,
    PRIMARY KEY (workout_id, seq)
) WITHOUT ROWID;

-- activity_uuid is '' for statistics that belong to the workout as a whole,
-- and the WorkoutActivity uuid for the segments of a multi-sport workout.
-- Without it the two would collide on (workout_id, type).
CREATE TABLE IF NOT EXISTS workout_statistics (
    workout_id INTEGER, activity_uuid TEXT, type TEXT, type_short TEXT,
    start_date TEXT, end_date TEXT,
    average REAL, minimum REAL, maximum REAL, sum REAL, unit TEXT,
    PRIMARY KEY (workout_id, activity_uuid, type)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workout_activities (
    workout_id INTEGER, uuid TEXT,
    start_date TEXT, end_date TEXT, duration REAL, duration_unit TEXT,
    PRIMARY KEY (workout_id, uuid)
) WITHOUT ROWID;

-- Which records were taken during which workout. A table rather than a column
-- on records, because one record could belong to several workouts, and because
-- a column could not be added to the databases that already exist.
CREATE TABLE IF NOT EXISTS workout_records (
    workout_id INTEGER, record_id INTEGER,
    PRIMARY KEY (workout_id, record_id)
) WITHOUT ROWID;

-- activity_uuid as in workout_statistics. Two tables, because a group declares
-- its type, source and unit once for all its zones and may have no zones at all.
CREATE TABLE IF NOT EXISTS workout_zone_groups (
    workout_id INTEGER, activity_uuid TEXT, seq INTEGER,
    type TEXT, type_short TEXT, source TEXT, unit TEXT,
    PRIMARY KEY (workout_id, activity_uuid, seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workout_zones (
    workout_id INTEGER, activity_uuid TEXT, group_seq INTEGER, seq INTEGER,
    minimum REAL, maximum REAL, duration REAL, duration_unit TEXT,
    PRIMARY KEY (workout_id, activity_uuid, group_seq, seq)
) WITHOUT ROWID;

-- One row per route of a workout, in the export's order. file is NULL for a route
-- that names no file. workout_points holds their points under one running seq.
CREATE TABLE IF NOT EXISTS workout_routes (
    workout_id INTEGER, seq INTEGER, file TEXT,
    source_name TEXT, source_version TEXT, device TEXT, creation_date TEXT,
    start_date TEXT, end_date TEXT,
    PRIMARY KEY (workout_id, seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workout_points (
    workout_id INTEGER, seq INTEGER,
    latitude REAL, longitude REAL, elevation REAL,
    time TEXT, ts INTEGER, speed REAL, course REAL,
    horizontal_accuracy REAL, vertical_accuracy REAL,
    PRIMARY KEY (workout_id, seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS activity_summaries (
    date_components TEXT PRIMARY KEY,
    active_energy_burned REAL, active_energy_burned_goal REAL,
    active_energy_burned_unit TEXT,
    apple_move_time REAL, apple_move_time_goal REAL,
    apple_exercise_time REAL, apple_exercise_time_goal REAL,
    apple_stand_hours REAL, apple_stand_hours_goal REAL
);

CREATE TABLE IF NOT EXISTS clinical_records (
    id INTEGER PRIMARY KEY,
    type TEXT, identifier TEXT, source_name TEXT, source_url TEXT,
    fhir_version TEXT, received_date TEXT, resource_file_path TEXT
);

CREATE TABLE IF NOT EXISTS audiograms (
    id INTEGER PRIMARY KEY,
    type TEXT, source_name TEXT, device TEXT,
    start_date TEXT, end_date TEXT, start_ts INTEGER
);

CREATE TABLE IF NOT EXISTS audiogram_points (
    audiogram_id INTEGER, seq INTEGER,
    frequency_value REAL, frequency_unit TEXT,
    left_ear_value REAL, left_ear_unit TEXT, left_ear_masked TEXT,
    left_ear_clamping_lower REAL, left_ear_clamping_upper REAL,
    right_ear_value REAL, right_ear_unit TEXT, right_ear_masked TEXT,
    right_ear_clamping_lower REAL, right_ear_clamping_upper REAL,
    PRIMARY KEY (audiogram_id, seq)
) WITHOUT ROWID;

-- One row per eye. Every measurement has its own unit attribute in the
-- export, and they are kept rather than assumed: dioptres, degrees,
-- millimetres and prism dioptres all appear here.
CREATE TABLE IF NOT EXISTS vision_prescriptions (
    id INTEGER PRIMARY KEY,
    -- Shared by the two rows of one prescription; extra_metadata.owner_id
    -- refers to this, not to id.
    prescription_id INTEGER,
    type TEXT, date_issued TEXT, expiration_date TEXT, brand TEXT, eye TEXT,
    sphere REAL, sphere_unit TEXT,
    cylinder REAL, cylinder_unit TEXT,
    axis REAL, axis_unit TEXT,
    add_power REAL, add_power_unit TEXT,
    vertex REAL, vertex_unit TEXT,
    prism_amount REAL, prism_amount_unit TEXT,
    prism_angle REAL, prism_angle_unit TEXT,
    far_pd REAL, far_pd_unit TEXT,
    near_pd REAL, near_pd_unit TEXT,
    base_curve REAL, base_curve_unit TEXT,
    diameter REAL, diameter_unit TEXT
);

-- MetadataEntry and Attachment children of the rarer top-level elements
-- (Correlation, Audiogram, VisionPrescription). They are too infrequent to
-- deserve a table each, and dropping them would violate the rule that nothing
-- disappears silently. owner_type names the table the owner id refers to.
CREATE TABLE IF NOT EXISTS extra_metadata (
    owner_type TEXT, owner_id INTEGER, key TEXT, value TEXT,
    PRIMARY KEY (owner_type, owner_id, key)
) WITHOUT ROWID;

-- Anything this importer has no handler for. Keeping the raw XML beats
-- dropping it silently: after an iOS update, this table is where a new
-- element type shows up first.
CREATE TABLE IF NOT EXISTS unhandled (
    id INTEGER PRIMARY KEY, tag TEXT, xml TEXT
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_records_type_ts ON records (type, start_ts);
CREATE INDEX IF NOT EXISTS idx_records_short_date ON records (type_short, local_date);
CREATE INDEX IF NOT EXISTS idx_records_local_date ON records (local_date);
CREATE INDEX IF NOT EXISTS idx_records_source ON records (source_name);
CREATE INDEX IF NOT EXISTS idx_records_correlation ON records (correlation_id);
CREATE INDEX IF NOT EXISTS idx_workouts_ts ON workouts (start_ts);
CREATE INDEX IF NOT EXISTS idx_workouts_activity ON workouts (activity_short);
"""

#: Number of rows buffered per table before an executemany.
BATCH_SIZE = 5000


class UnusableDatabase(Exception):
    """Raised when SQLite refuses the database an import is writing to."""


def connect(path: str) -> sqlite3.Connection:
    """Open the database with settings tuned for a bulk import."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=OFF;")
        connection.executescript(SCHEMA)
    except sqlite3.Error:
        connection.close()
        raise
    return connection


def collapse_unhandled(connection: sqlite3.Connection) -> None:
    """Remove rows of `unhandled` that hold the same element twice.

    Version 0.1.0 numbered these rows instead of hashing them, with the
    whitespace after the element included, so importing into a database from
    then stores each element again under its proper id. The table holds at most
    a few thousand rows, so this costs nothing when there is nothing to do.
    """
    connection.execute(
        """
        DELETE FROM unhandled WHERE id NOT IN (
            SELECT min(id) FROM unhandled
            GROUP BY tag, trim(xml, ' ' || char(9, 10, 13))
        )
        """
    )


def create_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(INDEXES)
    connection.commit()


def content_id(*parts: object) -> int:
    """Stable 64-bit id derived from the fields that identify a row.

    Two rows that agree on every identifying field *are* the same measurement —
    Apple exports contain such duplicates when a record is written twice — so
    collapsing them is intended, not a loss.
    """
    joined = "\x1f".join("" if part is None else str(part) for part in parts)
    digest = hashlib.blake2b(joined.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)
