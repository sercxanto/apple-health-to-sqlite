"""The interval sweep, tested on numbers small enough to check by hand.

`deduplicated_value` takes samples as `(start, end, value, source)` in seconds
and returns the day's value with each instant credited to one source only.
"""

from __future__ import annotations

import unittest

from apple_health_to_sqlite.dedup import deduplicated_value


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


if __name__ == "__main__":
    unittest.main()
