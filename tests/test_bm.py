import unittest
import numpy as np
from pathlib import Path

from pregdos.beam_model import BeamModel, get_fwhm

beam_models_dir = Path("res") / "beam_models"


class TestBeamModel(unittest.TestCase):

    def setUp(self):
        beam_model_files = sorted(beam_models_dir.glob("*.csv"))
        if not beam_model_files:
            self.skipTest("No beam model files found in the directory.")
        self.beam_models = []
        for path_bm in beam_model_files:
            if path_bm.is_file():
                bm = BeamModel(path_bm, nominal=True, beam_model_position=500.0)
                self.beam_models.append(bm)

    def test_fwhm_calculation(self):
        sigma = 1.0  # Example sigma value
        fwhm = get_fwhm(sigma)
        expected_fwhm = 2.354820045  # Expected FWHM calculation
        self.assertAlmostEqual(fwhm, expected_fwhm, places=5)

    def test_beam_model_integrity(self):
        """Ensure BeamModel loads CSVs correctly and exposes expected attributes."""
        for bm in self.beam_models:
            with self.subTest(beam_model=str(bm)):
                # Check data loaded
                self.assertIsInstance(bm.data, np.ndarray)
                self.assertGreater(bm.data.shape[0], 0, "Beam model data is empty.")

                # Check interpolators are callable
                for attr in ['f_sx', 'f_sy', 'f_e']:
                    interpolator = getattr(bm, attr, None)
                    self.assertIsNotNone(interpolator, f"{attr} is missing.")
                    self.assertTrue(callable(interpolator), f"{attr} is not callable.")

                # Sanity test a typical interpolation value
                test_energy = 150.0  # MeV
                sx_val = bm.f_sx(test_energy)
                sy_val = bm.f_sy(test_energy)
                e_val = bm.f_e(test_energy)
                self.assertTrue(0.0 < sx_val < 20.0, f"f_sx returned unrealistic value: {sx_val}")
                self.assertTrue(0.0 < sy_val < 20.0, f"f_sy returned unrealistic value: {sy_val}")
                self.assertTrue(70.0 < e_val < 230.0, f"f_e returned unrealistic value: {e_val}")
