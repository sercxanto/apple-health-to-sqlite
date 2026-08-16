"""Derived tables and views: the numbers people will actually read."""

from __future__ import annotations

import unittest

from . import support


class DailyMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, _ = support.import_export(support.export_from_fixture("records"))

    def test_sums_the_days_samples(self):
        """1200 + 500 steps on 2026-08-01."""
        total, count = support.rows(
            self.database,
            "select total, n from daily_metrics "
            "where type_short = 'StepCount' and local_date = '2026-08-01'",
        )[0]
        self.assertEqual(total, 1700.0)
        self.assertEqual(count, 2)

    def test_keeps_mean_min_and_max(self):
        mean, minimum, maximum = support.rows(
            self.database,
            "select mean, min_value, max_value from daily_metrics "
            "where type_short = 'StepCount' and local_date = '2026-08-01'",
        )[0]
        self.assertEqual((mean, minimum, maximum), (850.0, 500.0, 1200.0))

    def test_groups_by_local_date_not_utc(self):
        self.assertEqual(
            support.one(
                self.database,
                "select local_date from daily_metrics where type_short = 'BodyMass'",
            ),
            "2026-03-05",
        )

    def test_non_numeric_records_are_left_out(self):
        self.assertEqual(
            support.one(
                self.database,
                "select count(*) from daily_metrics where type_short = 'SleepAnalysis'",
            ),
            0,
        )

    def test_units_are_kept_apart(self):
        units = support.rows(
            self.database,
            "select distinct type_short, unit from daily_metrics order by type_short",
        )
        self.assertIn(("StepCount", "count"), units)
        self.assertIn(("BodyMass", "kg"), units)


class MetricSummaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, _ = support.import_export(support.export_from_fixture("records"))

    def test_counts_agree_with_the_records_table(self):
        self.assertEqual(
            support.one(self.database, "select sum(samples) from metric_summary"),
            support.one(
                self.database,
                "select count(*) from records where local_date is not null",
            ),
        )

    def test_splits_one_metric_by_source(self):
        """Two step-count samples from two devices, so two rows."""
        rows = support.rows(
            self.database,
            "select source_name, samples from metric_summary "
            "where type_short = 'StepCount' order by source_name",
        )
        self.assertEqual(rows, [("Apple Watch", 1), ("iPhone", 1)])

    def test_records_the_period_covered(self):
        first_seen, last_seen = support.rows(
            self.database,
            "select first_seen, last_seen from metric_summary "
            "where type_short = 'BodyMass'",
        )[0]
        self.assertEqual((first_seen, last_seen), ("2026-03-05", "2026-03-05"))

    def test_covers_non_numeric_metrics_too(self):
        """Unlike daily_metrics, which only aggregates numbers."""
        self.assertEqual(
            support.one(
                self.database,
                "select samples from metric_summary where type_short = 'SleepAnalysis'",
            ),
            1,
        )

    def test_is_rebuilt_rather_than_appended_to(self):
        before = support.one(self.database, "select count(*) from metric_summary")
        support.import_export(
            support.export_from_fixture("records"), database=self.database
        )
        self.assertEqual(
            support.one(self.database, "select count(*) from metric_summary"), before
        )


class SleepViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, _ = support.import_export(
            support.export_from_fixture("sleep_nights")
        )

    def test_segments_expose_the_stage_without_its_prefix(self):
        stages = {
            row[0]
            for row in support.rows(self.database, "select stage from sleep_segments")
        }
        self.assertEqual(
            stages, {"InBed", "AsleepCore", "AsleepDeep", "Awake", "AsleepREM"}
        )

    def test_minutes_are_computed_from_the_span(self):
        self.assertEqual(
            support.one(
                self.database,
                "select minutes from sleep_segments where stage = 'AsleepDeep'",
            ),
            60.0,
        )

    def test_a_night_spans_midnight(self):
        """Falling asleep at 23:00 and waking at 07:00 is one night, not two."""
        nights = support.rows(
            self.database,
            "select night_date, hours_asleep from nightly_sleep order by 1",
        )
        self.assertEqual(nights[0][0], "2026-08-01")
        # 2h core + 1h deep, with the Awake segment excluded.
        self.assertEqual(nights[0][1], 3.0)

    def test_afternoon_sleep_belongs_to_the_following_night(self):
        """The boundary sits at noon, so a nap at 14:00 starts a new night."""
        nights = dict(
            support.rows(
                self.database, "select night_date, hours_asleep from nightly_sleep"
            )
        )
        self.assertEqual(nights["2026-08-02"], 1.0)

    def test_in_bed_is_counted_separately_from_asleep(self):
        hours_asleep, hours_in_bed = support.rows(
            self.database,
            "select hours_asleep, hours_in_bed from nightly_sleep "
            "where night_date = '2026-08-01'",
        )[0]
        self.assertEqual(hours_in_bed, 8.0)
        self.assertLess(hours_asleep, hours_in_bed)

    def test_awake_minutes_are_reported(self):
        self.assertEqual(
            support.one(
                self.database,
                "select min_awake from nightly_sleep where night_date = '2026-08-01'",
            ),
            15.0,
        )


class SyntheticDataViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = support.sample_database()

    def test_daily_metrics_covers_every_numeric_type(self):
        raw = {
            row[0]
            for row in support.rows(
                self.database,
                "select distinct type_short from records where value is not null",
            )
        }
        derived = {
            row[0]
            for row in support.rows(
                self.database, "select distinct type_short from daily_metrics"
            )
        }
        self.assertEqual(raw, derived)

    def test_nightly_sleep_has_a_row_per_night(self):
        self.assertGreater(
            support.one(self.database, "select count(*) from nightly_sleep"), 5
        )

    def test_daily_metrics_index_exists(self):
        indexes = {
            row[0]
            for row in support.rows(
                self.database, "select name from sqlite_master where type = 'index'"
            )
        }
        self.assertIn("idx_daily_type_date", indexes)


if __name__ == "__main__":
    unittest.main()
