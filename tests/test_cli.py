"""The command line, driven the way a user drives it."""

from __future__ import annotations

import contextlib
import io
import sqlite3
import subprocess
import sys
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from apple_health_to_sqlite import cli, db, layout

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
        self.assertIn("export version 17", result.stdout)
        self.assertIn("records", result.stdout)
        self.assertGreater(
            support.one(directory / "health.db", "select count(*) from records"), 0
        )

    def test_quiet_suppresses_the_summary(self):
        directory = support.workspace("cli")
        result = run("import", str(self.export), str(directory), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_quiet_says_nothing_about_a_clean_import(self):
        directory = support.workspace("cli")
        result = run("import", str(self.export), str(directory), "--quiet")
        self.assertEqual(result.stderr, "")

    def test_quiet_still_reports_what_was_not_imported(self):
        export = support.write_export('<SomethingNew a="1"/>\n')
        directory = support.workspace("cli")
        result = run("import", str(export), str(directory), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("SomethingNew", result.stderr)

    def test_the_summary_counts_files_that_were_not_imported(self):
        export = support.write_export("")
        result = run("import", str(export), str(support.workspace("cli")))
        self.assertIn("not imported", result.stdout)
        self.assertIn("export_cda.xml", result.stdout)

    def test_quiet_does_not_list_files_every_export_has(self):
        export = support.write_export("")
        result = run("import", str(export), str(support.workspace("cli")), "--quiet")
        self.assertEqual(result.stderr, "")

    def test_quiet_still_reports_a_different_schema(self):
        export = support.write_export("", version=99)
        directory = support.workspace("cli")
        result = run("import", str(export), str(directory), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("schema-diff", result.stderr)

    def test_the_summary_still_reports_what_was_not_imported(self):
        export = support.write_export('<SomethingNew a="1"/>\n')
        directory = support.workspace("cli")
        result = run("import", str(export), str(directory))
        self.assertIn("SomethingNew", result.stdout)

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

    def test_a_mistyped_since_date_stops_before_anything_is_written(self):
        directory = support.workspace("cli") / "new"
        result = run("import", str(self.export), str(directory), "--since", "yesterday")
        self.assertEqual(result.returncode, 1)
        self.assertIn("YYYY-MM-DD", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(directory.exists())

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
        self.assertIn("HealthKit Export Version: 17", result.stdout)

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


def damage_first_member(path: Path) -> None:
    """Flip bytes in the middle of the first compressed file of a zip."""
    with zipfile.ZipFile(path) as archive:
        info = archive.infolist()[0]
    data = bytearray(path.read_bytes())
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    middle = start + info.compress_size // 2
    data[middle] ^= 0xFF
    data[middle + 1] ^= 0xFF
    path.write_bytes(bytes(data))


class FailureTest(unittest.TestCase):
    """What goes wrong on a real machine ends in a sentence, not a traceback."""

    def assert_explained(self, result, *expected):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(result.stderr.startswith("error: "), result.stderr)
        for text in expected:
            self.assertIn(text, result.stderr)

    def test_a_truncated_export(self):
        export = support.workspace("cli") / "Export.xml"
        export.write_text(support.wrap_body("")[:-20], encoding="utf-8")
        result = run("import", str(export), str(support.workspace("cli")))
        self.assert_explained(result, "Export.xml", "damaged")

    def test_a_damaged_zip(self):
        export = support.write_export("")
        damage_first_member(export)
        result = run("import", str(export), str(support.workspace("cli")))
        self.assert_explained(result, "damaged")

    def test_a_damaged_zip_with_the_schema_commands(self):
        export = support.write_export("")
        damage_first_member(export)
        for command in ("schema-diff", "extract-schema"):
            with self.subTest(command=command):
                self.assert_explained(run(command, str(export)), "damaged")

    def test_a_damaged_export_changes_nothing_in_the_database(self):
        directory = support.workspace("cli")
        run("import", str(support.export_from_fixture("workouts")), str(directory))
        database = directory / "health.db"
        before = support.one(database, "select count(*) from records")

        # More than one batch, so that rows have reached SQLite before the damage
        # shows, and only the transaction can keep them out.
        body = "".join(
            '<Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" '
            f'startDate="2026-03-01 08:00:00 +0100" value="{index}" '
            'endDate="2026-03-01 08:10:00 +0100"/>\n'
            for index in range(db.BATCH_SIZE + 1)
        )
        broken = support.workspace("cli") / "Export.xml"
        broken.write_text(support.wrap_body(body)[:-20], encoding="utf-8")
        result = run("import", str(broken), str(directory))

        self.assert_explained(result, "damaged")
        self.assertEqual(support.one(database, "select count(*) from records"), before)

    def test_a_damaged_route_is_named_and_the_rest_is_imported(self):
        directory = support.workspace("cli")
        export = support.export_from_fixture(
            "workouts", routes={"route_a.gpx": "<gpx><trk>"}
        )
        result = run("import", str(export), str(directory))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("damaged", result.stdout)
        self.assertIn("route_a.gpx", result.stdout)
        self.assertGreater(
            support.one(directory / "health.db", "select count(*) from workouts"), 0
        )
        self.assertEqual(
            support.one(
                directory / "health.db",
                "select count(*) from sqlite_master where name = 'daily_metrics'",
            ),
            1,
            "the run went on to build the derived tables",
        )

    def test_quiet_still_names_a_damaged_route(self):
        export = support.export_from_fixture(
            "workouts", routes={"route_a.gpx": "<gpx><trk>"}
        )
        result = run("import", str(export), str(support.workspace("cli")), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("route_a.gpx", result.stderr)

    def test_quiet_still_names_a_missing_route(self):
        export = support.export_from_fixture("workouts")
        result = run("import", str(export), str(support.workspace("cli")), "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("route_a.gpx", result.stderr)

    def test_the_import_works_again_after_a_failed_one(self):
        directory = support.workspace("cli")
        broken = support.workspace("cli") / "Export.xml"
        broken.write_text(support.wrap_body("")[:-20], encoding="utf-8")
        run("import", str(broken), str(directory))

        result = run(
            "import", str(support.export_from_fixture("workouts")), str(directory)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(
            support.one(directory / "health.db", "select count(*) from workouts"), 0
        )

    def test_a_database_with_another_layout(self):
        directory = support.workspace("cli")
        connection = sqlite3.connect(directory / "health.db")
        connection.execute("create table records (id integer primary key, type text)")
        connection.commit()
        connection.close()
        export = support.write_export(
            '<Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" '
            'startDate="2026-03-01 08:00:00 +0100" '
            'endDate="2026-03-01 08:10:00 +0100" value="1"/>\n'
        )
        result = run("import", str(export), str(directory))
        self.assert_explained(result, "health.db", "new directory")

    def test_a_database_from_a_newer_version(self):
        directory = support.workspace("cli")
        export = support.write_export("")
        run("import", str(export), str(directory), "--quiet")
        connection = sqlite3.connect(directory / "health.db")
        connection.execute(f"pragma user_version = {db.SCHEMA_VERSION + 1}")
        connection.close()

        result = run("import", str(export), str(directory))
        self.assert_explained(result, "health.db", "newer version")

    def test_a_file_that_is_not_a_database(self):
        directory = support.workspace("cli")
        (directory / "health.db").write_text("not a database, sorry\n" * 100)
        result = run("import", str(support.write_export("")), str(directory))
        self.assert_explained(result, "health.db", "new directory")

    def test_a_target_that_cannot_be_written(self):
        directory = support.workspace("cli")
        export = support.write_export("")
        run("import", str(export), str(directory), "--quiet")
        (directory / "metadata.yml").unlink()
        (directory / "metadata.yml").mkdir()
        self.assert_explained(
            run("import", str(export), str(directory)), "metadata.yml"
        )


class InterruptTest(unittest.TestCase):
    def test_ctrl_c_ends_in_a_sentence_and_the_usual_status(self):
        def interrupted(_arguments):
            raise KeyboardInterrupt

        errors = io.StringIO()
        with (
            mock.patch.dict(cli.COMMANDS, {"schema-diff": interrupted}),
            contextlib.redirect_stderr(errors),
        ):
            status = cli.main(["schema-diff", "Export.zip"])

        self.assertEqual(status, 130)
        self.assertIn("Interrupted", errors.getvalue())


class ParserTest(unittest.TestCase):
    def test_paths_can_follow_the_type_lists_after_a_double_dash(self):
        directory = support.workspace("cli")
        result = run(
            "import",
            "--quiet",
            "--types",
            "StepCount",
            "HeartRate",
            "--",
            str(support.sample_export(days=4)),
            str(directory),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {
                row[0]
                for row in support.rows(
                    directory / "health.db", "select distinct type_short from records"
                )
            },
            {"StepCount", "HeartRate"},
        )

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
