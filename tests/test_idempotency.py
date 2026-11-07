"""Re-importing must merge, not duplicate.

This is the property that makes the tool usable over years: every few months a
fresh export lands on the same database, overlapping almost entirely with what
is already there.
"""

from __future__ import annotations

import sqlite3
import unittest

from apple_health_to_sqlite import synthetic
from apple_health_to_sqlite.importer import UNHANDLED_LIMIT

from . import support

TABLES = (
    "records",
    "record_metadata",
    "hrv_beats",
    "correlations",
    "workouts",
    "workout_statistics",
    "workout_events",
    "workout_activities",
    "workout_records",
    "workout_zone_groups",
    "workout_zones",
    "workout_metadata",
    "extra_metadata",
    "workout_points",
    "workout_routes",
    "activity_summaries",
    "audiograms",
    "vision_prescriptions",
)


def counts(database):
    return {
        table: support.one(database, f"select count(*) from {table}")
        for table in TABLES
    }


class ReimportTest(unittest.TestCase):
    def test_importing_the_same_export_twice_changes_nothing(self):
        export = support.sample_export(days=10)
        database, _ = support.import_export(export)
        before = counts(database)

        support.import_export(export, database=database)
        self.assertEqual(counts(database), before)

    def test_overlapping_export_adds_only_the_new_days(self):
        base = support.workspace("incremental")
        options = {"seed": 11, "scale": 1.0}
        synthetic.synthesize(
            base / "first.zip",
            synthetic.SynthesizeOptions(days=10, end_date="2026-08-10", **options),
        )
        synthetic.synthesize(
            base / "second.zip",
            synthetic.SynthesizeOptions(days=10, end_date="2026-08-15", **options),
        )

        database, _ = support.import_export(base / "first.zip")
        first_count = support.one(database, "select count(*) from records")
        first_range = support.rows(
            database, "select min(local_date), max(local_date) from records"
        )[0]

        support.import_export(base / "second.zip", database=database)
        second_count = support.one(database, "select count(*) from records")
        second_range = support.rows(
            database, "select min(local_date), max(local_date) from records"
        )[0]

        self.assertGreater(second_count, first_count)
        self.assertEqual(first_range[0], second_range[0], "start date must not move")
        self.assertGreater(second_range[1], first_range[1], "should reach further")

        # Five days overlap; the merged database must be well short of the sum.
        self.assertLess(second_count, first_count * 2)

    def test_derived_tables_are_rebuilt_on_reimport(self):
        export = support.sample_export(days=6)
        database, _ = support.import_export(export)
        before = support.one(database, "select count(*) from daily_metrics")
        support.import_export(export, database=database)
        self.assertEqual(
            support.one(database, "select count(*) from daily_metrics"), before
        )

    def test_row_ids_are_stable_across_imports(self):
        """Ids are derived from content, so they must not move between runs."""
        export = support.sample_export(days=4)
        first, _ = support.import_export(export)
        second, _ = support.import_export(export)
        self.assertEqual(
            support.rows(first, "select id from records order by id limit 50"),
            support.rows(second, "select id from records order by id limit 50"),
        )


class UnhandledIdentityTest(unittest.TestCase):
    """A row in `unhandled` is identified by what it holds, like every other row."""

    def import_all(self, *bodies):
        database = support.workspace("db") / "health.db"
        for body in bodies:
            support.import_export(support.write_export(body), database=database)
        return database

    def stored(self, database):
        return sorted(
            row[0]
            for row in support.rows(database, "select xml from unhandled order by id")
        )

    def test_the_same_element_is_stored_once_across_imports(self):
        body = '<SomethingNew a="1"/>\n'
        self.assertEqual(len(self.stored(self.import_all(body, body))), 1)

    def test_a_different_element_at_the_same_position_is_not_lost(self):
        database = self.import_all('<SomethingNew a="1"/>\n', '<SomethingNew a="2"/>\n')
        stored = self.stored(database)
        self.assertEqual(len(stored), 2)
        self.assertIn('a="2"', stored[1])

    def test_the_order_of_the_elements_does_not_matter(self):
        database = self.import_all(
            '<SomethingNew a="1"/>\n<AlsoNew b="1"/>\n',
            '<AlsoNew b="1"/>\n<SomethingNew a="1"/>\n',
        )
        self.assertEqual(len(self.stored(database)), 2)

    def test_the_whitespace_around_an_element_does_not_matter(self):
        database = self.import_all(
            '<SomethingNew a="1"/>\n', '  <SomethingNew a="1"/><AlsoNew b="1"/>'
        )
        self.assertEqual(len(self.stored(database)), 2)

    def test_identical_elements_are_stored_once_and_counted_in_full(self):
        database, result = support.import_export(
            support.write_export('<SomethingNew a="1"/>\n' * 3)
        )
        self.assertEqual(len(self.stored(database)), 1)
        self.assertEqual(result.unhandled, {"SomethingNew": 3})

    def test_a_flood_of_one_element_does_not_crowd_out_another(self):
        flood = "".join(
            f'<SomethingNew a="{index}"/>\n' for index in range(UNHANDLED_LIMIT + 10)
        )
        database, result = support.import_export(
            support.write_export(flood + '<AlsoNew b="1"/>\n')
        )
        self.assertEqual(
            support.rows(
                database,
                "select tag, count(*) from unhandled group by tag order by tag",
            ),
            [("AlsoNew", 1), ("SomethingNew", UNHANDLED_LIMIT)],
        )
        self.assertEqual(result.unhandled["SomethingNew"], UNHANDLED_LIMIT + 10)

    def test_rows_stored_under_the_old_ids_are_not_duplicated(self):
        """0.1.0 numbered its rows, and a database from then is imported into."""
        body = '<SomethingNew a="1"/>\n'
        database = self.import_all(body)
        connection = sqlite3.connect(database)
        connection.execute(
            "update unhandled set id = 12345, xml = ?", ('<SomethingNew a="1" />\n',)
        )
        connection.commit()
        connection.close()

        support.import_export(support.write_export(body), database=database)
        self.assertEqual(len(self.stored(database)), 1)


if __name__ == "__main__":
    unittest.main()
