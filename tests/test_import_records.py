"""Importing records, including the shapes that are easy to get wrong."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from . import support


class RecordImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, cls.result = support.import_export(
            support.export_from_fixture("records")
        )

    def test_imports_every_record_including_the_nested_ones(self):
        # Twelve top-level records plus the two inside the Correlation.
        self.assertEqual(support.one(self.database, "select count(*) from records"), 14)

    def test_numeric_values_land_in_value(self):
        value, text = support.rows(
            self.database,
            "select value, value_text from records "
            "where type_short = 'StepCount' and value = 1200",
        )[0]
        self.assertEqual(value, 1200.0)
        self.assertIsNone(text)

    def test_enumerated_values_land_in_value_text(self):
        value, text = support.rows(
            self.database,
            "select value, value_text from records where type_short = 'SleepAnalysis'",
        )[0]
        self.assertIsNone(value)
        self.assertEqual(text, "HKCategoryValueSleepAnalysisAsleepREM")

    def test_type_short_strips_the_hk_prefix(self):
        self.assertIn(
            ("StepCount",),
            support.rows(self.database, "select distinct type_short from records"),
        )

    def test_device_name_is_extracted_from_the_device_string(self):
        self.assertEqual(
            support.one(
                self.database,
                "select device_name from records where source_name = 'Apple Watch' "
                "and type_short = 'StepCount'",
            ),
            "Apple Watch",
        )

    def test_metadata_entries(self):
        entries = dict(
            support.rows(
                self.database,
                "select m.key, m.value from record_metadata m join records r "
                "on r.id = m.record_id where r.value = 1200.0",
            )
        )
        self.assertEqual(
            entries, {"HKWasUserEntered": "0", "HKTimeZone": "Europe/Berlin"}
        )

    def test_heart_rate_variability_beats(self):
        beats = support.rows(
            self.database, "select seq, bpm from hrv_beats order by seq"
        )
        self.assertEqual([bpm for _, bpm in beats], [58.0, 61.0, 57.0])

    def test_correlation_wraps_its_records(self):
        rows = support.rows(
            self.database,
            "select r.type_short, r.value from records r "
            "join correlations c on c.id = r.correlation_id "
            "order by r.type_short",
        )
        self.assertEqual(
            rows,
            [("BloodPressureDiastolic", 79.0), ("BloodPressureSystolic", 124.0)],
        )

    def test_records_duplicated_at_top_level_collapse(self):
        """Apple emits correlation children twice, once nested and once at the
        top level. Two rows for one measurement would double every blood
        pressure reading."""
        self.assertEqual(
            support.one(
                self.database,
                "select count(*) from records "
                "where type_short = 'BloodPressureSystolic'",
            ),
            1,
        )

    def test_the_duplicate_does_not_erase_the_correlation_link(self):
        """The copies are identical apart from the link, so whichever row won
        would otherwise depend on document order."""
        self.assertIsNotNone(
            support.one(
                self.database,
                "select correlation_id from records "
                "where type_short = 'BloodPressureSystolic'",
            )
        )

    def test_records_outside_a_correlation_have_no_correlation_id(self):
        self.assertEqual(
            support.one(
                self.database,
                "select count(*) from records where correlation_id is null",
            ),
            12,
        )

    def test_local_date_follows_the_wall_clock_not_utc(self):
        """23:30 at -0500 is already the next day in UTC. Grouping by UTC would
        put the measurement on the wrong day."""
        local_date, start_ts = support.rows(
            self.database,
            "select local_date, start_ts from records where type_short = 'BodyMass'",
        )[0]
        self.assertEqual(local_date, "2026-03-05")
        self.assertEqual(
            datetime.fromtimestamp(start_ts, UTC).strftime("%Y-%m-%d"),
            "2026-03-06",
        )

    def test_duration_is_derived_from_the_span(self):
        self.assertEqual(
            support.one(
                self.database,
                "select duration_s from records where type_short = 'MindfulSession'",
            ),
            600.0,
        )

    def test_record_without_a_value_is_still_imported(self):
        value, text = support.rows(
            self.database,
            "select value, value_text from records where type_short = 'MindfulSession'",
        )[0]
        self.assertIsNone(value)
        self.assertIsNone(text)

    def test_export_metadata_is_recorded(self):
        version, locale, export_date = support.rows(
            self.database,
            "select healthkit_export_version, locale, export_date from export_meta",
        )[0]
        self.assertEqual(version, 14)
        self.assertEqual(locale, "en_US")
        self.assertEqual(export_date, "2026-08-15 21:30:00 +0200")

    def test_no_unhandled_elements(self):
        self.assertEqual(self.result.unhandled, {})


class UnknownElementTest(unittest.TestCase):
    """A future export version must not lose data silently."""

    @classmethod
    def setUpClass(cls):
        cls.database, cls.result = support.import_export(
            support.export_from_fixture("unknown_element")
        )

    def test_known_records_still_import(self):
        self.assertEqual(support.one(self.database, "select count(*) from records"), 1)

    def test_unknown_elements_are_counted_and_reported(self):
        self.assertEqual(self.result.unhandled, {"FutureHealthThing": 2})

    def test_raw_xml_is_kept(self):
        stored = support.rows(self.database, "select tag, xml from unhandled")
        self.assertEqual(len(stored), 2)
        self.assertIn("HKSomethingNew", stored[0][1])
        # Children come along, so nothing about the element is lost.
        self.assertIn("FutureDetail", "".join(xml for _, xml in stored))


class UnsupportedVersionTest(unittest.TestCase):
    def test_old_export_is_refused_with_an_explanation(self):
        from apple_health_to_sqlite import dtd

        export = support.write_export(
            '<Record type="HKQuantityTypeIdentifierStepCount" unit="count"'
            ' value="1" sourceName="iPhone" sourceVersion="9.0"'
            ' startDate="2016-08-01 08:00:00 +0200"'
            ' endDate="2016-08-01 09:00:00 +0200"/>\n',
            version=9,
        )
        with self.assertRaises(dtd.SchemaError) as caught:
            support.import_export(export)
        self.assertIn("version 9", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
