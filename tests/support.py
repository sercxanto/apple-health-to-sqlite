"""Helpers shared by the tests.

Two ways to get test data:

* `write_export(body)` wraps a hand-written `<HealthData>` body in the bundled
  DTD and zips it. Use it for edge cases, where being able to read the exact
  input in the fixture file is worth more than realism.
* `sample_database()` synthesizes a realistic multi-day export and imports it.
  It is cached for the whole test run, because synthesizing and importing takes
  a moment and most tests only read from it.

No test writes outside its temporary directory, and none of them touch the
network.
"""

from __future__ import annotations

import atexit
import itertools
import sqlite3
import tempfile
import zipfile
from functools import cache
from pathlib import Path

from apple_health_to_sqlite import dtd, pipeline, synthetic
from apple_health_to_sqlite.importer import ImportOptions, ImportResult

FIXTURES = Path(__file__).parent / "fixtures"

_temporary = tempfile.TemporaryDirectory(prefix="apple-health-to-sqlite-tests-")
atexit.register(_temporary.cleanup)
WORKSPACE = Path(_temporary.name)
_counter = itertools.count()


def workspace(prefix: str = "case") -> Path:
    """A fresh directory inside the run's temporary workspace."""
    path = WORKSPACE / f"{prefix}-{next(_counter)}"
    path.mkdir(parents=True)
    return path


# -- building exports by hand ----------------------------------------------


def wrap_body(body: str, *, version: int | None = None, locale: str = "en_US") -> str:
    """Wrap a `<HealthData>` body in the header a real export has.

    `version` only changes what the header claims; the declarations stay those of
    the bundled schema.
    """
    schema = dtd.bundled_text()
    if version is not None:
        schema = schema.replace(
            f"HealthKit Export Version: {dtd.bundled().version}",
            f"HealthKit Export Version: {version}",
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"{schema}"
        f'<HealthData locale="{locale}">\n'
        ' <ExportDate value="2026-08-15 21:30:00 +0200"/>\n'
        ' <Me HKCharacteristicTypeIdentifierDateOfBirth="1979-04-12"'
        ' HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet"'
        ' HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet"'
        ' HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet"/>\n'
        f"{body}"
        "</HealthData>\n"
    )


def write_export(
    body: str,
    *,
    name: str = "Export.xml",
    version: int | None = None,
    routes: dict[str, str] | None = None,
    directory: str = "apple_health_export",
    as_directory: bool = False,
) -> Path:
    """Write a zip (or an unpacked directory) containing one export."""
    root = workspace("export")
    xml = wrap_body(body, version=version)

    if as_directory:
        target = root / directory
        (target / "workout-routes").mkdir(parents=True)
        (target / name).write_text(xml, encoding="utf-8")
        for route_name, content in (routes or {}).items():
            (target / "workout-routes" / route_name).write_text(
                content, encoding="utf-8"
            )
        return root

    path = root / "Export.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{directory}/{name}", xml)
        archive.writestr(
            f"{directory}/export_cda.xml",
            '<?xml version="1.0"?><ClinicalDocument xmlns="urn:hl7-org:v3"/>',
        )
        for route_name, content in (routes or {}).items():
            archive.writestr(f"{directory}/workout-routes/{route_name}", content)
    return path


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.xml").read_text(encoding="utf-8")


def export_from_fixture(name: str, **kwargs) -> Path:
    return write_export(fixture(name), **kwargs)


def gpx(
    points: list[tuple[float, float]], start_time: str = "2026-08-01T15:00:00Z"
) -> str:
    """A minimal GPX track, in the shape Apple writes them."""
    body = ""
    for index, (latitude, longitude) in enumerate(points):
        body += (
            f'    <trkpt lat="{latitude}" lon="{longitude}">\n'
            f"      <ele>{500 + index}</ele>\n"
            f"      <time>{start_time[:17]}{index:02d}Z</time>\n"
            f"      <extensions>\n"
            f"        <speed>{2.5 + index}</speed>\n"
            f"        <course>90.0</course>\n"
            f"        <hAcc>3.0</hAcc>\n"
            f"        <vAcc>4.0</vAcc>\n"
            f"      </extensions>\n"
            f"    </trkpt>\n"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="Apple Health Export" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <trk>\n    <trkseg>\n{body}    </trkseg>\n  </trk>\n</gpx>\n"
    )


# -- importing --------------------------------------------------------------


def import_export(
    export: Path, options: ImportOptions | None = None, database: Path | None = None
) -> tuple[Path, ImportResult]:
    """Import an export into a fresh (or given) database."""
    if database is None:
        database = workspace("db") / "health.db"
    result = pipeline.run_import(export, str(database), options)
    return database, result


def rows(database: Path, sql: str, parameters=()) -> list[tuple]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def one(database: Path, sql: str, parameters=()):
    """First column of the first row — for counts and single values."""
    result = rows(database, sql, parameters)
    return result[0][0] if result else None


# -- synthetic sample data (cached for the whole run) -----------------------


@cache
def sample_export(days: int = 14, seed: int = 7, end_date: str = "2026-08-14") -> Path:
    path = workspace("sample") / "Export.zip"
    synthetic.synthesize(
        path,
        synthetic.SynthesizeOptions(days=days, seed=seed, end_date=end_date),
    )
    return path


@cache
def sample_database(days: int = 14) -> Path:
    database, _ = import_export(sample_export(days=days))
    return database
