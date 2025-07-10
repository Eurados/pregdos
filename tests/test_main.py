import unittest
import sys
from pathlib import Path
from io import StringIO

import pregdos.main as study

DICOM_TEST_DIR = Path("res/test_studies/DCPT_headphantom/")
BEAM_MODEL_PATH = Path("res/beam_models/DCPT_beam_model__v2.csv")
SPR_TABLE_PATH = Path("res/spr_tables/SPRtoMaterial__Brain.txt")


class TestPregdosCLI(unittest.TestCase):

    def test_help_flag(self):
        """Test that -h returns help message without error."""
        saved_stdout = sys.stdout
        try:
            sys.stdout = StringIO()
            with self.assertRaises(SystemExit) as cm:
                study.main(["-h"])
            output = sys.stdout.getvalue()
            self.assertIn("usage", output.lower())
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.stdout = saved_stdout

    def test_version_flag(self):
        """Test that -V returns version string without error."""
        saved_stdout = sys.stdout
        try:
            sys.stdout = StringIO()
            with self.assertRaises(SystemExit) as cm:
                study.main(["-V"])
            output = sys.stdout.getvalue()
            # Match versions like 0.0.post2+g3e1d4d2
            self.assertRegex(output, r"\d+\.\d+(\.\d+)?([a-z0-9\.\+\-]+)?")
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.stdout = saved_stdout

    def test_main(self):
        """Helper to run CLI on a given DICOM file and check output."""

        test_output_files = [
            Path(f"topas_field{i}.txt") for i in range(1, 4)
        ]

        # Clean up any existing output files
        for test_output_file in test_output_files:
            if test_output_file.exists():
                test_output_file.unlink()

        test_args = [
            "-vv",
            "-p 500.0",
            f"-b={BEAM_MODEL_PATH}",
            f"-s={SPR_TABLE_PATH}",
            f"{DICOM_TEST_DIR}"
        ]

        retcode = study.main(test_args)
        self.assertEqual(retcode, 0, f"CLI execution failed for {DICOM_TEST_DIR}")

        # check if all output files were created and are not empty:
        for test_output_file in test_output_files:
            self.assertTrue(test_output_file.exists(), f"Output file was not created for {DICOM_TEST_DIR}.")
            self.assertGreater(test_output_file.stat().st_size, 0, f"Output file is empty for {DICOM_TEST_DIR}.")

        # Clean up
        for test_output_file in test_output_files:
            test_output_file.unlink()


if __name__ == "__main__":
    unittest.main()
