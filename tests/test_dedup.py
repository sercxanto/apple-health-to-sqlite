"""The interval sweep, tested on numbers small enough to check by hand.

`deduplicated_value` takes samples as `(start, end, value, source)` in seconds
and returns the day's value with each instant credited to one source only.
"""

from __future__ import annotations

import random
import unittest

from apple_health_to_sqlite.dedup import deduplicated_value

from . import support


class DeduplicatedValueTest(unittest.TestCase):
    def test_a_single_source_keeps_its_whole_value(self):
        self.assertEqual(deduplicated_value([(0, 10, 100, "A")]), 100)

    def test_complementary_sources_add_up(self):
        """No overlap, so the sweep credits each its full value — the whole
        point of not collapsing to a single source."""
        samples = [(0, 10, 100, "A"), (20, 30, 50, "B")]
        self.assertEqual(deduplicated_value(samples), 150)

    def test_a_fully_overlapped_sample_is_dropped(self):
        """B sits entirely inside A's span, and A covers more of the day, so B
        wins nothing and contributes nothing."""
        samples = [(0, 10, 100, "A"), (2, 4, 40, "B")]
        self.assertEqual(deduplicated_value(samples), 100)

    def test_a_half_overlapped_sample_is_prorated(self):
        """Equal coverage, so the name breaks the tie: A wins 0–10, leaving B
        the 10–15 half of its span and half its value."""
        samples = [(0, 10, 100, "A"), (5, 15, 100, "B")]
        self.assertEqual(deduplicated_value(samples), 150)

    def test_the_better_covered_source_wins_the_overlap(self):
        """B covers more of the day than A, so B — not the alphabetically first
        — takes the overlap, and A keeps only its non-overlapping fifth."""
        samples = [(0, 10, 100, "A"), (5, 20, 300, "B")]
        # A's winning region is 0–5, half its span: 50. B keeps all 300.
        self.assertEqual(deduplicated_value(samples), 350)

    def test_priority_is_stable_regardless_of_row_order(self):
        forward = deduplicated_value([(0, 10, 100, "A"), (5, 15, 100, "B")])
        reversed_ = deduplicated_value([(5, 15, 100, "B"), (0, 10, 100, "A")])
        self.assertEqual(forward, reversed_)

    def test_a_sample_without_a_readable_end_counts_in_full(self):
        """It cannot be placed on the timeline, so nothing shows it overlaps."""
        samples = [(0, 10, 100, "A"), (0, None, 7, "B")]
        self.assertEqual(deduplicated_value(samples), 107)

    def test_samples_without_a_duration_add_up_across_sources(self):
        """A glass of water logged at one instant overlaps nothing."""
        instant = 1_780_000_000
        samples = [
            (instant, instant, 250, "A"),
            (instant + 3600, instant + 3600, 500, "B"),
        ]
        self.assertEqual(deduplicated_value(samples), 750)

    def test_a_sample_without_a_duration_counts_beside_timed_samples(self):
        samples = [(0, 10, 100, "A"), (5, 5, 40, "A"), (5, 5, 7, "B")]
        self.assertEqual(deduplicated_value(samples), 147)

    def test_a_sample_ending_before_it_starts_counts_in_full(self):
        samples = [(0, 10, 100, "A"), (20, 15, 7, "B")]
        self.assertEqual(deduplicated_value(samples), 107)


def credited_second_by_second(samples) -> float:
    """The same answer the slow and obvious way, on whole seconds."""
    covered: dict[str, set[int]] = {}
    for start, end, _value, source in samples:
        covered.setdefault(source, set()).update(range(start, end))
    ranked = sorted(covered, key=lambda source: (-len(covered[source]), source))
    owner: dict[int, str] = {}
    for source in ranked:
        for second in covered[source]:
            owner.setdefault(second, source)
    return sum(
        value
        * sum(owner[second] == source for second in range(start, end))
        / (end - start)
        for start, end, value, source in samples
    )


class AgainstAReferenceTest(unittest.TestCase):
    def test_random_days_agree_with_counting_every_second(self):
        generator = random.Random(20261107)
        for _ in range(300):
            samples = []
            for _ in range(generator.randrange(1, 40)):
                start = generator.randrange(500)
                samples.append(
                    (
                        start,
                        start + generator.randrange(1, 60),
                        generator.randrange(1, 100),
                        generator.choice("ABC"),
                    )
                )
            self.assertAlmostEqual(
                deduplicated_value(samples),
                credited_second_by_second(samples),
                msg=repr(samples),
            )


class SamplesWithoutDurationTest(unittest.TestCase):
    def test_the_documented_query_shows_them_rather_than_zero(self):
        record = (
            '<Record type="HKQuantityTypeIdentifierDietaryWater" unit="mL" value="{value}" '
            'sourceName="{source}" startDate="{time}" endDate="{time}"/>\n'
        )
        database, _ = support.import_export(
            support.write_export(
                record.format(value=250, source="A", time="2026-03-01 08:00:00 +0100")
                + record.format(value=500, source="B", time="2026-03-01 18:00:00 +0100")
            )
        )
        self.assertEqual(
            support.one(
                database,
                "select coalesce(deduplicated_value, day_value) from daily_metrics",
            ),
            750.0,
        )


class UnreadableTimestampTest(unittest.TestCase):
    def test_a_bad_end_date_does_not_abort_the_import(self):
        """One bad timestamp must not cost anyone a multi-gigabyte import, on the
        day with several sources any more than on any other day."""
        record = (
            '<Record type="HKQuantityTypeIdentifierStepCount" unit="count" value="10" '
            'sourceName="{source}" startDate="2026-03-01 08:00:00 +0100" '
            'endDate="{end}"/>\n'
        )
        database, _ = support.import_export(
            support.write_export(
                record.format(source="Phone", end="not a date")
                + record.format(source="Watch", end="2026-03-01 08:10:00 +0100")
            )
        )
        self.assertEqual(
            support.rows(
                database, "select total, deduplicated_value from daily_metrics"
            ),
            [(20.0, 20.0)],
        )


if __name__ == "__main__":
    unittest.main()
