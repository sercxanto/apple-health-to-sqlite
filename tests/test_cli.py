"""The command line, driven the way a user drives it."""

from __future__ import annotations

import subprocess
import sys
import unittest

from . import support


def run(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "applehealth", *arguments],
        capture_output=True,
        text=True,
    )


class GenerateCommandTest(unittest.TestCase):
    def test_writes_an_export_and_reports_it(self):
        output = support.workspace("cli") / "Export.zip"
        result = run("generate", "--days", "3", "--end-date", "2026-08-14", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.exists())
        self.assertIn("2026-08-12 to 2026-08-14", result.stdout)

    def test_quiet_says_nothing(self):
        output = support.workspace("cli") / "Export.zip"
        result = run("generate", "--days", "2", "--quiet", str(output))
        self.assertEqual(result.stdout, "")


class ImportCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = support.sample_export(days=4)

    def test_imports_and_prints_a_summary(self):
        database = support.workspace("cli") / "health.db"
        result = run("import", str(self.export), str(database))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export version 14", result.stdout)
        self.assertIn("records", result.stdout)
        self.assertGreater(support.one(database, "select count(*) from records"), 0)

    def test_quiet_suppresses_the_summary(self):
        database = support.workspace("cli") / "health.db"
        result = run("import", str(self.export), str(database), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_filters_are_wired_through(self):
        database = support.workspace("cli") / "health.db"
        result = run(
            "import", str(self.export), str(database), "--types", "StepCount", "--quiet"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {
                row[0]
                for row in support.rows(
                    database, "select distinct type_short from records"
                )
            },
            {"StepCount"},
        )

    def test_missing_export_fails_with_a_message_not_a_traceback(self):
        result = run("import", "/nope/Export.zip", "/tmp/unused.db")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class SchemaCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = support.sample_export(days=2)

    def test_extract_schema_prints_the_dtd(self):
        result = run("extract-schema", str(self.export))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<!DOCTYPE HealthData", result.stdout)
        self.assertIn("HealthKit Export Version: 14", result.stdout)

    def test_extract_schema_writes_a_file(self):
        target = support.workspace("cli") / "schema.dtd"
        result = run("extract-schema", str(self.export), "-o", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<!ELEMENT Record", target.read_text())

    def test_schema_diff_reports_a_match(self):
        result = run("schema-diff", str(self.export))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No differences", result.stdout)

    def test_schema_diff_lists_differences_without_health_data(self):
        export = support.write_export(
            '<Record type="HKQuantityTypeIdentifierStepCount" unit="count"'
            ' value="987654" sourceName="iPhone" sourceVersion="17.4"'
            ' startDate="2026-08-01 08:00:00 +0200"'
            ' endDate="2026-08-01 09:00:00 +0200"/>\n'
        )
        # Add an attribute the bundled schema does not know about.
        import zipfile

        member = "apple_health_export/Export.xml"
        with zipfile.ZipFile(export) as archive:
            content = archive.read(member).decode()
        content = content.replace(
            "  unit          CDATA #IMPLIED",
            "  unit          CDATA #IMPLIED\n  futureField   CDATA #IMPLIED",
            1,
        )
        patched = support.workspace("cli") / "Export.zip"
        with zipfile.ZipFile(patched, "w") as archive:
            archive.writestr(member, content)

        result = run("schema-diff", str(patched))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("+ attribute Record/@futureField", result.stdout)
        # The point of the command: declarations only, never measurements.
        self.assertNotIn("987654", result.stdout)


class ParserTest(unittest.TestCase):
    def test_no_command_fails(self):
        result = run()
        self.assertNotEqual(result.returncode, 0)

    def test_version_flag(self):
        result = run("--version")
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
