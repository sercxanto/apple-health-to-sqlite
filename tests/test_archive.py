"""Finding Export.xml, whatever it happens to be called this time."""

from __future__ import annotations

import unittest
import zipfile

from applehealth.archive import ExportArchive, ExportNotFound

from . import support

BODY = (
    '<Record type="HKQuantityTypeIdentifierStepCount" unit="count" value="1"'
    ' sourceName="iPhone" sourceVersion="17.4"'
    ' startDate="2026-08-01 08:00:00 +0200"'
    ' endDate="2026-08-01 09:00:00 +0200"/>\n'
)


class MemberLookupTest(unittest.TestCase):
    def test_capital_export_xml(self):
        """The spelling on the user's own device. Do not regress this."""
        path = support.write_export(BODY, name="Export.xml")
        with ExportArchive(path) as archive:
            self.assertTrue(archive.export_member.endswith("Export.xml"))

    def test_lowercase_export_xml(self):
        path = support.write_export(BODY, name="export.xml")
        with ExportArchive(path) as archive:
            self.assertTrue(archive.export_member.endswith("export.xml"))

    def test_uppercase_enclosing_directory(self):
        path = support.write_export(BODY, directory="Apple_Health_Export")
        with ExportArchive(path) as archive, archive.open_export() as stream:
            self.assertIn(b"HealthData", stream.read(4000))

    def test_unpacked_directory(self):
        path = support.write_export(BODY, as_directory=True)
        with ExportArchive(path) as archive, archive.open_export() as stream:
            self.assertIn(b"HealthData", stream.read(4000))

    def test_bare_xml_file(self):
        directory = support.write_export(BODY, as_directory=True)
        xml = directory / "apple_health_export" / "Export.xml"
        with ExportArchive(xml) as archive, archive.open_export() as stream:
            self.assertIn(b"HealthData", stream.read(4000))

    def test_clinical_document_is_not_mistaken_for_the_export(self):
        path = support.write_export(BODY)
        with ExportArchive(path) as archive:
            self.assertNotIn("cda", archive.export_member.lower())

    def test_shallowest_candidate_wins(self):
        """A stray copy in a subdirectory must not shadow the real one."""
        path = support.write_export(BODY)
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("apple_health_export/backup/old/Export.xml", "<broken/>")
        with ExportArchive(path) as archive:
            self.assertNotIn("backup", archive.export_member)

    def test_missing_export_names_what_it_did_find(self):
        path = support.workspace("empty") / "Export.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("apple_health_export/export_cda.xml", "<doc/>")
        with (
            ExportArchive(path) as archive,
            self.assertRaises(ExportNotFound) as caught,
        ):
            _ = archive.export_member
        self.assertIn("export_cda.xml", str(caught.exception))

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            ExportArchive(support.WORKSPACE / "does-not-exist.zip")


class RouteLookupTest(unittest.TestCase):
    def test_finds_gpx_routes(self):
        path = support.write_export(
            BODY,
            routes={
                "route_a.gpx": support.gpx([(48.1, 11.5)]),
                "route_b.GPX": support.gpx([(48.2, 11.6)]),
            },
        )
        with ExportArchive(path) as archive:
            members = archive.route_members()
        self.assertEqual(len(members), 2)

    def test_no_routes(self):
        path = support.write_export(BODY)
        with ExportArchive(path) as archive:
            self.assertEqual(archive.route_members(), [])


if __name__ == "__main__":
    unittest.main()
