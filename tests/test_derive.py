"""Derived tables and views: the numbers people will actually read."""

from __future__ import annotations

import sqlite3
import unittest

from . import support

#: What `nightly_sleep` used to be, and still has to read for a single source.
SUM_OF_SEGMENTS = """
SELECT night_date,
       round(sum(CASE WHEN stage LIKE 'Asleep%' THEN minutes END) / 60.0, 2),
       round(sum(CASE WHEN stage = 'InBed' THEN minutes END) / 60.0, 2),
       round(sum(CASE WHEN stage = 'AsleepDeep' THEN minutes END), 0),
       round(sum(CASE WHEN stage = 'AsleepREM' THEN minutes END), 0),
       round(sum(CASE WHEN stage = 'Awake' THEN minutes END), 0)
FROM sleep_segments
GROUP BY night_date
ORDER BY night_date
"""


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

    def day_value(self, type_short: str) -> tuple:
        return support.rows(
            self.database,
            "select aggregation, day_value, total, mean from daily_metrics "
            "where type_short = ?",
            (type_short,),
        )[0]

    def test_a_cumulative_metric_reads_its_day_as_the_sum(self):
        """Steps add up: 1200 + 500 is the day's answer, their mean is not."""
        aggregation, day_value, total, _ = self.day_value("StepCount")
        self.assertEqual(aggregation, "cumulative")
        self.assertEqual(day_value, 1700.0)
        self.assertEqual(day_value, total)

    def test_a_discrete_metric_reads_its_day_as_the_mean(self):
        """60 bpm and 80 bpm make a day of 70 bpm, not one of 140."""
        aggregation, day_value, total, mean = self.day_value("HeartRate")
        self.assertEqual(aggregation, "discrete")
        self.assertEqual(day_value, 70.0)
        self.assertEqual(day_value, mean)
        self.assertEqual(total, 140.0)

    def test_an_unknown_metric_keeps_its_aggregates_and_picks_none(self):
        """What every metric looks like between an iOS release and a catalogue
        refresh: the numbers are all there, only the signpost is missing."""
        aggregation, day_value, total, mean = self.day_value("InventedFutureMetric")
        self.assertIsNone(aggregation)
        self.assertIsNone(day_value)
        self.assertEqual((total, mean), (7.0, 7.0))

    def test_every_row_states_where_its_day_value_came_from(self):
        """A `day_value` without its `aggregation` would be a number with no
        way to tell whether it was summed or averaged."""
        self.assertEqual(
            support.one(
                self.database,
                "select count(*) from daily_metrics "
                "where (day_value is null) != (aggregation is null)",
            ),
            0,
        )


class DeduplicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, _ = support.import_export(support.export_from_fixture("records"))

    def dedup(self, type_short: str, local_date: str) -> tuple:
        return support.rows(
            self.database,
            "select total, deduplicated_value from daily_metrics "
            "where type_short = ? and local_date = ?",
            (type_short, local_date),
        )[0]

    def test_complementary_sources_deduplicate_to_the_sum(self):
        """Watch in the morning, iPhone later, no overlap: the two 08-01 step
        samples reconcile to the same 1700 they sum to — nothing to drop."""
        total, deduplicated = self.dedup("StepCount", "2026-08-01")
        self.assertEqual(total, 1700.0)
        self.assertEqual(deduplicated, 1700.0)

    def test_an_overlap_is_prorated_below_the_sum(self):
        """8 km all-hour Watch and a half-overlapping 4 km Phone sum to 12, but
        the Phone's overlapped half is the Watch's, so Health shows 10."""
        total, deduplicated = self.dedup("DistanceWalkingRunning", "2026-08-05")
        self.assertEqual(total, 12.0)
        self.assertEqual(deduplicated, 10.0)

    def test_a_single_source_day_stays_null(self):
        """One source has nothing to reconcile, so the sweep leaves it alone and
        `day_value` is the number to read."""
        total, deduplicated = self.dedup("DistanceWalkingRunning", "2026-08-06")
        self.assertEqual(total, 5.0)
        self.assertIsNone(deduplicated)

    def test_discrete_metrics_are_never_deduplicated(self):
        """Averaging two sources' heart rates inflates nothing, so it is left
        NULL rather than reconciled."""
        self.assertEqual(
            support.one(
                self.database,
                "select count(*) from daily_metrics "
                "where aggregation = 'discrete' and deduplicated_value is not null",
            ),
            0,
        )

    def test_by_source_keeps_the_sources_apart(self):
        """The per-source table has the two step rows `daily_metrics` folds into
        one, so both the split and the deduplicated whole are on hand."""
        rows = support.rows(
            self.database,
            "select source_name, total from daily_metrics_by_source "
            "where type_short = 'StepCount' and local_date = '2026-08-01' "
            "order by source_name",
        )
        self.assertEqual(rows, [("Apple Watch", 1200.0), ("iPhone", 500.0)])


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


def sleep_record(source: str, stage: str, start: str, end: str) -> str:
    return (
        '<Record type="HKCategoryTypeIdentifierSleepAnalysis" '
        f'sourceName="{source}" startDate="2026-03-{start} +0100" '
        f'endDate="2026-03-{end} +0100" value="HKCategoryValueSleepAnalysis{stage}"/>\n'
    )


class SeveralSleepSourcesTest(unittest.TestCase):
    """Two sources recording one night are one night's sleep, not two."""

    COLUMNS = "hours_asleep, hours_in_bed, min_deep, min_rem, min_awake"

    def night(self, *records):
        database, _ = support.import_export(support.write_export("".join(records)))
        return support.rows(
            database, f"select night_date, {self.COLUMNS} from nightly_sleep"
        )

    def test_a_night_recorded_twice_counts_once(self):
        self.assertEqual(
            self.night(
                sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 07:00:00"),
                sleep_record("SleepApp", "AsleepCore", "01 23:00:00", "02 07:00:00"),
            ),
            [("2026-03-01", 8.0, None, None, None, None)],
        )

    def test_sources_that_complement_each_other_add_up(self):
        self.assertEqual(
            self.night(
                sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 03:00:00"),
                sleep_record("SleepApp", "AsleepCore", "02 03:00:00", "02 07:00:00"),
            ),
            [("2026-03-01", 8.0, None, None, None, None)],
        )

    def test_a_partial_overlap_counts_its_time_once(self):
        self.assertEqual(
            self.night(
                sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 05:00:00"),
                sleep_record("SleepApp", "AsleepCore", "02 03:00:00", "02 07:00:00"),
            ),
            [("2026-03-01", 8.0, None, None, None, None)],
        )

    def test_in_bed_from_one_source_does_not_push_out_sleep_from_another(self):
        """An iPhone often records only time in bed, and more of it than the
        Watch records of sleep. They answer different questions."""
        self.assertEqual(
            self.night(
                sleep_record("iPhone", "InBed", "01 22:30:00", "02 07:30:00"),
                sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 07:00:00"),
            ),
            [("2026-03-01", 8.0, 9.0, None, None, None)],
        )

    def test_the_stages_are_those_of_the_source_that_won(self):
        self.assertEqual(
            self.night(
                sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 05:00:00"),
                sleep_record("Watch", "AsleepDeep", "02 05:00:00", "02 06:00:00"),
                sleep_record("Watch", "AsleepREM", "02 06:00:00", "02 07:00:00"),
                sleep_record(
                    "SleepApp", "AsleepUnspecified", "02 00:00:00", "02 06:00:00"
                ),
            ),
            [("2026-03-01", 8.0, None, 60.0, 60.0, None)],
        )

    def test_a_better_covered_source_keeps_its_stages_over_a_shorter_one(self):
        self.assertEqual(
            self.night(
                sleep_record(
                    "SleepApp", "AsleepUnspecified", "01 23:00:00", "02 07:00:00"
                ),
                sleep_record("Watch", "AsleepDeep", "02 01:00:00", "02 02:00:00"),
            ),
            [("2026-03-01", 8.0, None, None, None, None)],
        )

    def test_different_nights_may_have_different_winners(self):
        database, _ = support.import_export(
            support.write_export(
                sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 07:00:00")
                + sleep_record("SleepApp", "AsleepCore", "02 23:00:00", "03 06:00:00")
                + sleep_record("Watch", "AsleepCore", "02 23:00:00", "03 01:00:00")
            )
        )
        self.assertEqual(
            support.rows(
                database,
                "select night_date, hours_asleep from nightly_sleep order by 1",
            ),
            [("2026-03-01", 8.0), ("2026-03-02", 7.0)],
        )

    def test_the_segments_stay_as_recorded(self):
        database, _ = support.import_export(
            support.write_export(
                sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 07:00:00")
                + sleep_record("SleepApp", "AsleepCore", "01 23:00:00", "02 07:00:00")
            )
        )
        self.assertEqual(
            support.one(database, "select count(*) from sleep_segments"), 2
        )

    def test_a_segment_without_a_readable_end_does_not_stop_the_import(self):
        self.assertEqual(
            self.night(
                sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 07:00:00"),
                sleep_record(
                    "SleepApp", "AsleepCore", "01 23:00:00", "02 07:00:00"
                ).replace(
                    'endDate="2026-03-02 07:00:00 +0100"', 'endDate="not a date"'
                ),
            ),
            [("2026-03-01", 8.0, None, None, None, None)],
        )

    def test_a_database_with_the_old_view_is_upgraded(self):
        """Up to 0.1.0, nightly_sleep was a view."""
        export = support.write_export(
            sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 07:00:00")
        )
        database, _ = support.import_export(export)
        connection = sqlite3.connect(database)
        connection.executescript(
            "drop table nightly_sleep;"
            "create view nightly_sleep as select 1 as night_date;"
        )
        connection.close()

        support.import_export(export, database=database)
        self.assertEqual(
            support.rows(
                database, "select night_date, hours_asleep from nightly_sleep"
            ),
            [("2026-03-01", 8.0)],
        )

    def test_importing_twice_gives_the_same_nights(self):
        export = support.write_export(
            sleep_record("Watch", "AsleepCore", "01 23:00:00", "02 07:00:00")
        )
        database, _ = support.import_export(export)
        support.import_export(export, database=database)
        self.assertEqual(support.one(database, "select count(*) from nightly_sleep"), 1)

    def test_a_single_source_reads_the_same_as_adding_up_its_segments(self):
        database = support.sample_database()
        self.assertEqual(
            support.rows(
                database,
                f"select night_date, {self.COLUMNS} from nightly_sleep order by 1",
            ),
            support.rows(database, SUM_OF_SEGMENTS),
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

    def test_every_synthetic_metric_knows_how_its_day_adds_up(self):
        """The synthesizer only emits identifiers Apple documents, so a metric
        without an aggregation style here means the catalogue has gone stale,
        not that Apple stayed silent."""
        self.assertEqual(
            support.rows(
                self.database,
                "select distinct type_short from daily_metrics "
                "where aggregation is null",
            ),
            [],
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
