"""The generator stands in for real health data, so it has to be faithful."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ElementTree
import zipfile

from applehealth import dtd, generate

from . import support


def elements_and_attributes(export_path):
    """Every element name and attribute name appearing in a generated export."""
    tags: set[str] = set()
    attributes: dict[str, set[str]] = {}
    with zipfile.ZipFile(export_path) as archive:
        member = next(n for n in archive.namelist() if n.endswith("Export.xml"))
        with archive.open(member) as stream:
            for _event, element in ElementTree.iterparse(stream, events=("end",)):
                tags.add(element.tag)
                attributes.setdefault(element.tag, set()).update(element.attrib)
    return tags, attributes


class StructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = support.sample_export(days=8)
        cls.tags, cls.attributes = elements_and_attributes(cls.export)
        cls.schema = dtd.bundled()

    def test_export_is_well_formed_xml(self):
        # elements_and_attributes would have raised on malformed XML; assert
        # something was actually read.
        self.assertIn("HealthData", self.tags)
        self.assertIn("Record", self.tags)

    def test_emits_only_declared_elements(self):
        self.assertEqual(self.tags - set(self.schema.elements), set())

    def test_emits_only_declared_attributes(self):
        for tag, used in self.attributes.items():
            declared = self.schema.attributes_of(tag)
            self.assertEqual(
                used - declared, set(), f"undeclared attributes on <{tag}>"
            )

    def test_covers_every_declared_element(self):
        """Otherwise a handler could rot without any test noticing."""
        missing = set(self.schema.elements) - self.tags
        self.assertEqual(missing, set(), "declared but never generated")

    def test_names_the_export_with_a_capital_e_by_default(self):
        with zipfile.ZipFile(self.export) as archive:
            names = archive.namelist()
        self.assertTrue(any(n.endswith("/Export.xml") for n in names))

    def test_includes_routes_and_a_clinical_document(self):
        with zipfile.ZipFile(self.export) as archive:
            names = archive.namelist()
        self.assertTrue(
            any("workout-routes/" in n and n.endswith(".gpx") for n in names)
        )
        self.assertTrue(any(n.endswith("export_cda.xml") for n in names))


class DeterminismTest(unittest.TestCase):
    def test_same_seed_produces_the_same_export(self):
        options = generate.GenerateOptions(days=3, seed=99, end_date="2026-08-14")
        first = support.workspace("gen") / "a.zip"
        second = support.workspace("gen") / "b.zip"
        generate.generate(first, options)
        generate.generate(second, options)
        self.assertEqual(
            zipfile.ZipFile(first).read("apple_health_export/Export.xml"),
            zipfile.ZipFile(second).read("apple_health_export/Export.xml"),
        )

    def test_different_seeds_produce_different_data(self):
        base = support.workspace("gen")
        generate.generate(
            base / "a.zip",
            generate.GenerateOptions(days=3, seed=1, end_date="2026-08-14"),
        )
        generate.generate(
            base / "b.zip",
            generate.GenerateOptions(days=3, seed=2, end_date="2026-08-14"),
        )
        self.assertNotEqual(
            zipfile.ZipFile(base / "a.zip").read("apple_health_export/Export.xml"),
            zipfile.ZipFile(base / "b.zip").read("apple_health_export/Export.xml"),
        )

    def test_day_count_is_respected(self):
        result = generate.generate(
            support.workspace("gen") / "c.zip",
            generate.GenerateOptions(days=5, end_date="2026-08-14"),
        )
        self.assertEqual(str(result.start_day), "2026-08-10")
        self.assertEqual(str(result.end_day), "2026-08-14")


if __name__ == "__main__":
    unittest.main()
