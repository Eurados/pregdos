import unittest
from pathlib import Path

from pregdos.model_rtstruct import RTStruct
from pregdos.import_rtstruct import import_rtstruct

# paths to test RS file
CT_TEST_DIR = Path("res") / "test_studies" / "DCPT_headphantom"
CT_TEST_FILE = Path("res") / "test_studies" / "DCPT_headphantom" / \
    "RS.1.2.246.352.205.5439556202947041733.367077883804944283.dcm"

# patient information from the test RTSTRUCT file
PT_NAME = "E2E_test^ProcedureGroup1"
PT_ID = "E2E_test_PG1_1"
N_ROIS = 11  # number of ROIs in the test RTSTRUCT file
ROI_NAME = "BODY"  # name of the first ROI in the test RTSTRUCT file


class TestRTStruct(unittest.TestCase):
    def test_rts_initialization(self):
        rts = RTStruct()
        self.assertIsInstance(rts, RTStruct)
        self.assertEqual(rts.patient_id, "")
        self.assertEqual(rts.patient_name, "")

    def test_rts_load_file(self):
        # Load the RTSTRUCT file
        rts = import_rtstruct(CT_TEST_FILE)
        self.assertIsInstance(rts, RTStruct)
        self.assertEqual(rts.patient_name, PT_NAME)
        self.assertEqual(rts.patient_id, PT_ID)
        self.assertEqual(rts.n_rois, N_ROIS)
        self.assertEqual(rts.rois[0].roi_name, ROI_NAME)

    def test_rts_load_directory(self):
        # Load the RTSTRUCT from a directory
        rts = import_rtstruct(CT_TEST_DIR)
        self.assertIsInstance(rts, RTStruct)
        self.assertEqual(rts.patient_name, PT_NAME)
        self.assertEqual(rts.patient_id, PT_ID)
        self.assertEqual(rts.n_rois, N_ROIS)
        self.assertEqual(rts.rois[0].roi_name, ROI_NAME)


if __name__ == '__main__':
    unittest.main()
