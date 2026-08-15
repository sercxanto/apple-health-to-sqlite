"""Import filters, which exist to keep a very large export manageable."""

from __future__ import annotations

import unittest

from applehealth.importer import ImportOptions

from . import support


class TypeFilterTest(unittest.TestCase):
    def test_only_the_requested_types_are_imported(self):
        database, _ = support.import_export(
            support.sample_export(days=6),
            ImportOptions(types=("StepCount", "HeartRate")),
        )
        self.assertEqual(
            {
                row[0]
                for row in support.rows(
                    database, "select distinct type_short from records"
                )
            },
            {"StepCount", "HeartRate"},
        )

    def test_fully_qualified_type_names_work_too(self):
        database, _ = support.import_export(
            support.sample_export(days=6),
            ImportOptions(types=("HKQuantityTypeIdentifierStepCount",)),
        )
        self.assertEqual(
            {
                row[0]
                for row in support.rows(
                    database, "select distinct type_short from records"
                )
            },
            {"StepCount"},
        )

    def test_excluded_types_are_dropped(self):
        database, _ = support.import_export(
            support.sample_export(days=6), ImportOptions(exclude_types=("HeartRate",))
        )
        self.assertEqual(
            support.one(
                database,
                "select count(*) from records where type_short = 'HeartRate'",
            ),
            0,
        )
        self.assertGreater(support.one(database, "select count(*) from records"), 0)


class DateFilterTest(unittest.TestCase):
    def test_since_drops_earlier_data(self):
        database, _ = support.import_export(
            support.sample_export(days=14), ImportOptions(since="2026-08-10")
        )
        earliest = support.one(database, "select min(local_date) from records")
        self.assertGreaterEqual(earliest, "2026-08-09")

    def test_since_also_applies_to_workouts(self):
        database, _ = support.import_export(
            support.sample_export(days=14), ImportOptions(since="2026-08-10")
        )
        self.assertGreaterEqual(
            support.one(database, "select min(local_date) from workouts"), "2026-08-09"
        )


class RouteAndDerivedSwitchTest(unittest.TestCase):
    def test_no_routes_skips_the_gpx_files(self):
        database, result = support.import_export(
            support.sample_export(days=6), ImportOptions(routes=False)
        )
        self.assertEqual(result.routes_imported, 0)
        self.assertEqual(
            support.one(database, "select count(*) from workout_points"), 0
        )

    def test_no_derived_leaves_daily_metrics_out(self):
        database, _ = support.import_export(
            support.sample_export(days=6), ImportOptions(derived=False)
        )
        tables = {
            row[0]
            for row in support.rows(
                database, "select name from sqlite_master where type = 'table'"
            )
        }
        self.assertNotIn("daily_metrics", tables)


if __name__ == "__main__":
    unittest.main()
