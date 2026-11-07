"""The bundled DTD is the project's source of truth, so it is held to it."""

from __future__ import annotations

import unittest

from apple_health_to_sqlite import dtd, importer
from apple_health_to_sqlite.archive import ExportArchive

from . import support


class BundledSchemaTest(unittest.TestCase):
    def setUp(self):
        self.schema = dtd.bundled()

    def test_parses_and_declares_the_expected_version(self):
        self.assertEqual(self.schema.version, 17)
        self.assertEqual(self.schema.require_supported(), 17)

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

    def test_every_declared_nesting_is_read_by_a_handler(self):
        """Having a handler somewhere is not enough. `Workout` gained `Record`
        as a child in version 17, and the test above could not notice, because
        `Record` has a handler at the top level."""
        declared = {
            name: element.children()
            for name, element in self.schema.elements.items()
            if element.children()
        }
        self.assertEqual(declared, {**importer.CHILD_ELEMENTS})

    def test_every_declared_element_is_reachable(self):
        """Up to version 14 the DTD declared WorkoutActivity and then referenced
        it from no content model. Pinned down here so that an update bringing
        back such an element is noticed rather than absorbed."""
        self.assertEqual(
            set(self.schema.elements) - self.schema.reachable_elements(), set()
        )

    def test_correlation_children_are_documented_as_duplicated(self):
        """The comment drives real behaviour in the importer, so losing it in a
        schema refresh should be visible."""
        self.assertIn(
            "also appear as top-level records",
            self.schema.text,
        )

    def test_workout_records_are_documented_as_duplicated(self):
        """The same for a Workout's records, and for the same reason."""
        self.assertIn(
            "children of a Workout also appear as top-level records",
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
                "HealthKit Export Version: 17", "HealthKit Export Version: 9"
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
                "HealthKit Export Version: 17", "HealthKit Export Version: 18"
            )
        )
        self.assertEqual(schema.require_supported(), 18)

    def test_accepts_the_oldest_supported_version(self):
        schema = dtd.parse(
            dtd.bundled_text().replace(
                "HealthKit Export Version: 17", "HealthKit Export Version: 14"
            )
        )
        self.assertEqual(schema.require_supported(), 14)

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

    def test_a_new_element_comes_with_its_whole_declaration(self):
        """The name alone is not enough to fold it into the bundled schema."""
        theirs = dtd.parse(
            dtd.bundled_text().replace(
                "<!ELEMENT FileReference EMPTY>",
                "<!ELEMENT BrandNew ((Inner)*)>\n"
                "<!ATTLIST BrandNew\n  first CDATA #REQUIRED\n"
                "  second CDATA #IMPLIED\n>\n"
                "<!ELEMENT FileReference EMPTY>",
                1,
            )
        )
        line = next(
            line for line in dtd.diff(self.bundled, theirs) if "BrandNew" in line
        )
        self.assertIn("((Inner)*)", line)
        self.assertIn("first, second", line)

    def test_reports_a_version_change(self):
        theirs = dtd.parse(
            dtd.bundled_text().replace(
                "HealthKit Export Version: 17", "HealthKit Export Version: 15"
            )
        )
        differences = dtd.diff(self.bundled, theirs)
        self.assertTrue(any("export version" in line for line in differences))


class EveryAttributeReachesTheDatabaseTest(unittest.TestCase):
    """An element with a handler can still lose an attribute without a word.

    The document below holds every element wherever the schema allows it, with a
    value of its own in every attribute. Each value has to turn up in the
    database. They are numbers, because many columns only take numbers, and all
    the same length, so that none can be mistaken for part of another.
    """

    def build(self, schema, name: str, path: str, values: dict[str, str]) -> str:
        element = schema.elements[name]
        attributes = ""
        for attribute in sorted(element.attributes):
            value = f"{1_000_000 + len(values)}.5"
            values[f"{path}/@{attribute}"] = value
            attributes += f' {attribute}="{value}"'
        children = "".join(
            self.build(schema, child, f"{path}/{child}", values)
            for child in sorted(element.children())
        )
        return f"<{name}{attributes}>{children}</{name}>\n"

    def test_every_declared_attribute_arrives_wherever_it_can_occur(self):
        values: dict[str, str] = {}
        body = self.build(dtd.bundled(), "HealthData", "HealthData", values)
        export = support.workspace("export") / "Export.xml"
        export.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n' + dtd.bundled_text() + body,
            encoding="utf-8",
        )

        database, result = support.import_export(export)

        self.assertEqual(result.unhandled, {})
        self.assertGreater(len(values), 150, "the document should cover the schema")
        tables = support.rows(
            database, "select name from sqlite_master where type = 'table'"
        )
        stored = "\n".join(
            str(row)
            for (table,) in tables
            for row in support.rows(database, f"select * from {table}")
        )
        missing = sorted(path for path, value in values.items() if value not in stored)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
