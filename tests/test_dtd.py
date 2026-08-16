"""The bundled DTD is the project's source of truth, so it is held to it."""

from __future__ import annotations

import unittest

from applehealth import dtd, importer
from applehealth.archive import ExportArchive

from . import support


class BundledSchemaTest(unittest.TestCase):
    def setUp(self):
        self.schema = dtd.bundled()

    def test_parses_and_declares_the_expected_version(self):
        self.assertEqual(self.schema.version, 14)
        self.assertEqual(self.schema.require_supported(), 14)

    def test_declares_the_core_elements(self):
        for element in ("HealthData", "Record", "Workout", "WorkoutStatistics"):
            self.assertIn(element, self.schema)

    def test_record_attributes(self):
        self.assertEqual(
            self.schema.attributes_of("Record"),
            {
                "type",
                "unit",
                "value",
                "sourceName",
                "sourceVersion",
                "device",
                "creationDate",
                "startDate",
                "endDate",
            },
        )

    def test_every_declared_element_has_a_handler(self):
        """The regression guard for a new export version.

        If Apple adds an element and someone copies the new DTD in without
        writing a handler, this fails and says which element is missing.
        """
        self.assertEqual(
            set(self.schema.elements) - importer.HANDLED_ELEMENTS,
            set(),
            "declared in the DTD but no importer handler",
        )

    def test_workout_activity_is_declared_but_unreachable(self):
        """Apple's version 14 DTD declares WorkoutActivity and then references
        it from no content model. Pinned down here so that a future schema
        update that fixes it is noticed rather than absorbed."""
        self.assertEqual(
            set(self.schema.elements) - self.schema.reachable_elements(),
            {"WorkoutActivity"},
        )

    def test_correlation_children_are_documented_as_duplicated(self):
        """The comment drives real behaviour in the importer, so losing it in a
        schema refresh should be visible."""
        self.assertIn(
            "also appear as top-level records",
            self.schema.text,
        )

    def test_workout_declares_both_totals_and_statistics(self):
        """Both forms are declared, which is why the importer reads both."""
        self.assertIn("totalDistance", self.schema.attributes_of("Workout"))
        self.assertIn("WorkoutStatistics", self.schema.elements["Workout"].children())

    def test_no_handler_without_a_declaration(self):
        self.assertEqual(
            importer.HANDLED_ELEMENTS - set(self.schema.elements),
            set(),
            "handled by the importer but not declared in the DTD",
        )


class ReachabilityTest(unittest.TestCase):
    def test_follows_the_content_models(self):
        schema = dtd.parse(
            "<!DOCTYPE HealthData [\n"
            "<!ELEMENT HealthData (Alpha)>\n"
            "<!ELEMENT Alpha (Beta*)>\n"
            "<!ELEMENT Beta EMPTY>\n"
            "<!ELEMENT Orphan EMPTY>\n"
            "]>"
        )
        self.assertEqual(schema.reachable_elements(), {"HealthData", "Alpha", "Beta"})

    def test_keywords_are_not_mistaken_for_elements(self):
        schema = dtd.parse("<!DOCTYPE HealthData [\n<!ELEMENT HealthData EMPTY>\n]>")
        self.assertEqual(schema.reachable_elements(), {"HealthData"})

    def test_survives_a_recursive_content_model(self):
        schema = dtd.parse(
            "<!DOCTYPE HealthData [\n"
            "<!ELEMENT HealthData (Node*)>\n"
            "<!ELEMENT Node (Node*)>\n"
            "]>"
        )
        self.assertEqual(schema.reachable_elements(), {"HealthData", "Node"})


class VersionGateTest(unittest.TestCase):
    def test_rejects_versions_below_fourteen(self):
        schema = dtd.parse(
            dtd.bundled_text().replace(
                "HealthKit Export Version: 14", "HealthKit Export Version: 9"
            )
        )
        with self.assertRaises(dtd.SchemaError) as caught:
            schema.require_supported()
        message = str(caught.exception)
        self.assertIn("9", message)
        self.assertIn("14", message)

    def test_accepts_newer_versions(self):
        schema = dtd.parse(
            dtd.bundled_text().replace(
                "HealthKit Export Version: 14", "HealthKit Export Version: 17"
            )
        )
        self.assertEqual(schema.require_supported(), 17)

    def test_missing_version_comment(self):
        schema = dtd.parse("<!DOCTYPE HealthData [\n<!ELEMENT HealthData ANY>\n]>")
        with self.assertRaises(dtd.SchemaError):
            schema.require_supported()


class ExtractTest(unittest.TestCase):
    def test_reads_the_dtd_back_out_of_a_synthetic_export(self):
        export = support.sample_export(days=2)
        with ExportArchive(export) as archive, archive.open_export() as stream:
            extracted = dtd.extract(stream)
        self.assertEqual(extracted.strip(), dtd.bundled_text().strip())

    def test_export_without_a_doctype_is_reported_clearly(self):
        path = support.workspace("nodtd") / "Export.zip"
        import zipfile

        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "apple_health_export/Export.xml", '<?xml version="1.0"?><HealthData/>'
            )
        with (
            ExportArchive(path) as archive,
            archive.open_export() as stream,
            self.assertRaises(dtd.SchemaError),
        ):
            dtd.extract(stream)


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.bundled = dtd.bundled()

    def test_identical_schemas_have_no_differences(self):
        self.assertEqual(dtd.diff(self.bundled, dtd.bundled()), [])

    def test_detects_a_new_attribute(self):
        theirs = dtd.parse(
            dtd.bundled_text().replace(
                "  unit          CDATA #IMPLIED",
                "  unit          CDATA #IMPLIED\n  brandNew      CDATA #IMPLIED",
                1,
            )
        )
        self.assertIn("+ attribute Record/@brandNew", dtd.diff(self.bundled, theirs))

    def test_detects_a_removed_attribute(self):
        theirs = dtd.parse(
            dtd.bundled_text().replace("  unit          CDATA #IMPLIED\n", "", 1)
        )
        self.assertIn("- attribute Record/@unit", dtd.diff(self.bundled, theirs))

    def test_detects_a_new_element(self):
        theirs = dtd.parse(
            dtd.bundled_text().replace(
                "<!ELEMENT FileReference EMPTY>",
                "<!ELEMENT FileReference EMPTY>\n<!ELEMENT BrandNew EMPTY>",
                1,
            )
        )
        differences = dtd.diff(self.bundled, theirs)
        self.assertTrue(any("+ element BrandNew" in line for line in differences))

    def test_reports_a_version_change(self):
        theirs = dtd.parse(
            dtd.bundled_text().replace(
                "HealthKit Export Version: 14", "HealthKit Export Version: 15"
            )
        )
        differences = dtd.diff(self.bundled, theirs)
        self.assertTrue(any("export version" in line for line in differences))


if __name__ == "__main__":
    unittest.main()
