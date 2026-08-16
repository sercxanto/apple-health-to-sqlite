"""Re-importing must merge, not duplicate.

This is the property that makes the tool usable over years: every few months a
fresh export lands on the same database, overlapping almost entirely with what
is already there.
"""

from __future__ import annotations

import unittest

from applehealth import synthetic

from . import support

TABLES = (
    "records",
    "record_metadata",
    "hrv_beats",
    "correlations",
    "workouts",
    "workout_statistics",
    "workout_events",
    "workout_points",
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


if __name__ == "__main__":
    unittest.main()
