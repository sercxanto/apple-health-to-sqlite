"""The Datasette metadata is only useful if its queries still run.

Canned queries are the part of the project most likely to rot: they name
columns and views that live in the importer's schema, and nothing else checks
them. Here Datasette itself loads the metadata (which also proves the YAML is
valid and that Datasette accepts the shape), and every query it reports is then
executed against a real database.

Skipped when Datasette is not installed, so the fast suite still runs on a bare
checkout.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from applehealth import layout

from . import support

# The same directory `import` copies from, so this tests what is actually shipped.
CONFIG_DIRECTORY = layout.config_dir()
METADATA = CONFIG_DIRECTORY / "metadata.yml"
SETTINGS = CONFIG_DIRECTORY / "settings.json"
DATASETTE = shutil.which("datasette")
ROUTE_POINTS = [(48.10, 11.50), (48.11, 11.51), (48.12, 11.52)]

# Queries whose empty result is the right answer rather than a broken query.
# days_without_data reports gaps in the history and the synthesizer writes every
# day, so nothing is a correct answer here; test_days_without_data_finds_a_gap
# makes a gap and checks it there instead.
MAY_BE_EMPTY = {"days_without_data"}


def load_queries(database: Path) -> list[dict]:
    """Ask Datasette which canned queries it sees for this database."""
    result = subprocess.run(
        [
            DATASETTE,
            str(database),
            "-m",
            str(METADATA),
            "--get",
            f"/{database.stem}.json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["queries"]


@unittest.skipIf(DATASETTE is None, "datasette is not installed")
class MetadataQueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Datasette derives the database name from the filename, and the
        # metadata is keyed on "health".
        source = support.sample_database(days=14)
        cls.database = support.workspace("datasette") / "health.db"
        shutil.copy(source, cls.database)
        cls.queries = load_queries(cls.database)
        cls.values = {
            "day": support.one(
                cls.database,
                "select local_date from records where type_short = 'HeartRate' limit 1",
            ),
            "metric": "StepCount",
        }

    @classmethod
    def bindings(cls, sql: str) -> dict[str, str]:
        """The sample values a query needs, keyed by the parameters it names.

        Datasette turns `:day` into an input field; the tests have to fill the
        same fields in. Deriving them from the SQL keeps a query that names a
        new parameter from quietly slipping past the checks below.
        """
        return {name: value for name, value in cls.values.items() if f":{name}" in sql}

    @classmethod
    def query(cls, name: str) -> str:
        """The SQL Datasette reports for one canned query, by name."""
        return next(q["sql"] for q in cls.queries if q["name"] == name)

    def test_datasette_loads_the_metadata(self):
        self.assertGreater(len(self.queries), 5)

    def test_every_query_runs_against_a_real_database(self):
        connection = sqlite3.connect(self.database)
        try:
            for query in self.queries:
                with self.subTest(query=query["name"]):
                    connection.execute(
                        query["sql"], self.bindings(query["sql"])
                    ).fetchall()
        finally:
            connection.close()

    def test_queries_without_parameters_return_rows(self):
        connection = sqlite3.connect(self.database)
        try:
            for query in self.queries:
                if self.bindings(query["sql"]) or query["name"] in MAY_BE_EMPTY:
                    continue
                with self.subTest(query=query["name"]):
                    rows = connection.execute(query["sql"]).fetchall()
                    self.assertGreater(
                        len(rows), 0, "query returned nothing on sample data"
                    )
        finally:
            connection.close()

    def test_parameterised_queries_return_rows_when_given_a_parameter(self):
        connection = sqlite3.connect(self.database)
        try:
            for query in self.queries:
                parameters = self.bindings(query["sql"])
                if not parameters or query["name"] in MAY_BE_EMPTY:
                    continue
                with self.subTest(query=query["name"]):
                    rows = connection.execute(query["sql"], parameters).fetchall()
                    self.assertGreater(len(rows), 0)
        finally:
            connection.close()

    def test_days_without_data_finds_a_gap(self):
        """A day with no samples and a day with a zero look the same in a chart,
        so the query has to name the missing one. The synthesizer writes every
        day; the gap is made here.
        """
        database = support.workspace("gap") / "health.db"
        shutil.copy(self.database, database)
        connection = sqlite3.connect(database)
        try:
            skipped = support.one(
                database,
                "select local_date from daily_metrics where type_short = 'StepCount' "
                "order by local_date limit 1 offset 3",
            )
            connection.execute(
                "delete from daily_metrics "
                "where type_short = 'StepCount' and local_date = ?",
                (skipped,),
            )
            rows = connection.execute(
                self.query("days_without_data"), {"metric": "StepCount"}
            ).fetchall()
            self.assertEqual([row[0] for row in rows], [skipped])
        finally:
            connection.close()

    def test_every_parameter_a_query_uses_has_a_sample_value(self):
        """A query naming a parameter this test does not know would otherwise
        be silently skipped rather than checked."""
        for query in self.queries:
            with self.subTest(query=query["name"]):
                named = set(re.findall(r":([a-z_]+)", query["sql"]))
                self.assertLessEqual(named, set(self.values))

    def test_serving_a_query_over_http_works(self):
        """One end-to-end check that the whole path really is wired up."""
        result = subprocess.run(
            [
                DATASETTE,
                str(self.database),
                "-m",
                str(METADATA),
                "--get",
                "/health/steps_per_day.json?_shape=array",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        self.assertGreater(len(payload), 0)
        self.assertIn("steps", payload[0])

    def route_query(self) -> str:
        return next(q for q in self.queries if q["name"] == "route_of_a_workout")["sql"]

    def test_map_query_returns_coordinates(self):
        """datasette-cluster-map keys off columns named latitude/longitude.

        Asked for every day that has a route, not for one of them: the days
        that go wrong are the ones whose first workout is the indoor one.
        """
        connection = sqlite3.connect(self.database)
        try:
            days = [
                day
                for (day,) in connection.execute(
                    "select distinct local_date from workouts where n_points > 0"
                )
            ]
            self.assertGreater(len(days), 0)
            for day in days:
                with self.subTest(day=day):
                    cursor = connection.execute(self.route_query(), {"day": day})
                    columns = [column[0] for column in cursor.description]
                    self.assertGreater(len(cursor.fetchall()), 0)
                    self.assertIn("latitude", columns)
                    self.assertIn("longitude", columns)
        finally:
            connection.close()

    def test_map_query_finds_a_route_behind_an_earlier_routeless_workout(self):
        """The shape that made this query look broken: an indoor workout in the
        morning, the run with the GPS track in the evening. Asking for the
        day's *first* workout shows an empty map for a day that has a route."""
        database, _ = support.import_export(
            support.export_from_fixture(
                "workouts", routes={"route_a.gpx": support.gpx(ROUTE_POINTS)}
            )
        )
        connection = sqlite3.connect(database)
        try:
            rows = connection.execute(
                self.route_query(), {"day": "2026-08-01"}
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(rows), len(ROUTE_POINTS))


@unittest.skipIf(DATASETTE is None, "datasette is not installed")
class ImportedDirectoryTest(unittest.TestCase):
    """What `import` leaves behind opens with nothing but `datasette DIRECTORY`."""

    def test_canned_queries_and_settings_apply_without_further_arguments(self):
        directory = support.workspace("imported") / "mine"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "applehealth",
                "import",
                str(support.sample_export(days=6)),
                str(directory),
                "--quiet",
            ],
            check=True,
        )

        def get(path: str):
            result = subprocess.run(
                [DATASETTE, str(directory), "--get", path],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(result.stdout)

        self.assertGreater(len(get("/health/steps_per_day.json?_shape=array")), 0)
        self.assertEqual(
            get("/-/settings.json")["sql_time_limit_ms"],
            json.loads(SETTINGS.read_text())["sql_time_limit_ms"],
        )


@unittest.skipIf(DATASETTE is None, "datasette is not installed")
class SettingsTest(unittest.TestCase):
    """Settings are not metadata, and getting that wrong fails silently."""

    @classmethod
    def setUpClass(cls):
        cls.directory = support.workspace("configdir")
        shutil.copy(METADATA, cls.directory / "metadata.yml")
        shutil.copy(SETTINGS, cls.directory / "settings.json")
        shutil.copy(support.sample_database(days=6), cls.directory / "health.db")

    def effective_settings(self) -> dict:
        result = subprocess.run(
            [DATASETTE, str(self.directory), "--get", "/-/settings.json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_configuration_directory_applies_settings_json(self):
        self.assertEqual(
            self.effective_settings()["sql_time_limit_ms"],
            json.loads(SETTINGS.read_text())["sql_time_limit_ms"],
        )

    def test_the_time_limit_is_raised_above_the_default(self):
        self.assertGreater(self.effective_settings()["sql_time_limit_ms"], 1000)

    def test_configuration_directory_also_applies_the_metadata(self):
        result = subprocess.run(
            [
                DATASETTE,
                str(self.directory),
                "--get",
                "/health/steps_per_day.json?_shape=array",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertGreater(len(json.loads(result.stdout)), 0)

    def test_settings_inside_metadata_are_ignored(self):
        """The trap this configuration exists to avoid: Datasette accepts a
        `settings:` block in metadata.yml and does nothing with it."""
        directory = support.workspace("badconfig")
        shutil.copy(support.sample_database(days=6), directory / "health.db")
        metadata = directory / "metadata.yml"
        metadata.write_text(
            METADATA.read_text() + "\nsettings:\n  sql_time_limit_ms: 9000\n"
        )
        result = subprocess.run(
            [
                DATASETTE,
                str(directory / "health.db"),
                "-m",
                str(metadata),
                "--get",
                "/-/settings.json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["sql_time_limit_ms"], 1000)


if __name__ == "__main__":
    unittest.main()
