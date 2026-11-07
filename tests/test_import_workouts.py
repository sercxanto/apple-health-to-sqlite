"""Workouts: statistics, the totals that moved, segments, and GPS routes."""

from __future__ import annotations

import sqlite3
import unittest
import zipfile
from pathlib import Path

from apple_health_to_sqlite.importer import ImportOptions

from . import support

ROUTE = support.gpx([(48.10, 11.50), (48.11, 11.51), (48.12, 11.52)])


class WorkoutImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, cls.result = support.import_export(
            support.export_from_fixture("workouts", routes={"route_a.gpx": ROUTE})
        )

    def test_imports_all_three_workouts(self):
        self.assertEqual(support.one(self.database, "select count(*) from workouts"), 3)

    def test_totals_are_backfilled_from_workout_statistics(self):
        """Version 14 dropped totalDistance from the Workout element. Reading
        only the attribute is why older tools show empty distances."""
        distance, unit, energy = support.rows(
            self.database,
            "select total_distance, total_distance_unit, total_energy_burned "
            "from workouts where activity_short = 'Running'",
        )[0]
        self.assertEqual(distance, 10.5)
        self.assertEqual(unit, "km")
        self.assertEqual(energy, 620.25)

    def test_legacy_attributes_are_still_honoured(self):
        distance, energy = support.rows(
            self.database,
            "select total_distance, total_energy_burned from workouts "
            "where activity_short = 'Cycling'",
        )[0]
        self.assertEqual(distance, 12.25)
        self.assertEqual(energy, 310.5)

    def test_statistics_keep_average_minimum_maximum(self):
        average, minimum, maximum = support.rows(
            self.database,
            "select average, minimum, maximum from workout_statistics "
            "where type_short = 'HeartRate'",
        )[0]
        self.assertEqual((average, minimum, maximum), (150.5, 98.0, 178.0))

    def test_events_are_numbered_in_document_order(self):
        events = support.rows(
            self.database,
            "select seq, type_short from workout_events order by workout_id, seq",
        )
        self.assertEqual([t for _, t in events], ["Pause", "Resume"])

    def test_multi_sport_segments_are_kept_separately(self):
        segments = support.rows(
            self.database,
            "select uuid, duration from workout_activities order by uuid",
        )
        self.assertEqual(segments, [("segment-bike", 60.0), ("segment-swim", 30.0)])

    def test_segment_statistics_do_not_collide_with_workout_level_ones(self):
        """All three share a type; without activity_uuid in the key, two would
        overwrite the third."""
        sums = support.rows(
            self.database,
            "select activity_uuid, sum from workout_statistics s "
            "join workouts w on w.id = s.workout_id "
            "where w.activity_short = 'SwimBikeRun' order by activity_uuid",
        )
        self.assertEqual(
            sums, [("", 900.0), ("segment-bike", 600.0), ("segment-swim", 300.0)]
        )

    def test_event_and_route_metadata_are_kept(self):
        self.assertEqual(
            support.rows(
                self.database,
                "select key, value from workout_metadata "
                "where key like 'event:%' or key like 'route:%' order by key",
            ),
            [
                ("event:0:HKFakeEventKey", "event-value"),
                ("route:HKFakeRouteKey", "route-value"),
            ],
        )

    def test_a_workouts_record_collapses_with_its_top_level_copy(self):
        self.assertEqual(support.one(self.database, "select count(*) from records"), 1)
        self.assertEqual(
            support.one(self.database, "select count(*) from record_metadata"), 1
        )

    def test_a_workouts_record_is_linked_to_it(self):
        self.assertEqual(
            support.rows(
                self.database,
                "select w.activity_short, r.value from workout_records l "
                "join workouts w on w.id = l.workout_id "
                "join records r on r.id = l.record_id",
            ),
            [("SwimBikeRun", 140.0)],
        )

    def test_zone_groups_belong_to_the_workout_or_a_segment(self):
        self.assertEqual(
            support.rows(
                self.database,
                "select activity_uuid, seq, type_short, source, unit "
                "from workout_zone_groups order by activity_uuid, seq",
            ),
            [
                ("", 0, "HeartRate", "Fake Source", "count/min"),
                ("", 1, "CyclingPower", "Fake Source", "W"),
                ("segment-swim", 0, "HeartRate", "Fake Source", "count/min"),
            ],
        )

    def test_zones_may_be_open_ended(self):
        self.assertEqual(
            support.rows(
                self.database,
                "select seq, minimum, maximum, duration, duration_unit "
                "from workout_zones where activity_uuid = '' and group_seq = 0 "
                "order by seq",
            ),
            [
                (0, None, 120.0, 20.0, "min"),
                (1, 120.0, 150.0, 40.0, "min"),
                (2, 150.0, None, 30.0, "min"),
            ],
        )

    def test_nothing_in_a_version_17_workout_is_left_unhandled(self):
        self.assertEqual(self.result.unhandled, {})

    def test_route_points_are_linked_to_their_workout(self):
        points = support.rows(
            self.database,
            "select p.seq, p.latitude, p.longitude, p.elevation, p.speed "
            "from workout_points p join workouts w on w.id = p.workout_id "
            "where w.activity_short = 'Running' order by p.seq",
        )
        self.assertEqual(len(points), 3)
        self.assertEqual(points[0][1:3], (48.10, 11.50))
        self.assertEqual(points[0][3], 500.0)
        self.assertEqual(points[0][4], 2.5)

    def test_route_point_times_are_parsed(self):
        self.assertEqual(
            support.one(
                self.database, "select count(*) from workout_points where ts is null"
            ),
            0,
        )

    def test_point_count_is_written_back_to_the_workout(self):
        self.assertEqual(
            support.one(
                self.database,
                "select n_points from workouts where activity_short = 'Running'",
            ),
            3,
        )

    def test_workouts_without_a_route_have_no_points(self):
        self.assertEqual(
            support.one(
                self.database,
                "select coalesce(n_points, 0) from workouts "
                "where activity_short = 'Cycling'",
            ),
            0,
        )

    def test_route_count_is_reported(self):
        self.assertEqual(self.result.routes_imported, 1)


class RouteWithoutWorkoutTest(unittest.TestCase):
    def test_orphan_route_files_are_skipped(self):
        """A GPX with no matching workout is ignored rather than imported
        against a made-up id."""
        database, result = support.import_export(
            support.export_from_fixture(
                "workouts", routes={"route_nobody_references.gpx": ROUTE}
            )
        )
        self.assertEqual(result.routes_imported, 0)
        self.assertEqual(
            support.one(database, "select count(*) from workout_points"), 0
        )


class SyntheticWorkoutTest(unittest.TestCase):
    """The same paths, exercised against realistic synthetic data."""

    @classmethod
    def setUpClass(cls):
        cls.database = support.sample_database()

    def test_every_outdoor_workout_has_a_distance(self):
        """Indoor workouts have none, which is Apple's shape rather than a gap;
        anything that went out and recorded a route must have one."""
        self.assertEqual(
            support.one(
                self.database,
                "select count(*) from workouts "
                "where route_file is not null and total_distance is null",
            ),
            0,
        )

    def test_indoor_workouts_arrive_without_a_route_or_a_distance(self):
        without_route, with_distance = support.rows(
            self.database,
            "select count(*), count(total_distance) from workouts "
            "where activity_short = 'TraditionalStrengthTraining'",
        )[0]
        self.assertGreater(without_route, 0)
        self.assertEqual(with_distance, 0)

    def test_a_day_can_hold_an_indoor_workout_before_the_outdoor_one(self):
        """The ordinary shape of a real day, and the one that breaks any query
        reaching for "the" workout of a day — see tests/test_metadata_queries."""
        self.assertGreater(
            support.one(
                self.database,
                "select count(*) from workouts early join workouts late "
                "on late.local_date = early.local_date "
                "where early.route_file is null and late.route_file is not null "
                "and early.start_ts < late.start_ts",
            ),
            0,
        )

    def test_a_workouts_records_are_not_counted_twice(self):
        linked, distinct = support.rows(
            self.database,
            "select count(*), count(distinct record_id) from workout_records",
        )[0]
        self.assertGreater(linked, 0)
        self.assertEqual(linked, distinct)
        self.assertEqual(
            support.one(
                self.database,
                "select count(*) from workout_records l "
                "left join records r on r.id = l.record_id where r.id is null",
            ),
            0,
        )

    def test_zones_add_up_to_the_workout(self):
        rows = support.rows(
            self.database,
            "select w.duration, sum(z.duration) from workouts w "
            "join workout_zones z on z.workout_id = w.id "
            "where z.activity_uuid = '' group by w.id",
        )
        self.assertGreater(len(rows), 0)
        for workout_minutes, zone_minutes in rows:
            self.assertAlmostEqual(workout_minutes, zone_minutes, places=2)

    def test_segments_carry_statistics_events_and_zones(self):
        for table in ("workout_statistics", "workout_zone_groups"):
            self.assertGreater(
                support.one(
                    self.database,
                    f"select count(*) from {table} where activity_uuid != ''",
                ),
                0,
                table,
            )
        self.assertGreater(
            support.one(
                self.database,
                "select count(*) from workout_events where type_short = 'Segment'",
            ),
            0,
        )

    def test_workout_summary_view_computes_speed(self):
        minutes, distance, speed = support.rows(
            self.database,
            "select minutes, distance, speed from workout_summary "
            "where distance is not null order by local_date limit 1",
        )[0]
        self.assertAlmostEqual(speed, distance / (minutes / 60), places=1)


def workout_with_routes(*files: str, start: str = "2026-03-01 09:00:00 +0100") -> str:
    routes = "".join(
        f'<WorkoutRoute sourceName="Watch" sourceVersion="1.{index}" '
        f'startDate="{start}" endDate="{start}">'
        f'<MetadataEntry key="HKSyncVersion" value="{index}"/>'
        f'<FileReference path="/workout-routes/{name}"/></WorkoutRoute>'
        for index, name in enumerate(files)
    )
    return (
        '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" sourceName="Watch" '
        f'startDate="{start}" endDate="{start}">{routes}</Workout>\n'
    )


LATITUDES_B = [(50.0, 8.0), (50.1, 8.1)]
ROUTE_B = support.gpx(LATITUDES_B)


class SeveralRoutesTest(unittest.TestCase):
    """The schema allows any number of routes on a workout."""

    @classmethod
    def setUpClass(cls):
        cls.export = support.write_export(
            # Not in alphabetical order: the export's order is what counts.
            workout_with_routes("z_first.gpx", "a_second.gpx"),
            routes={"z_first.gpx": ROUTE, "a_second.gpx": ROUTE_B},
        )
        cls.database, cls.result = support.import_export(cls.export)

    def test_every_point_of_every_route_is_imported_in_the_exports_order(self):
        self.assertEqual(
            support.rows(
                self.database, "select seq, latitude from workout_points order by seq"
            ),
            [(0, 48.10), (1, 48.11), (2, 48.12), (3, 50.0), (4, 50.1)],
        )
        self.assertEqual(self.result.routes_imported, 2)

    def test_each_route_has_its_row(self):
        self.assertEqual(
            support.rows(
                self.database,
                "select seq, file, source_name, source_version from workout_routes "
                "order by seq",
            ),
            [(0, "z_first.gpx", "Watch", "1.0"), (1, "a_second.gpx", "Watch", "1.1")],
        )

    def test_the_workout_names_its_first_route_and_counts_all_points(self):
        self.assertEqual(
            support.rows(self.database, "select route_file, n_points from workouts"),
            [("z_first.gpx", 5)],
        )

    def test_the_metadata_of_each_route_is_kept(self):
        self.assertEqual(
            support.rows(
                self.database,
                "select key, value from workout_metadata order by key",
            ),
            [("route:1:HKSyncVersion", "1"), ("route:HKSyncVersion", "0")],
        )

    def test_importing_again_changes_nothing(self):
        database, _ = support.import_export(self.export)
        support.import_export(self.export, database=database)
        self.assertEqual(
            support.rows(database, "select * from workout_points order by seq"),
            support.rows(self.database, "select * from workout_points order by seq"),
        )

    def test_a_database_that_kept_only_the_last_route_is_put_right(self):
        """0.1.0 imported the last route from seq 0, where the first belongs."""
        database, _ = support.import_export(self.export)
        connection = sqlite3.connect(database)
        connection.execute("delete from workout_points where seq > 1")
        connection.execute("update workout_points set latitude = 50.0 + seq / 10.0")
        connection.commit()
        connection.close()

        support.import_export(self.export, database=database)
        self.assertEqual(
            support.rows(
                database, "select seq, latitude from workout_points order by seq"
            ),
            [(0, 48.10), (1, 48.11), (2, 48.12), (3, 50.0), (4, 50.1)],
        )

    def test_a_route_without_a_file_still_has_its_row(self):
        database, result = support.import_export(
            support.write_export(
                '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" '
                'sourceName="Watch" startDate="2026-03-01 09:00:00 +0100" '
                'endDate="2026-03-01 10:00:00 +0100">'
                '<WorkoutRoute sourceName="Watch" startDate="2026-03-01 09:00:00 +0100" '
                'endDate="2026-03-01 10:00:00 +0100"/></Workout>\n'
            )
        )
        self.assertEqual(
            support.rows(database, "select seq, file from workout_routes"), [(0, None)]
        )
        self.assertEqual(result.routes_missing, [])

    def test_one_file_named_by_two_workouts_reaches_both(self):
        database, _ = support.import_export(
            support.write_export(
                workout_with_routes("shared.gpx")
                + workout_with_routes("shared.gpx", start="2026-03-02 09:00:00 +0100"),
                routes={"shared.gpx": ROUTE},
            )
        )
        self.assertEqual(
            support.rows(
                database,
                "select count(*) from workout_points group by workout_id",
            ),
            [(3,), (3,)],
        )

    def test_synthetic_workouts_have_their_route_rows(self):
        database = support.sample_database()
        self.assertEqual(
            support.one(database, "select count(*) from workout_routes"),
            support.one(
                database, "select count(*) from workouts where route_file is not null"
            ),
        )


class RouteProblemsTest(unittest.TestCase):
    def test_a_referenced_file_that_is_not_there_is_reported(self):
        database, result = support.import_export(
            support.write_export(
                workout_with_routes("here.gpx", "gone.gpx"),
                routes={"here.gpx": ROUTE},
            )
        )
        self.assertEqual(result.routes_missing, ["gone.gpx"])
        self.assertEqual(result.routes_imported, 1)
        self.assertEqual(
            support.one(database, "select count(*) from workout_points"), 3
        )

    def test_a_damaged_route_is_skipped_and_reported(self):
        database, result = support.import_export(
            support.write_export(
                workout_with_routes("bad.gpx", "good.gpx"),
                routes={"bad.gpx": ROUTE[: len(ROUTE) // 2], "good.gpx": ROUTE_B},
            )
        )
        self.assertEqual(result.routes_damaged, ["bad.gpx"])
        self.assertEqual(result.routes_imported, 1)
        self.assertEqual(
            support.rows(database, "select latitude from workout_points order by seq"),
            [(50.0,), (50.1,)],
            "none of the damaged file's points, and all of the next file's",
        )

    def test_a_damaged_route_does_not_cost_points_from_an_earlier_import(self):
        export = support.write_export(
            workout_with_routes("one.gpx", "two.gpx"),
            routes={"one.gpx": ROUTE, "two.gpx": ROUTE_B},
        )
        database, _ = support.import_export(export)
        damaged = support.write_export(
            workout_with_routes("one.gpx", "two.gpx"),
            routes={"one.gpx": ROUTE, "two.gpx": "<gpx><trk>"},
        )
        support.import_export(damaged, database=database)
        self.assertEqual(
            support.one(database, "select count(*) from workout_points"), 5
        )

    def test_a_clean_import_reports_no_route_problems(self):
        _, result = support.import_export(support.sample_export(days=4))
        self.assertEqual(result.routes_missing, [])
        self.assertEqual(result.routes_damaged, [])


class UnreadFilesTest(unittest.TestCase):
    def export_with_files(self, **options) -> Path:
        export = support.write_export(
            workout_with_routes("used.gpx"),
            routes={"used.gpx": ROUTE, "nobody_refers_to_this.gpx": ROUTE},
            **options,
        )
        with zipfile.ZipFile(export, "a") as archive:
            archive.writestr("apple_health_export/electrocardiograms/a.csv", "x")
            archive.writestr("apple_health_export/electrocardiograms/b.csv", "x")
            archive.writestr("apple_health_export/electrocardiograms/", "")
        return export

    def test_what_nothing_read_is_listed_per_directory(self):
        _, result = support.import_export(self.export_with_files())
        self.assertEqual(
            result.files_not_read,
            {"electrocardiograms": 2, "export_cda.xml": 1, "workout-routes": 1},
        )

    def test_routes_left_out_on_request_are_listed_too(self):
        _, result = support.import_export(
            self.export_with_files(), ImportOptions(routes=False)
        )
        self.assertEqual(result.files_not_read["workout-routes"], 2)

    def test_an_unpacked_directory_is_listed_the_same_way(self):
        export = support.write_export(
            workout_with_routes("used.gpx"),
            routes={"used.gpx": ROUTE, "nobody_refers_to_this.gpx": ROUTE},
            as_directory=True,
        )
        _, result = support.import_export(export)
        self.assertEqual(result.files_not_read, {"workout-routes": 1})

    def test_a_bare_xml_file_has_nothing_else_to_read(self):
        path = support.workspace("export") / "Export.xml"
        path.write_text(support.wrap_body(""), encoding="utf-8")
        _, result = support.import_export(path)
        self.assertEqual(result.files_not_read, {})


if __name__ == "__main__":
    unittest.main()
