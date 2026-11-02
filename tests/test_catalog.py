"""The catalogue: what a record's value means, where the export cannot say.

The export says `value="HKCategoryValueSeverityModerate"` and its DTD says
nothing more than `CDATA`; it says `value="1200"` without saying that a day of
those adds up while a day of heart rates does not. These tests cover the layer
that supplies both, and — more importantly — that it stays out of the way when
it does not recognise something.
"""

from __future__ import annotations

import unittest

from applehealth import catalog

from . import support


class CatalogDataTest(unittest.TestCase):
    """The generated file itself, before any database is involved."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = catalog.load()

    def test_every_type_names_a_known_enumeration(self):
        """A type may have no enumeration, but never a dangling one."""
        for type_name, entry in self.catalog["types"].items():
            if entry["enum"] is not None:
                self.assertIn(entry["enum"], self.catalog["enums"], type_name)

    def test_value_names_are_unique_across_enumerations(self):
        """The database keys on the value alone, so collisions would silently
        map a value to the wrong meaning."""
        seen = set()
        for entry in self.catalog["enums"].values():
            for member in entry["members"]:
                self.assertNotIn(member["name"], seen)
                seen.add(member["name"])

    def test_short_names_are_the_value_without_its_enumeration_prefix(self):
        for enum, entry in self.catalog["enums"].items():
            for member in entry["members"]:
                self.assertEqual(member["name"], enum + member["short"])

    def test_only_scales_carry_ordinals(self):
        for enum, entry in self.catalog["enums"].items():
            ordinals = [member["ordinal"] for member in entry["members"]]
            if entry["scale"]:
                self.assertEqual(
                    [o for o in ordinals if o is not None],
                    list(range(len([o for o in ordinals if o is not None]))),
                    f"{enum} should number its ladder from zero, without gaps",
                )
            else:
                self.assertEqual(ordinals, [None] * len(ordinals), enum)

    def test_severity_runs_from_absent_to_severe(self):
        members = self.catalog["enums"]["HKCategoryValueSeverity"]["members"]
        ordered = {member["short"]: member["ordinal"] for member in members}
        self.assertLess(ordered["NotPresent"], ordered["Mild"])
        self.assertLess(ordered["Mild"], ordered["Moderate"])
        self.assertLess(ordered["Moderate"], ordered["Severe"])
        self.assertIsNone(ordered["Unspecified"])

    def test_sleep_stages_are_present_and_unranked(self):
        """`derive` reads these names; the sleep views break if they move."""
        members = self.catalog["enums"]["HKCategoryValueSleepAnalysis"]["members"]
        shorts = {member["short"] for member in members}
        self.assertTrue({"InBed", "Awake", "AsleepCore", "AsleepREM"} <= shorts)
        self.assertNotIn("AllAsleepValues", shorts, "that one is a Set, not a value")


class QuantityCatalogDataTest(unittest.TestCase):
    """The other half of the catalogue: how a day of samples adds up."""

    @classmethod
    def setUpClass(cls):
        cls.types = catalog.load_quantities()["types"]

    def test_every_entry_is_a_quantity_identifier(self):
        for type_name in self.types:
            self.assertTrue(type_name.startswith("HKQuantityTypeIdentifier"), type_name)

    def test_aggregation_is_one_of_the_two_styles_or_unstated(self):
        """`daily_metrics` switches on these three cases and nothing else."""
        for type_name, entry in self.types.items():
            self.assertIn(
                entry["aggregation"], ("cumulative", "discrete", None), type_name
            )

    def test_the_two_styles_are_both_well_populated(self):
        """A crawl that silently stopped matching would land everything in one
        bucket — or in neither — rather than in the rough halves Apple has."""
        styles = [entry["aggregation"] for entry in self.types.values()]
        self.assertGreater(styles.count("cumulative"), 20)
        self.assertGreater(styles.count("discrete"), 20)

    def test_the_metrics_the_docs_lean_on_read_the_way_they_read(self):
        """Steps add up, heart rate does not. docs/database.md and `derive` both say so."""
        self.assertEqual(
            self.types["HKQuantityTypeIdentifierStepCount"]["aggregation"], "cumulative"
        )
        self.assertEqual(
            self.types["HKQuantityTypeIdentifierHeartRate"]["aggregation"], "discrete"
        )


class CategorySamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, _ = support.import_export(
            support.export_from_fixture("category_values")
        )

    def sample(self, type_short: str) -> tuple:
        return support.rows(
            self.database,
            "select value_short, ordinal, family, topic from category_samples "
            "where type_short = ?",
            (type_short,),
        )[0]

    def test_value_name_loses_its_enumeration_prefix(self):
        self.assertEqual(self.sample("LowerBackPain")[:2], ("Moderate", 2))

    def test_absent_is_the_bottom_of_the_scale_not_a_missing_value(self):
        self.assertEqual(self.sample("Headache")[:2], ("NotPresent", 0))

    def test_values_beside_the_scale_get_no_ordinal(self):
        self.assertEqual(self.sample("Chills")[:2], ("Unspecified", None))

    def test_an_enumeration_that_is_not_a_scale_still_gets_its_name(self):
        self.assertEqual(self.sample("AppetiteChanges")[:2], ("Decreased", None))

    def test_types_carry_apples_grouping(self):
        self.assertEqual(
            self.sample("LowerBackPain")[2:], ("Symptoms", "Musculoskeletal")
        )

    def test_unknown_type_with_a_known_value_still_reads(self):
        """Values are looked up by name alone, not per type — so a symptom type
        Apple adds after this catalogue was generated still reads `Severe`,
        because it uses the same severity enumeration every other one does. Its
        grouping is unknown, which is what keeps it out of the symptom diary."""
        self.assertEqual(
            self.sample("InventedFutureSymptom"), ("Severe", 3, None, None)
        )

    def test_unknown_value_on_a_known_type_keeps_its_raw_name(self):
        value_short, ordinal, family, _ = self.sample("Bloating")
        self.assertEqual(value_short, "HKCategoryValueSeverityInvented")
        self.assertIsNone(ordinal)
        self.assertEqual(family, "Symptoms")

    def test_raw_value_is_kept_alongside_the_translation(self):
        self.assertEqual(
            support.one(
                self.database,
                "select value_text from category_samples "
                "where type_short = 'LowerBackPain'",
            ),
            "HKCategoryValueSeverityModerate",
        )

    def test_quantity_records_are_left_out(self):
        self.assertEqual(
            support.one(
                self.database,
                "select count(*) from category_samples "
                "where type not like 'HKCategoryTypeIdentifier%'",
            ),
            0,
        )


class SymptomsViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, _ = support.import_export(
            support.export_from_fixture("category_values")
        )

    def test_lists_only_the_symptom_family(self):
        symptoms = {
            row[0]
            for row in support.rows(self.database, "select symptom from symptoms")
        }
        self.assertIn("LowerBackPain", symptoms)
        self.assertNotIn(
            "MindfulSession", symptoms, "a mindful session is not a symptom"
        )

    def test_an_unrecognised_type_is_not_guessed_into_the_diary(self):
        self.assertNotIn(
            "InventedFutureSymptom",
            {
                row[0]
                for row in support.rows(self.database, "select symptom from symptoms")
            },
        )

    def test_severity_can_be_ordered(self):
        ordered = support.rows(
            self.database,
            "select symptom from symptoms where ordinal is not null "
            "order by ordinal desc, symptom",
        )
        self.assertEqual(ordered[0][0], "LowerBackPain")  # Moderate
        self.assertEqual(ordered[-1][0], "Headache")  # NotPresent

    def test_an_episode_spanning_days_keeps_its_whole_span(self):
        """A week of back pain is one record. `minutes` covers the week, while
        `local_date` is the day it started — the grouping most queries want."""
        start, end, minutes = support.rows(
            self.database,
            "select local_date, end_local, minutes from symptoms "
            "where symptom = 'PelvicPain'",
        )[0]
        self.assertEqual(start, "2026-08-02")
        self.assertTrue(end.startswith("2026-08-08"), end)
        self.assertAlmostEqual(minutes, 6 * 24 * 60 + 479, delta=1)


class SleepStillReadsFromTheCatalogTest(unittest.TestCase):
    """`sleep_segments` used to strip the prefix with a hard-coded `replace`."""

    @classmethod
    def setUpClass(cls):
        cls.database, _ = support.import_export(
            support.export_from_fixture("sleep_nights")
        )

    def test_stages_are_named_without_their_prefix(self):
        stages = {
            row[0]
            for row in support.rows(self.database, "select stage from sleep_segments")
        }
        self.assertEqual(
            stages, {"InBed", "AsleepCore", "AsleepDeep", "Awake", "AsleepREM"}
        )


if __name__ == "__main__":
    unittest.main()
