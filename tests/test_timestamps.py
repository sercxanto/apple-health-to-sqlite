"""Apple timestamps are parsed by hand for speed, so they get parsed carefully."""

from __future__ import annotations

import random
import unittest
from datetime import UTC, datetime, timedelta, timezone

from apple_health_to_sqlite import timestamps


class ParseTest(unittest.TestCase):
    def test_splits_into_local_date_local_iso_and_utc_instant(self):
        local_date, local_iso, instant = timestamps.parse("2026-08-15 20:48:13 +0200")
        self.assertEqual(local_date, "2026-08-15")
        self.assertEqual(local_iso, "2026-08-15T20:48:13")
        # 20:48 local at +0200 is 18:48 UTC.
        self.assertEqual(
            datetime.fromtimestamp(instant, UTC).strftime("%Y-%m-%d %H:%M"),
            "2026-08-15 18:48",
        )

    def test_negative_offset(self):
        local_date, _, instant = timestamps.parse("2026-03-05 23:30:00 -0500")
        self.assertEqual(local_date, "2026-03-05")
        # The UTC instant falls on the following day.
        self.assertEqual(
            datetime.fromtimestamp(instant, UTC).strftime("%Y-%m-%d %H:%M"),
            "2026-03-06 04:30",
        )

    def test_half_hour_offset(self):
        _, _, instant = timestamps.parse("2026-08-15 12:00:00 +0530")
        self.assertEqual(
            datetime.fromtimestamp(instant, UTC).strftime("%H:%M"), "06:30"
        )

    def test_agrees_with_strptime_on_random_samples(self):
        """The fast path must not diverge from the reference implementation."""
        generator = random.Random(20260815)
        base = datetime(2019, 1, 1, tzinfo=UTC)
        for _ in range(2000):
            offset_minutes = generator.choice([-480, -300, -60, 0, 60, 120, 330, 780])
            moment = (
                base + timedelta(seconds=generator.randrange(9 * 365 * 86400))
            ).astimezone(timezone(timedelta(minutes=offset_minutes)))
            text = moment.strftime("%Y-%m-%d %H:%M:%S %z")
            expected = datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
            local_date, local_iso, instant = timestamps.parse(text)
            self.assertEqual(instant, int(expected.timestamp()), text)
            self.assertEqual(local_date, expected.strftime("%Y-%m-%d"), text)
            self.assertEqual(local_iso, expected.strftime("%Y-%m-%dT%H:%M:%S"), text)

    def test_daylight_saving_transition(self):
        """Same wall clock, different offsets, two hours apart in real time."""
        _, _, before = timestamps.parse("2026-03-29 02:30:00 +0100")
        _, _, after = timestamps.parse("2026-03-29 02:30:00 +0200")
        self.assertEqual(before - after, 3600)

    def test_missing_offset_is_treated_as_utc(self):
        _, _, instant = timestamps.parse("2026-08-15 12:00:00")
        self.assertEqual(
            datetime.fromtimestamp(instant, UTC).strftime("%H:%M"), "12:00"
        )

    def test_malformed_input_returns_nothing_rather_than_raising(self):
        # One bad row must not abort a multi-gigabyte import.
        for bad in (None, "", "not a date", "2026-13-45 99:99:99 +0200", "2026-08"):
            self.assertEqual(timestamps.parse(bad), (None, None, None), repr(bad))


class GpxTimeTest(unittest.TestCase):
    def test_parses_utc_gpx_stamp(self):
        instant = timestamps.parse_gpx_time("2026-08-01T15:12:00Z")
        self.assertEqual(
            datetime.fromtimestamp(instant, UTC).strftime("%Y-%m-%d %H:%M"),
            "2026-08-01 15:12",
        )

    def test_tolerates_fractional_seconds(self):
        self.assertEqual(
            timestamps.parse_gpx_time("2026-08-01T15:12:00.482Z"),
            timestamps.parse_gpx_time("2026-08-01T15:12:00Z"),
        )

    def test_missing_value(self):
        self.assertIsNone(timestamps.parse_gpx_time(None))


class StartOfDayTest(unittest.TestCase):
    def test_midnight_utc(self):
        instant = timestamps.start_of_day("2026-08-15")
        self.assertEqual(
            datetime.fromtimestamp(instant, UTC).strftime("%Y-%m-%d %H:%M"),
            "2026-08-15 00:00",
        )


if __name__ == "__main__":
    unittest.main()
