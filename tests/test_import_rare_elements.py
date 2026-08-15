"""Audiograms, vision prescriptions and clinical records.

Rare enough that nobody would notice them breaking, which is exactly why they
are tested. The generated export carries one of each.
"""

from __future__ import annotations

import unittest

from . import support


class AudiogramTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = support.sample_database()

    def test_audiogram_is_imported(self):
        self.assertEqual(
            support.one(self.database, "select count(*) from audiograms"), 1
        )

    def test_sensitivity_points_keep_both_ears(self):
        points = support.rows(
            self.database,
            "select frequency_value, frequency_unit, left_ear_value, "
            "right_ear_value from audiogram_points order by seq",
        )
        self.assertEqual(
            [point[0] for point in points], [500.0, 1000.0, 2000.0, 4000.0, 8000.0]
        )
        self.assertTrue(all(point[1] == "Hz" for point in points))
        self.assertTrue(
            all(point[2] is not None and point[3] is not None for point in points)
        )

    def test_masking_and_clamping_range_are_kept(self):
        masked, lower, upper = support.rows(
            self.database,
            "select left_ear_masked, left_ear_clamping_lower, "
            "left_ear_clamping_upper from audiogram_points limit 1",
        )[0]
        self.assertEqual(masked, "0")
        self.assertEqual((lower, upper), (0.0, 90.0))

    def test_audiogram_metadata_is_not_dropped(self):
        entries = support.rows(
            self.database,
            "select key, value from extra_metadata where owner_type = 'audiograms'",
        )
        self.assertEqual(entries, [("HKMetadataKeyAudiogramTestMethod", "1")])


class VisionPrescriptionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = support.sample_database()

    def test_one_row_per_eye(self):
        eyes = support.rows(
            self.database, "select eye from vision_prescriptions order by eye"
        )
        self.assertEqual(eyes, [("left",), ("right",)])

    def test_measurements_and_their_units_are_both_kept(self):
        sphere, sphere_unit, axis, axis_unit = support.rows(
            self.database,
            "select sphere, sphere_unit, axis, axis_unit "
            "from vision_prescriptions where eye = 'right'",
        )[0]
        self.assertEqual((sphere, sphere_unit), (-1.25, "diopters"))
        self.assertEqual((axis, axis_unit), (180.0, "degrees"))

    def test_pupillary_distances_use_the_export_spelling(self):
        far_pd, far_pd_unit, near_pd = support.rows(
            self.database,
            "select far_pd, far_pd_unit, near_pd from vision_prescriptions "
            "where eye = 'left'",
        )[0]
        self.assertEqual((far_pd, far_pd_unit, near_pd), (32.0, "mm", 30.0))

    def test_both_eyes_share_a_prescription_id(self):
        ids = {
            row[0]
            for row in support.rows(
                self.database, "select prescription_id from vision_prescriptions"
            )
        }
        self.assertEqual(len(ids), 1)

    def test_attachment_and_metadata_are_kept(self):
        entries = dict(
            support.rows(
                self.database,
                "select key, value from extra_metadata "
                "where owner_type = 'vision_prescriptions'",
            )
        )
        self.assertEqual(entries["attachment:0"], "prescription-scan-1")
        self.assertIn("HKMetadataKeyDeviceSerialNumber", entries)

    def test_extra_metadata_joins_back_to_the_prescription(self):
        joined = support.one(
            self.database,
            "select count(*) from extra_metadata m "
            "join vision_prescriptions v on v.prescription_id = m.owner_id "
            "where m.owner_type = 'vision_prescriptions'",
        )
        self.assertGreater(joined, 0)


class ClinicalRecordTest(unittest.TestCase):
    def test_reference_is_imported(self):
        database = support.sample_database()
        row = support.rows(
            database,
            "select type, fhir_version, resource_file_path from clinical_records",
        )
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0][1], "4.0.1")
        self.assertTrue(row[0][2].endswith(".json"))


if __name__ == "__main__":
    unittest.main()
