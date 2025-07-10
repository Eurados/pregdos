import logging
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)


def get_fwhm(sigma):
    return sigma * 2.0 * np.sqrt(2.0 * np.log(2.0))


class BeamModel():
    """Beam model from a given CSV file."""

    def __init__(self, fn: Path, nominal=True, beam_model_position=500.0):
        """
        Load a beam model given as a CSV file.

        Interpolation lookup can be done as a function of nominal energy (default, nominal=True),
        or as a function of actual energy (nominal=False).

        Header rows will be discarded and must be prefixed with '#'.

        Input columns for beam model:
            1) nominal energy [MeV]
            2) measured energy [MeV]
            3) energy spread 1 sigma [MeV]
            4) primary protons per MU [protons/MU]
            5) 1 sigma spot size x [mm]
            6) 1 sigma spot size y [mm]
        Optionally, 4 more columns may be given:
            7) 1 sigma divergence x [rad]
            8) 1 sigma divergence y [rad]
            9) cov (x, x') [mm]
            10) cov (y, y') [mm]

        TODO: get rid of scipy dependency
        """
        data = np.genfromtxt(fn, delimiter=",", invalid_raise=False, comments='#')

        # resolve by nominal energy
        if nominal:
            energy = data[:, 0]
        else:
            energy = data[:, 1]

        k = 'cubic'

        cols = len(data[0])
        logger.debug("Number of columns in beam model: %i", cols)

        self.has_divergence = False

        try:
            from scipy.interpolate import interp1d
        except ImportError:
            logger.error("scipy is not installed, cannot interpolate beam model.")
            logger.error("Please install pymchelper[dicom] or pymchelper[all] to us this feature.")
            return

        if cols in (6, 10):
            self.f_en = interp1d(energy, data[:, 0], kind=k)  # nominal energy [MeV]
            self.f_e = interp1d(energy, data[:, 1], kind=k)  # measured energy [MeV]
            self.f_espread = interp1d(energy, data[:, 2], kind=k)  # energy spread 1 sigma [% of measured energy]
            self.f_ppmu = interp1d(energy, data[:, 3], kind=k)  # 1e6 protons per MU  [1e6/MU]
            self.f_sx = interp1d(energy, data[:, 4], kind=k)  # 1 sigma x [mm]
            self.f_sy = interp1d(energy, data[:, 5], kind=k)  # 1 sigma y [mm]
        else:
            logger.error("invalid column count")

        if cols == 10:
            logger.debug("Beam model has divergence data")
            self.has_divergence = True
            self.f_divx = interp1d(energy, data[:, 6], kind=k)  # div x [rad]
            self.f_divy = interp1d(energy, data[:, 7], kind=k)  # div y [rad]
            self.f_covx = interp1d(energy, data[:, 8], kind=k)  # cov (x, x') [mm]
            self.f_covy = interp1d(energy, data[:, 9], kind=k)  # cov (y, y') [mm]

        self.data = data
        self.beam_model_position = beam_model_position  # position of the beam model in mm
