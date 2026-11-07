"""A database outlives the version that created it, and has to keep working."""

from __future__ import annotations

import sqlite3
import unittest

from apple_health_to_sqlite import db

from . import support

AUDIOGRAM = (
    '<Audiogram type="HKDataTypeIdentifierAudiogram" sourceName="Audiologist" '
    'sourceVersion="4.2" creationDate="2026-03-02 09:00:00 +0100" '
    'startDate="2026-03-01 09:00:00 +0100" endDate="2026-03-01 09:30:00 +0100"/>\n'
)

#: The two tables as version 0.1.0 created them, which had no layout version.
ORIGINAL_LAYOUT = """
DROP TABLE audiograms;
CREATE TABLE audiograms (
    id INTEGER PRIMARY KEY,
    type TEXT, source_name TEXT, device TEXT,
    start_date TEXT, end_date TEXT, start_ts INTEGER
);
DROP TABLE export_meta;
CREATE TABLE export_meta (
    id INTEGER PRIMARY KEY,
    source_file TEXT, locale TEXT, export_date TEXT,
    healthkit_export_version INTEGER, imported_at TEXT,
    me_date_of_birth TEXT, me_biological_sex TEXT, me_blood_type TEXT,
    me_skin_type TEXT
);
PRAGMA user_version = 0;
"""


def user_version(path) -> int:
    return support.one(path, "pragma user_version")


def layout_of(path) -> dict[str, list[tuple]]:
    """Every table's columns, in order, since rows are inserted by position."""
    tables = support.rows(
        path, "select name from sqlite_master where type = 'table' order by name"
    )
    return {
        name: support.rows(path, f"pragma table_info({name})") for (name,) in tables
    }


def as_of_version_0_1_0(path) -> None:
    """Turn a database into what 0.1.0 left behind, keeping the audiogram rows."""
    rows = support.rows(
        path,
        "select id, type, source_name, device, start_date, end_date, start_ts "
        "from audiograms",
    )
    connection = sqlite3.connect(path)
    connection.executescript(ORIGINAL_LAYOUT)
    connection.executemany("insert into audiograms values (?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


def fresh_database():
    path = support.workspace("db") / "health.db"
    db.connect(str(path)).close()
    return path


class LayoutVersionTest(unittest.TestCase):
    def test_a_new_database_is_marked_with_the_current_version(self):
        self.assertEqual(user_version(fresh_database()), db.SCHEMA_VERSION)
        self.assertGreater(db.SCHEMA_VERSION, 0)

    def test_an_older_database_ends_up_with_the_layout_of_a_new_one(self):
        """Column order matters as much as the columns: rows go in by position."""
        older = fresh_database()
        as_of_version_0_1_0(older)
        db.connect(str(older)).close()

        self.assertEqual(layout_of(older), layout_of(fresh_database()))
        self.assertEqual(user_version(older), db.SCHEMA_VERSION)

    def test_connecting_again_changes_nothing(self):
        older = fresh_database()
        as_of_version_0_1_0(older)
        db.connect(str(older)).close()
        layout = layout_of(older)

        db.connect(str(older)).close()
        self.assertEqual(layout_of(older), layout)

    def test_a_database_from_a_newer_version_is_refused_and_left_alone(self):
        path = fresh_database()
        connection = sqlite3.connect(path)
        connection.execute(f"pragma user_version = {db.SCHEMA_VERSION + 1}")
        connection.close()

        with self.assertRaisesRegex(db.UnusableDatabase, "newer version"):
            db.connect(str(path))
        self.assertEqual(user_version(path), db.SCHEMA_VERSION + 1)


class ImportingIntoAnOlderDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.export = support.write_export(AUDIOGRAM)
        self.database, _ = support.import_export(self.export)
        as_of_version_0_1_0(self.database)
        support.import_export(self.export, database=self.database)

    def test_rows_already_there_gain_what_they_were_missing(self):
        self.assertEqual(
            support.rows(
                self.database, "select source_version, creation_date from audiograms"
            ),
            [("4.2", "2026-03-02 09:00:00 +0100")],
        )

    def test_the_export_meta_row_is_written_in_the_new_layout(self):
        self.assertEqual(
            support.one(self.database, "select count(*) from export_meta"), 1
        )


if __name__ == "__main__":
    unittest.main()
