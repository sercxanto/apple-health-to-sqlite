"""The command line, driven the way a user drives it."""

from __future__ import annotations

import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from apple_health_to_sqlite import layout

from . import support


def run(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "apple_health_to_sqlite", *arguments],
        capture_output=True,
        text=True,
    )


class CommandNameTest(unittest.TestCase):
    def test_the_command_is_installed_under_the_package_name_only(self):
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        project = tomllib.loads(pyproject.read_text("utf-8"))["project"]
        self.assertEqual(
            project["scripts"],
            {project["name"]: "apple_health_to_sqlite.cli:main"},
        )

    def test_help_uses_the_name_people_type(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("usage: apple-health-to-sqlite"))


class SynthesizeCommandTest(unittest.TestCase):
    def test_writes_an_export_and_reports_it(self):
        output = support.workspace("cli") / "Export.zip"
        result = run(
            "synthesize", "--days", "3", "--end-date", "2026-08-14", str(output)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.exists())
        self.assertIn("2026-08-12 to 2026-08-14", result.stdout)

    def test_quiet_says_nothing(self):
        output = support.workspace("cli") / "Export.zip"
        result = run("synthesize", "--days", "2", "--quiet", str(output))
        self.assertEqual(result.stdout, "")


class ImportCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = support.sample_export(days=4)

    def test_imports_and_prints_a_summary(self):
        directory = support.workspace("cli")
        result = run("import", str(self.export), str(directory))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export version 14", result.stdout)
        self.assertIn("records", result.stdout)
        self.assertGreater(
            support.one(directory / "health.db", "select count(*) from records"), 0
        )

    def test_quiet_suppresses_the_summary(self):
        directory = support.workspace("cli")
        result = run("import", str(self.export), str(directory), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_filters_are_wired_through(self):
        directory = support.workspace("cli")
        result = run(
            "import",
            str(self.export),
            str(directory),
            "--types",
            "StepCount",
            "--quiet",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {
                row[0]
                for row in support.rows(
                    directory / "health.db", "select distinct type_short from records"
                )
            },
            {"StepCount"},
        )

    def test_missing_export_fails_with_a_message_not_a_traceback(self):
        directory = support.workspace("cli") / "mine"
        result = run("import", "/nope/Export.zip", str(directory))
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(directory.exists())


class ImportDirectoryTest(unittest.TestCase):
    """`import` leaves a directory that `datasette DIRECTORY` can open as it is."""

    @classmethod
    def setUpClass(cls):
        cls.export = support.sample_export(days=2)

    def test_creates_the_directory_with_database_and_configuration(self):
        directory = support.workspace("cli") / "new" / "mine"
        result = run("import", str(self.export), str(directory), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((directory / layout.DATABASE_NAME).exists())
        for name in layout.CONFIG_FILES:
            self.assertEqual(
                (directory / name).read_bytes(),
                (layout.config_dir() / name).read_bytes(),
                name,
            )

    def test_importing_again_refreshes_the_configuration_and_keeps_the_rows(self):
        directory = support.workspace("cli")
        run("import", str(self.export), str(directory), "--quiet")
        database = directory / layout.DATABASE_NAME
        records = support.one(database, "select count(*) from records")
        (directory / "metadata.yml").write_text("title: out of date\n")

        result = run("import", str(self.export), str(directory), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(support.one(database, "select count(*) from records"), records)
        self.assertEqual(
            (directory / "metadata.yml").read_bytes(),
            (layout.config_dir() / "metadata.yml").read_bytes(),
        )

    def test_the_summary_says_how_to_open_the_result(self):
        directory = support.workspace("cli")
        result = run("import", str(self.export), str(directory))
        self.assertIn(f"View it with: datasette {directory}\n", result.stdout)

    def test_the_summary_quotes_a_directory_with_spaces(self):
        directory = support.workspace("cli") / "my data"
        result = run("import", str(self.export), str(directory))
        self.assertIn(f'View it with: datasette "{directory}"\n', result.stdout)

    def test_an_existing_file_is_left_alone(self):
        target = support.workspace("cli") / "notes"
        target.write_text("mine\n")
        result = run("import", str(self.export), str(target))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(target.read_text(), "mine\n")

    def test_a_metadata_file_without_a_database_is_not_overwritten(self):
        directory = support.workspace("cli")
        (directory / "metadata.yml").write_text("somebody else's\n")
        result = run("import", str(self.export), str(directory))
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(directory / "metadata.yml"), result.stderr)
        self.assertEqual((directory / "metadata.yml").read_text(), "somebody else's\n")
        self.assertFalse((directory / layout.DATABASE_NAME).exists())

    def test_a_failed_import_leaves_no_configuration_behind(self):
        directory = support.workspace("cli")
        run("import", "/nope/Export.zip", str(directory))
        self.assertEqual(list(directory.iterdir()), [])


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


class HelpTest(unittest.TestCase):
    def test_import_says_what_it_puts_in_the_directory(self):
        result = run("import", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in (layout.DATABASE_NAME, *layout.CONFIG_FILES):
            self.assertIn(name, result.stdout)


class ImportTargetTest(unittest.TestCase):
    """The rules themselves, in this process, where coverage can see them."""

    def test_a_new_directory_is_accepted(self):
        layout.check_target(support.workspace("layout") / "new")

    def test_a_directory_of_an_earlier_import_is_accepted(self):
        directory = support.workspace("layout")
        (directory / layout.DATABASE_NAME).touch()
        (directory / "metadata.yml").touch()
        layout.check_target(directory)

    def test_a_directory_with_unrelated_files_is_accepted(self):
        """Such as the export itself: `import mine/Export.zip mine/`."""
        directory = support.workspace("layout")
        (directory / "Export.zip").touch()
        layout.check_target(directory)

    def test_an_existing_file_is_refused(self):
        file = support.workspace("layout") / "notes"
        file.touch()
        with self.assertRaises(layout.UnusableDirectory):
            layout.check_target(file)

    def test_settings_without_a_database_are_refused(self):
        directory = support.workspace("layout")
        (directory / "settings.json").touch()
        with self.assertRaises(layout.UnusableDirectory) as raised:
            layout.check_target(directory)
        self.assertIn("settings.json", str(raised.exception))

    def test_the_view_command_quotes_only_what_needs_it(self):
        self.assertEqual(layout.view_command("mine"), "datasette mine")
        self.assertEqual(layout.view_command("my data"), 'datasette "my data"')


class ConfigurationTest(unittest.TestCase):
    def test_the_bundled_configuration_ships_with_the_package(self):
        """Inside the package, so `import` works from an installed wheel too."""
        directory = layout.config_dir()
        self.assertEqual(directory.parent.name, "apple_health_to_sqlite")
        for name in layout.CONFIG_FILES:
            self.assertTrue((directory / name).exists(), name)

    def test_prepare_refreshes_the_configuration(self):
        target = support.workspace("layout") / "serving"
        (stale := target / "metadata.yml").parent.mkdir(parents=True)
        stale.write_text("title: out of date\n")

        layout.copy_configuration(target)
        self.assertEqual(
            stale.read_text(), (layout.config_dir() / "metadata.yml").read_text()
        )


class ParserTest(unittest.TestCase):
    def test_no_command_fails(self):
        result = run()
        self.assertNotEqual(result.returncode, 0)

    def test_version_flag(self):
        result = run("--version")
        self.assertEqual(result.returncode, 0)


class DefaultEncodingTest(unittest.TestCase):
    def test_an_import_names_every_encoding_it_reads_and_writes(self):
        """The default is UTF-8 on Linux and macOS, usually cp1252 on Windows.

        So a forgotten `encoding=` passes everywhere it is likely to be tried
        and only garbles text on Windows, and only once a file holds something
        outside ASCII. Python can flag the call itself, on any platform.
        """
        export = support.workspace("encoding") / "Export.zip"
        synthesize = run("synthesize", "--days", "2", "--quiet", str(export))
        self.assertEqual(synthesize.returncode, 0, synthesize.stderr)

        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "warn_default_encoding",
                "-W",
                "error::EncodingWarning",
                "-m",
                "apple_health_to_sqlite",
                "import",
                "--quiet",
                str(export),
                str(export.parent / "output"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
