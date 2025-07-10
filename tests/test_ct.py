import unittest
from pathlib import Path

from pregdos.model_ct import CTModel
from pregdos.import_ct import load_ct

# path to test CT file

CT_TEST_PATH = Path("res") / "test_studies" / "DCPT_headphantom"


class TestCT(unittest.TestCase):
    def test_ct_initialization(self):
        ct = CTModel()
        self.assertIsInstance(ct, CTModel)
        self.assertEqual(ct.patient_name, "")
        self.assertEqual(ct.rows, 0)

    def test_ct_load(self):
        # This test would normally load a CT model from a file.
        # Here we just check that the method exists and returns an instance.
        ct = load_ct(CT_TEST_PATH)
        self.assertIsInstance(ct, CTModel)
        self.assertIsNotNone(ct.images)
        self.assertIsNotNone(ct.patient_id)
        self.assertIsNotNone(ct.patient_name)


if __name__ == '__main__':
    unittest.main()
