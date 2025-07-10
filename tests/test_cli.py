import unittest
import sys
from pathlib import Path
from io import StringIO

import pregdos.main_plan_export as main_plan_export


class TestPregdosCLI(unittest.TestCase):

    def test_help_flag(self):
        """Test that -h returns help message without error."""
        saved_stdout = sys.stdout
        try:
            sys.stdout = StringIO()
            with self.assertRaises(SystemExit) as cm:
                main_plan_export.main(["-h"])
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
                main_plan_export.main(["-V"])
            output = sys.stdout.getvalue()
            # Match versions like 0.0.post2+g3e1d4d2
            self.assertRegex(output, r"\d+\.\d+(\.\d+)?([a-z0-9\.\+\-]+)?")
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.stdout = saved_stdout

    def _run_conversion_test(self, dicom_file_name: str):
        """Helper to run CLI on a given DICOM file and check output."""
        test_output_file = Path("plan_field1.txt")
        if test_output_file.exists():
            test_output_file.unlink()

        test_args = [
            "-f1",
            "-vv",
            "-b=res/beam_models/DCPT_beam_model__v2.csv",
            f"res/test_plans/{dicom_file_name}"
        ]

        retcode = main_plan_export.main(test_args)
        self.assertEqual(retcode, 0, f"CLI execution failed for {dicom_file_name}")

        self.assertTrue(test_output_file.exists(), f"Output file was not created for {dicom_file_name}.")
        self.assertGreater(test_output_file.stat().st_size, 0, f"Output file is empty for {dicom_file_name}.")

        # Clean up
        test_output_file.unlink()

    def test_conversion_temp_160MeV(self):
        """Run CLI conversion on monoenergetic 160 MeV DICOM input."""
        self._run_conversion_test("temp_160MeV_10x10.dcm")

    def test_conversion_temp_sobp(self):
        """Run CLI conversion on SOBP DICOM input."""
        self._run_conversion_test("temp_sobp_10x10.dcm")


if __name__ == "__main__":
    unittest.main()
