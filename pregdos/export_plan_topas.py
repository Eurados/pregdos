import logging
import numpy as np
from pathlib import Path

from pregdos.model_plan import Plan, Field
from pregdos.beam_model import BeamModel
from pregdos.topas_text import TopasText

logger = logging.getLogger(__name__)


# export_plan is a workflow function, not a core method of the TopasPlan abstraction
def export_plan(pln: Plan, bm: BeamModel, output_base_path: Path,
                field_nr: int = -1, nominal: bool = True,
                nstat: int = int(1e6)) -> None:
    """
    Export one or all fields from a Plan to Topas .txt files.
    If field_nr >= 1, export only that field.
    If field_nr < 0, export all fields with field number appended.
    """

    if field_nr >= 1:
        field = pln.fields[field_nr - 1]
        output_path = output_base_path.with_name(
            f"{output_base_path.stem}_field{field_nr}{output_base_path.suffix}"
        )
        topas_text = TopasPlan.generate(field, bm, nominal=nominal, nstat=nstat)
        output_path.write_text(topas_text)
    else:
        for i, field in enumerate(pln.fields, start=1):
            output_path = output_base_path.with_name(
                f"{output_base_path.stem}_field{i}{output_base_path.suffix}"
            )
            topas_text = TopasPlan.generate(field, bm, nominal=nominal, nstat=nstat)
            output_path.write_text(topas_text)


class TopasPlan:
    @staticmethod
    def generate(myfield: Field, bm: BeamModel, nominal: bool,
                 nstat=100000, test_mode=False, add_footer=True) -> str:
        """
        Export the field to a topas input file.
        """
        # topas needs long series of spots for each parameter.
        # energy, energyspread, posX, AngleX, posY, AngleY,
        # SigmaX, SigmaY, SigmaXprime, SigmaYprime, CorrelationX, CorrelationY, spotweight

        # calculate total number of sports
        n_spots = 0
        # loop over all spots in all layers in all fields:

        for mylayer in myfield.layers:
            for spot in mylayer.spots:
                n_spots += 1

        # establish size of output arrays
        times = np.zeros(n_spots)
        energies = np.zeros(n_spots)
        espreads = np.zeros(n_spots)
        posx = np.zeros(n_spots)
        angx = np.zeros(n_spots)
        posy = np.zeros(n_spots)
        angy = np.zeros(n_spots)
        sigx = np.zeros(n_spots)
        sigy = np.zeros(n_spots)
        sigxp = np.zeros(n_spots)
        sigyp = np.zeros(n_spots)
        corx = np.zeros(n_spots)
        cory = np.zeros(n_spots)
        nparts = np.zeros(n_spots)  # number of particles per spot, will be calculated from MUs

        # loop over all spots in all layers in all fields:
        # and fill the arrays
        _spot_index = 0
        _nlayer = 0
        for mylayer in myfield.layers:
            if nominal:
                energy = mylayer.energy_nominal
            else:
                # energy from scipy interpolation
                energy = mylayer.energy_measured
            espread = mylayer.espread

            sad_x = myfield.layers[0].sad[0]
            sad_y = myfield.layers[0].sad[1]

            for spot in mylayer.spots:
                times[_spot_index] = _spot_index + 1
                energies[_spot_index] = energy
                espreads[_spot_index] = espread
                # calculate position and angle in mm and degrees at beam model position,
                # which is upstream of the isocenter
                posx[_spot_index] = spot.x * (sad_x - bm.beam_model_position) / sad_x
                angx[_spot_index] = np.arctan(spot.x / sad_x) * 180.0 / np.pi
                posy[_spot_index] = spot.y * (sad_y - bm.beam_model_position) / sad_y
                angy[_spot_index] = np.arctan(spot.y / sad_y) * 180.0 / np.pi
                sigx[_spot_index] = bm.f_sx(mylayer.energy_nominal)
                sigy[_spot_index] = bm.f_sy(mylayer.energy_nominal)
                sigxp[_spot_index] = bm.f_divx(mylayer.energy_nominal)
                sigyp[_spot_index] = bm.f_divy(mylayer.energy_nominal)
                corx[_spot_index] = bm.f_covx(mylayer.energy_nominal)
                cory[_spot_index] = bm.f_covy(mylayer.energy_nominal)
                nparts[_spot_index] = spot.mu * mylayer.mu_to_part_coef  # convert MU to number of particles
                _spot_index += 1
            _nlayer += 1

        # show sad_x and sad_y
        logger.info(f"SAD X: {sad_x:.2f} mm, SAD Y: {sad_y:.2f} mm")

        total_number_of_particles = nparts.sum()
        nstat_scale = (nstat / total_number_of_particles) * myfield.scaling
        logger.info(f"Proton budget for this plan: {total_number_of_particles:.3e} protons")
        logger.info(f"Requested number of simulated particles: {nstat:.3e}")
        logger.info(f"Scaling factor:          {1 / nstat_scale:.4e}")
        logger.info(f"Number of spots:         {n_spots}")
        logger.info(f"Number of energy layers: {_nlayer}")
        logger.debug(f"Beam Meterset Weight:    {myfield.meterset_weight_final:.2f}")
        logger.info(f"Beam Meterset:           {myfield.cum_mu:.2f} MU")

        # build output lines instead of writing to file
        lines = []
        lines.append(f"#PARTICLE_SCALING = {1 / nstat_scale:.0f}\n")
        lines.append(f"#SOPInstanceUID = {myfield.sop_instance_uid}\n")
        lines.append(TopasText.variables(myfield))
        if test_mode:
            lines.append(TopasText.setup())
            lines.append(TopasText.world_setup())
            lines.append(TopasText.geometry_gantry())
            lines.append(TopasText.geometry_couch())
            lines.append(TopasText.geometry_dcm_to_iec())

        lines.append(TopasText.geometry_beam_position_timefeature())
        lines.append(TopasText.field_beam_timefeature())

        lines.append("##############################################\n")
        lines.append("###  T  I  M  E    F  E  A  T  U  R  E  S  ###\n")
        lines.append("##############################################\n")
        lines.append("\n")

        lines.append(f"i:Tf/NumberOfSequentialTimes         = {n_spots}\n")
        lines.append(f"d:Tf/TimelineStart                   = {1} s\n")
        lines.append(f"d:Tf/TimelineEnd                     = {n_spots+1} s\n")
        lines.append("\n")

        lines.append(_topas_array(times, energies, "Energy", "f", 3, "MeV"))
        lines.append(_topas_array(times, espreads, "EnergySpread", "f", 5, ""))
        lines.append(_topas_array(times, posx, "spotPositionX", "f", 2, "mm"))
        lines.append(_topas_array(times, angx, "spotAngleX", "f", 3, "deg"))
        lines.append(_topas_array(times, posy, "spotPositionY", "f", 2, "mm"))
        lines.append(_topas_array(times, angy, "spotAngleY", "f", 3, "deg"))
        lines.append(_topas_array(times, sigx, "SigmaX", "f", 5, "mm"))
        lines.append(_topas_array(times, sigy, "SigmaY", "f", 5, "mm"))
        lines.append(_topas_array(times, sigxp, "SigmaXprime", "f", 5, ""))
        lines.append(_topas_array(times, sigyp, "SigmaYprime", "f", 5, ""))
        lines.append(_topas_array(times, corx, "CorrelationX", "f", 5, ""))
        lines.append(_topas_array(times, cory, "CorrelationY", "f", 5, ""))
        lines.append(_topas_array(times, nparts * nstat_scale, "spotWeight", "f", 0, ""))

        lines.append(f"# Total number of particles: {total_number_of_particles * nstat_scale:.0f}\n")
        lines.append(f"# Total number of particles scaled down by {1 / nstat_scale:.0f}\n")
        lines.append(f"# Total MU in field: {myfield.cum_mu:.2f}\n")

        if add_footer:
            lines.append(TopasText.footer())

        topas_text = "".join(lines)
        return topas_text


def _topas_array(time_arr: np.array, arr: np.array, name: str, fmt: str = "f", precision: int = 0, unit=""):
    """generate string of time data."""
    s = ""
    n_spots = arr.size
    s += f"s:Tf/{name}/Function                 = \"Step\"\n"
    if unit == "":
        _pre = "uv"
    else:
        _pre = "dv"

    _ft = " ".join(map(str, time_arr.astype(int)))
    _fa = " ".join(f"{x:.{precision}{fmt}}" for x in arr)
    s += f"dv:Tf/{name}/Times                   = {n_spots} {_ft} s\n"
    s += f"{_pre}:Tf/{name}/Values                   = {arr.size} {_fa} {unit}\n"
    s += "\n\n"
    return s
