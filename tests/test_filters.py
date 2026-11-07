"""Import filters, which exist to keep a very large export manageable."""

from __future__ import annotations

import unittest

from apple_health_to_sqlite import timestamps
from apple_health_to_sqlite.importer import ImportOptions

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


def step_record(start: str, end: str | None = None) -> str:
    return (
        '<Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" '
        f'unit="count" startDate="{start}" endDate="{end or start}" value="10"/>\n'
    )


def dated_elements(day: str) -> str:
    """One of each element that carries a date, other than Record and Workout."""
    stamp = f"{day} 12:00:00 +0200"
    return f"""
<Correlation type="HKCorrelationTypeIdentifierBloodPressure" sourceName="Cuff"
             startDate="{stamp}" endDate="{stamp}"/>
<ActivitySummary dateComponents="{day}" activeEnergyBurned="100"/>
<ClinicalRecord type="HKClinicalTypeIdentifierLabResultRecord" identifier="{day}"
                sourceName="Clinic" sourceURL="https://example.invalid/" fhirVersion="4.0.1"
                receivedDate="{stamp}" resourceFilePath="/clinical-records/{day}.json"/>
<Audiogram type="HKDataTypeIdentifierAudiogram" sourceName="Audiologist"
           startDate="{stamp}" endDate="{stamp}"/>
<VisionPrescription type="HKGlassesPrescription" dateIssued="{stamp}">
  <RightEye sphere="-1"/>
</VisionPrescription>
"""


class DateFilterTest(unittest.TestCase):
    def test_since_drops_earlier_data(self):
        database, _ = support.import_export(
            support.sample_export(days=14), ImportOptions(since="2026-08-10")
        )
        earliest = support.one(database, "select min(local_date) from records")
        self.assertEqual(earliest, "2026-08-10")

    def test_since_also_applies_to_workouts(self):
        database, _ = support.import_export(
            support.sample_export(days=14), ImportOptions(since="2026-08-10")
        )
        self.assertGreaterEqual(
            support.one(database, "select min(local_date) from workouts"), "2026-08-10"
        )

    def test_since_keeps_the_first_minutes_of_the_day_east_of_utc(self):
        """00:30 at +0200 is still the evening before in UTC, but the calendar
        day it belongs to is the one the user asked for."""
        database, _ = support.import_export(
            support.write_export(step_record("2026-03-01 00:30:00 +0200")),
            ImportOptions(since="2026-03-01"),
        )
        self.assertEqual(support.one(database, "select count(*) from records"), 1)

    def test_since_drops_the_last_minutes_of_the_day_before_west_of_utc(self):
        """23:30 at -0500 is already the requested day in UTC, but not locally."""
        database, _ = support.import_export(
            support.write_export(step_record("2026-02-28 23:30:00 -0500")),
            ImportOptions(since="2026-03-01"),
        )
        self.assertEqual(support.one(database, "select count(*) from records"), 0)

    def test_since_applies_to_every_dated_element(self):
        database, _ = support.import_export(
            support.write_export(
                dated_elements("2026-02-28") + dated_elements("2026-03-01")
            ),
            ImportOptions(since="2026-03-01"),
        )
        for table, column, expected in (
            ("correlations", "local_date", "2026-03-01"),
            ("activity_summaries", "date_components", "2026-03-01"),
            ("clinical_records", "identifier", "2026-03-01"),
            ("audiograms", "substr(start_date, 1, 10)", "2026-03-01"),
            ("vision_prescriptions", "substr(date_issued, 1, 10)", "2026-03-01"),
        ):
            with self.subTest(table=table):
                self.assertEqual(
                    support.rows(database, f"select {column} from {table}"),
                    [(expected,)],
                )

    def test_a_filtered_correlation_leaves_no_record_pointing_at_it(self):
        """A record can start on the requested day inside a correlation that
        started the evening before. The record is kept, through its top-level
        copy, but must not name a correlation the database does not have."""
        record = step_record("2026-03-01 00:10:00 +0200")
        database, _ = support.import_export(
            support.write_export(
                f"""
<Correlation type="HKCorrelationTypeIdentifierFood" sourceName="Watch"
             startDate="2026-02-28 23:50:00 +0200" endDate="2026-03-01 00:10:00 +0200">
  {record}
</Correlation>
{record}"""
            ),
            ImportOptions(since="2026-03-01"),
        )
        self.assertEqual(
            support.rows(database, "select correlation_id from records"), [(None,)]
        )
        self.assertEqual(support.one(database, "select count(*) from correlations"), 0)

    def test_an_entry_with_an_unreadable_date_is_kept(self):
        """Nothing says it is older, and dropping it would lose it silently."""
        database, _ = support.import_export(
            support.write_export(step_record("not a date")),
            ImportOptions(since="2026-03-01"),
        )
        self.assertEqual(support.one(database, "select count(*) from records"), 1)

    def test_an_invalid_date_is_refused_rather_than_ignored(self):
        for value in ("yesterday", "2026-13-45", "01.03.2026", ""):
            with self.subTest(value=value), self.assertRaises(timestamps.InvalidDate):
                ImportOptions(since=value)

    def test_a_date_without_leading_zeros_means_the_same_day(self):
        database, _ = support.import_export(
            support.write_export(step_record("2026-03-01 00:30:00 +0200")),
            ImportOptions(since="2026-3-1"),
        )
        self.assertEqual(support.one(database, "select count(*) from records"), 1)


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
