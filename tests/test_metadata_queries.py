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
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path

from . import support

CONFIG_DIRECTORY = Path(__file__).resolve().parent.parent / "datasette"
METADATA = CONFIG_DIRECTORY / "metadata.yml"
SETTINGS = CONFIG_DIRECTORY / "settings.json"
DATASETTE = shutil.which("datasette")


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
        cls.day = support.one(
            cls.database,
            "select local_date from records where type_short = 'HeartRate' limit 1",
        )

    def test_datasette_loads_the_metadata(self):
        self.assertGreater(len(self.queries), 5)

    def test_every_query_runs_against_a_real_database(self):
        connection = sqlite3.connect(self.database)
        try:
            for query in self.queries:
                with self.subTest(query=query["name"]):
                    parameters = {"day": self.day} if ":day" in query["sql"] else {}
                    connection.execute(query["sql"], parameters).fetchall()
        finally:
            connection.close()

    def test_queries_without_parameters_return_rows(self):
        connection = sqlite3.connect(self.database)
        try:
            for query in self.queries:
                if ":day" in query["sql"]:
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
                if ":day" not in query["sql"]:
                    continue
                with self.subTest(query=query["name"]):
                    rows = connection.execute(
                        query["sql"], {"day": self.day}
                    ).fetchall()
                    self.assertGreater(len(rows), 0)
        finally:
            connection.close()

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

    def test_map_query_returns_coordinates(self):
        """datasette-cluster-map keys off columns named latitude/longitude."""
        connection = sqlite3.connect(self.database)
        try:
            day = connection.execute(
                "select local_date from workouts where n_points > 0 limit 1"
            ).fetchone()[0]
            query = next(q for q in self.queries if q["name"] == "route_of_a_workout")
            rows = connection.execute(query["sql"], {"day": day}).fetchall()
        finally:
            connection.close()
        self.assertGreater(len(rows), 0)


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
