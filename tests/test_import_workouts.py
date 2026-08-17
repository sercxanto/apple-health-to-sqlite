"""Workouts: statistics, the totals that moved, segments, and GPS routes."""

from __future__ import annotations

import unittest

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

    def test_workout_summary_view_computes_speed(self):
        minutes, distance, speed = support.rows(
            self.database,
            "select minutes, distance, speed from workout_summary "
            "where distance is not null order by local_date limit 1",
        )[0]
        self.assertAlmostEqual(speed, distance / (minutes / 60), places=1)


if __name__ == "__main__":
    unittest.main()
