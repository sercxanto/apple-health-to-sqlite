"""Orchestration: everything an import does, from archive to derived tables.

Kept separate from `importer.py` so that `routes.py` can depend on the importer
without a circular import, and so tests can drive a full import in one call.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from . import db, derive, dtd, routes
from .archive import ExportArchive
from .importer import Importer, ImportOptions, ImportResult


def read_schema(archive: ExportArchive) -> dtd.Schema:
    """Parse the DTD out of the export's own header."""
    with archive.open_export() as stream:
        return dtd.parse(dtd.extract(stream))


def run_import(
    export_path: str | os.PathLike[str],
    database_path: str,
    options: ImportOptions | None = None,
    on_progress=None,
) -> ImportResult:
    """Import an export into `database_path`, creating or extending it."""
    options = options or ImportOptions()
    started = time.monotonic()

    with ExportArchive(export_path) as archive:
        schema = read_schema(archive)
        # Fails loudly on anything older than version 14 rather than importing
        # a subset of the data and staying quiet about it.
        version = schema.require_supported()

        # Only now, so a mistyped export path leaves no stray directory behind.
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = db.connect(database_path)
        importer = Importer(connection, options)

        with archive.open_export() as stream:
            result = importer.parse(stream, on_progress=on_progress)
        connection.commit()

        if options.routes:
            result.routes_imported = routes.import_routes(importer, archive)
            connection.commit()

        result.version = version
        result.schema_differences = dtd.diff(dtd.bundled(), schema)
        _write_export_meta(connection, os.fspath(export_path), result)

    db.create_indexes(connection)
    if options.derived:
        derive.build(connection)
    connection.execute("ANALYZE")
    connection.commit()
    connection.close()
    # Report the whole run, not just the XML parse: indexes and derived tables
    # are a third of it on a large export.
    result.elapsed_s = time.monotonic() - started
    return result


def _write_export_meta(
    connection: sqlite3.Connection, source_file: str, result: ImportResult
) -> None:
    connection.execute("DELETE FROM export_meta")
    connection.execute(
        "INSERT INTO export_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            1,
            os.path.abspath(source_file),
            result.locale,
            result.export_date,
            result.version,
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            result.me.get("HKCharacteristicTypeIdentifierDateOfBirth"),
            result.me.get("HKCharacteristicTypeIdentifierBiologicalSex"),
            result.me.get("HKCharacteristicTypeIdentifierBloodType"),
            result.me.get("HKCharacteristicTypeIdentifierFitzpatrickSkinType"),
        ),
    )
    connection.commit()


#: Tables worth reporting row counts for after an import.
REPORTED_TABLES = (
    "records",
    "record_metadata",
    "hrv_beats",
    "correlations",
    "workouts",
    "workout_activities",
    "workout_statistics",
    "workout_events",
    "workout_records",
    "workout_zone_groups",
    "workout_zones",
    "workout_points",
    "activity_summaries",
    "clinical_records",
    "audiograms",
    "vision_prescriptions",
    "unhandled",
)


def table_counts(database_path: str) -> dict[str, int]:
    """Row counts for the tables an import fills, skipping empty ones."""
    connection = sqlite3.connect(database_path)
    try:
        counts = {}
        for table in REPORTED_TABLES:
            count = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if count:
                counts[table] = count
        return counts
    finally:
        connection.close()
