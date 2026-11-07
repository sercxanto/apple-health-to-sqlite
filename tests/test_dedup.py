"""The interval sweep, tested on numbers small enough to check by hand.

`deduplicated_value` takes samples as `(start, end, value, source)` in seconds
and returns the day's value with each instant credited to one source only.
"""

from __future__ import annotations

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
