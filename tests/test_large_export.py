"""Slow test: the two properties that only show up at size.

Opt in with `APPLE_HEALTH_TO_SQLITE_SLOW_TESTS=1` (or `poe test-all`). It synthesizes a few
hundred thousand records, which takes a minute or two.

What it guards:

* **Memory stays flat.** The importer streams; if someone replaces `iterparse`
  with `parse`, or drops the `root.clear()`, this is what catches it — the
  functional tests would all still pass.
* **Idempotency holds at scale**, not just on a handful of rows.
"""

from __future__ import annotations

import os
import resource
import shutil
import sys
import unittest

from apple_health_to_sqlite import synthetic

from . import support

#: Peak resident memory allowed for the whole test process. The importer itself
#: sits around 50 MB regardless of export size; the headroom is for the Python
#: runtime and the test suite around it.
MEMORY_LIMIT_MB = 400

#: Budget per canned query on this database. See the test for how it was
#: chosen; it is a proxy for "does not scan `records`", not a latency target.
CANNED_QUERY_LIMIT_S = 0.2

SLOW_TESTS_ENABLED = os.environ.get("APPLE_HEALTH_TO_SQLITE_SLOW_TESTS") == "1"


def peak_memory_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.
    return usage / 1024 if sys.platform.startswith("linux") else usage / (1024 * 1024)


@unittest.skipUnless(
    SLOW_TESTS_ENABLED, "set APPLE_HEALTH_TO_SQLITE_SLOW_TESTS=1 to run"
)
class LargeExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = support.workspace("large") / "Export.zip"
        synthetic.synthesize(
            cls.export,
            synthetic.SynthesizeOptions(days=365, scale=2.0, end_date="2026-08-14"),
        )
        cls.database, cls.result = support.import_export(cls.export)

    def test_imported_a_lot_of_records(self):
        count = support.one(self.database, "select count(*) from records")
        self.assertGreater(count, 300_000)

    def test_memory_stayed_bounded(self):
        """The whole point of streaming the parse."""
        peak = peak_memory_mb()
        self.assertLess(
            peak,
            MEMORY_LIMIT_MB,
            f"peak resident memory {peak:.0f} MB — the importer is no longer "
            f"streaming, or is buffering rows it should be flushing",
        )

    def test_reimport_is_still_idempotent_at_scale(self):
        before = support.one(self.database, "select count(*) from records")
        support.import_export(self.export, database=self.database)
        self.assertEqual(
            support.one(self.database, "select count(*) from records"), before
        )

    def test_daily_metrics_has_a_row_per_metric_and_day(self):
        days = support.one(
            self.database, "select count(distinct local_date) from records"
        )
        self.assertGreaterEqual(days, 365)
        self.assertGreater(
            support.one(self.database, "select count(*) from daily_metrics"), days
        )

    def test_no_canned_query_approaches_datasette_s_time_limit(self):
        """The regression this catches is a query that quietly starts scanning
        `records` — fine on a fixture, seconds on a real export, and Datasette
        gives up at one second by default.

        The threshold cannot be Datasette's own limit: this database is a
        fraction of a real five-year export, so a query that takes four seconds
        there takes well under one second here and would slip through. It is
        set from measurement instead. On this data the slowest canned query
        runs in 9 ms and the inventory query, before it was moved to
        `metric_summary`, took 620 ms. 200 ms sits between the two with room on
        both sides for slower hardware.
        """
        import sqlite3
        import time

        from .test_metadata_queries import DATASETTE, load_queries

        if DATASETTE is None:
            self.skipTest("datasette is not installed")

        named = support.workspace("timing") / "health.db"
        shutil.copy(self.database, named)
        parameters = {
            "day": support.one(named, "select max(local_date) from records"),
            "metric": "StepCount",
        }

        connection = sqlite3.connect(named)
        try:
            for query in load_queries(named):
                with self.subTest(query=query["name"]):
                    started = time.perf_counter()
                    connection.execute(query["sql"], parameters).fetchall()
                    elapsed = time.perf_counter() - started
                    self.assertLess(
                        elapsed,
                        CANNED_QUERY_LIMIT_S,
                        f"{query['name']} took {elapsed * 1000:.0f} ms on "
                        f"{support.one(named, 'select count(*) from records'):,} "
                        f"records — most likely it is scanning `records` where "
                        f"`daily_metrics` or `metric_summary` would do",
                    )
        finally:
            connection.close()

    def test_indexed_lookup_stays_fast(self):
        """A day of heart rate out of a year of samples should be instant."""
        import time

        day = support.one(self.database, "select max(local_date) from records")
        started = time.perf_counter()
        support.rows(
            self.database,
            "select start_ts, value from records "
            "where type_short = 'HeartRate' and local_date = ? order by start_ts",
            (day,),
        )
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
