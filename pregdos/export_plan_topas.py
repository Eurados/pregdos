import numpy as np
from pathlib import Path
import logging
import datetime
import getpass  # used for recording the user who generated the file

from pregdos.__version__ import __version__
from pregdos.plan_model import Field
from pregdos.beam_model import BeamModel

logger = logging.getLogger(__name__)

# TODO: implement CT setup and Water phantom setup.


class Topas:
    @staticmethod
    def export(fn: Path, myfield: Field, bm: BeamModel, nominal: bool,
               nstat=100000):
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

        # open output file for writing
        with open(fn, "w") as f:
            f.write(f"#PARTICLE_SCALING = {1 / nstat_scale:.0f}\n")
            f.write(f"#SOPInstanceUID = {myfield.sop_instance_uid}\n")
            f.write(_topas_variables(myfield))
            f.write(_topas_setup())
            f.write(_topas_world_setup())
            f.write(_topas_geometry())
            f.write(_topas_beam())

            f.write("##############################################\n")
            f.write("###  T  I  M  E    F  E  A  T  U  R  E  S  ###\n")
            f.write("##############################################\n")
            f.write("\n")

            f.write(f"i:Tf/NumberOfSequentialTimes         = {n_spots}\n")
            f.write(f"d:Tf/TimelineStart                   = {1} s\n")
            f.write(f"d:Tf/TimelineEnd                     = {n_spots+1} s\n")
            f.write("\n")

            f.write(_topas_array(times, energies, "Energy", "f", 3, "MeV"))
            f.write(_topas_array(times, espreads, "EnergySpread", "f", 5, ""))
            f.write(_topas_array(times, posx, "spotPositionX", "f", 2, "mm"))
            f.write(_topas_array(times, angx, "spotAngleX", "f", 3, "deg"))
            f.write(_topas_array(times, posy, "spotPositionY", "f", 2, "mm"))
            f.write(_topas_array(times, angy, "spotAngleY", "f", 3, "deg"))
            f.write(_topas_array(times, sigx, "SigmaX", "f", 5, "mm"))
            f.write(_topas_array(times, sigy, "SigmaY", "f", 5, "mm"))
            f.write(_topas_array(times, sigxp, "SigmaXprime", "f", 5, ""))
            f.write(_topas_array(times, sigyp, "SigmaYprime", "f", 5, ""))
            f.write(_topas_array(times, corx, "CorrelationX", "f", 5, ""))
            f.write(_topas_array(times, cory, "CorrelationY", "f", 5, ""))
            f.write(_topas_array(times, nparts * nstat_scale, "spotWeight", "f", 0, ""))

            f.write(f"#Total number of particles: {total_number_of_particles * nstat_scale:.0f}\n")
            f.write(f"#Total number of particles scaled down by {1 / nstat_scale:.0f}\n")
            f.write(f"#Total MU in field: {myfield.cum_mu:.2f}\n")

            f.write(_topas_footer())
        logger.info(f"Topas input file written to '{fn}'")


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


def _topas_variables(myfield: Field) -> str:
    # Extract isocenter, gantry, couch, and snout_position from the first layer
    layer = myfield.layers[0]  # varying isocenter, gantry, couch, snout_position per controlpoint is not supported.
    isocenter = getattr(layer, "isocenter", [0.0, 0.0, 0.0])
    gantry_angle = getattr(layer, "gantry_angle", 0.0)
    couch_angle = getattr(layer, "couch_angle", 0.0)
    dicom_origin = getattr(layer, "dicom_origin", [0.0, 0.0, 0.0])
    snout_position = getattr(layer, "snout_position", 421.0)

    lines = [
        "##############################################",
        "###           V A R I A B L E S            ###",
        "##############################################",
        "",
        f"d:Rt/Plan/IsoCenterX                 = {isocenter[0]:.2f} mm",
        f"d:Rt/Plan/IsoCenterY                 = {isocenter[1]:.2f} mm",
        f"d:Rt/Plan/IsoCenterZ                 = {isocenter[2]:.2f} mm",
        f"d:Ge/snoutPosition                   = {snout_position:.2f} mm",
        f"d:Ge/gantryAngle                     = {gantry_angle:.2f} deg",
        f"d:Ge/couchAngle                      = {couch_angle:.2f} deg",
        f"dc:Ge/Patient/DicomOriginX           = {dicom_origin[0]:.2f} mm",
        f"dc:Ge/Patient/DicomOriginY           = {dicom_origin[1]:.2f} mm",
        f"dc:Ge/Patient/DicomOriginZ           = {dicom_origin[2]:.2f} mm",
        "\n"
    ]
    return "\n".join(lines)


def _topas_setup() -> str:
    lines = [
        "##############################################",
        "###         T O P A S    S E T U P         ###",
        "##############################################",
        "# sv:Ph/Default/Modules                = 6 \"g4em-standard_opt3\" "
        "\"g4h-phy_QGSP_BIC_HP\" \"g4decay\" \"g4ion-binarycascade\" "
        "\"g4h-elastic_HP\" \"g4stopping\"",
        "i:Ts/ShowHistoryCountAtInterval         = 100000",
        "i:Ts/NumberOfThreads                    = 0 # 0 for using all cores, -1 for all but one",
        "b:Ts/DumpParameters                     = \"False\"",
        "b:Ge/Patient/IgnoreInconsistentFrameOfReferenceUID = \"True\"",
        "\n"
    ]
    return "\n".join(lines)


def _topas_world_setup() -> str:
    lines = [
        "##############################################",
        "###         W O R L D    S E T U P         ###",
        "##############################################",
        's:Ge/World/Type            = "TsBox"',
        's:Ge/World/Material        = "Air"',
        "d:Ge/World/HLX             = 90. cm",
        "d:Ge/World/HLY             = 90. cm",
        "d:Ge/World/HLZ             = 90. cm",
        'b:Ge/World/Invisible       = "True"',
        "\n"
    ]
    return "\n".join(lines)


def _topas_geometry() -> str:
    lines = [
        "##############################################",
        "###            G E O M E T R Y             ###",
        "##############################################",
        's:Ge/Gantry/Parent                   = "DCM_to_IEC"',
        's:Ge/Gantry/Type                     = "Group"',
        "d:Ge/Gantry/TransX                   = 0.00 mm",
        "d:Ge/Gantry/TransY                   = 0.00 mm",
        "d:Ge/Gantry/TransZ                   = 0.00 mm",
        "d:Ge/Gantry/RotX                     = 0.00 deg",
        "d:Ge/Gantry/RotY                     = Ge/gantryAngle deg",
        "d:Ge/Gantry/RotZ                     = 0.00 deg",
        "",
        's:Ge/Couch/Parent                  = "World"',
        's:Ge/Couch/Type                    = "Group"',
        "d:Ge/Couch/RotX                    = 0. deg",
        "d:Ge/Couch/RotY                    = -1.0 * Ge/couchAngle deg",
        "d:Ge/Couch/RotZ                    = 0. deg",
        "d:Ge/Couch/TransX                  = 0.0 mm",
        "d:Ge/Couch/TransY                  = 0.0 mm",
        "d:Ge/Couch/TransZ                  = 0.0 mm",
        "",
        's:Ge/DCM_to_IEC/Parent               = "Couch"',
        's:Ge/DCM_to_IEC/Type                 = "Group"',
        "d:Ge/DCM_to_IEC/TransX               = 0.0 mm",
        "d:Ge/DCM_to_IEC/TransY               = 0.0 mm",
        "d:Ge/DCM_to_IEC/TransZ               = 0.0 mm",
        "d:Ge/DCM_to_IEC/RotX                 = 90.00 deg",
        "d:Ge/DCM_to_IEC/RotY                 = 0.0 deg",
        "d:Ge/DCM_to_IEC/RotZ                 = 0.0 deg",
        "",
        's:Ge/BeamPosition/Parent             = "Gantry"',
        's:Ge/BeamPosition/Type               = "Group"',
        "d:Ge/BeamPosition/TransZ             = -500.0 mm",
        "d:Ge/BeamPosition/TransX             = Tf/spotPositionX/Value mm",
        "d:Ge/BeamPosition/TransY             = -1.0 * Tf/spotPositionY/Value mm",
        "d:Ge/BeamPosition/RotX               = -1.0 * Tf/spotAngleY/Value deg",
        "d:Ge/BeamPosition/RotY               = -1.0 * Tf/spotAngleX/Value deg",
        "d:Ge/BeamPosition/RotZ               = 0.00 deg",
        "\n"
    ]
    return "\n".join(lines)


def _topas_beam() -> str:
    lines = [
        "##############################################",
        "###               B  E  A  M               ###",
        "##############################################",
        's:So/Field/Type                      = "Emittance"',
        's:So/Field/Component                 = "BeamPosition"',
        's:So/Field/BeamParticle              = "proton"',
        "d:So/Field/BeamEnergy                = Tf/Energy/Value MeV",
        "u:So/Field/BeamEnergySpread          = Tf/EnergySpread/Value",
        's:So/Field/Distribution              = "BiGaussian"',
        "d:So/Field/SigmaX                    = Tf/SigmaX/Value mm",
        "d:So/Field/SigmaY                    = Tf/SigmaY/Value mm",
        "u:So/Field/SigmaXprime               = Tf/SigmaXprime/Value",
        "u:So/Field/SigmaYprime               = Tf/SigmaYprime/Value",
        "u:So/Field/CorrelationX              = Tf/CorrelationX/Value",
        "u:So/Field/CorrelationY              = Tf/CorrelationY/Value",
        "",
        "i:So/Field/NumberOfHistoriesInRun    = Tf/spotWeight/Value",
        "\n"
    ]
    return "\n".join(lines)


def _topas_range_shifter(myfield: Field) -> str:
    if Field.range_shifter_thickness is None:
        return ""

    lines = [
        "##############################################",
        "###         R A N G E   S H I F T E R       ###",
        "##############################################",
        's:Ge/RangeShifter/Parent             = "Gantry"',
        's:Ge/RangeShifter/Type               = "TsBox"',
        's:Ge/RangeShifter/Material           = "Lexan"',
        'b:Ge/RangeShifter/Isparallel         = "True"',
        f"d:Ge/RangeShifter/HLX                = {200:.2f} mm",
        f"d:Ge/RangeShifter/HLY                = {200:.2f} mm",
        f"d:Ge/RangeShifter/HLZ                = {myfield.range_shifter_thickness/2:.2f} mm",
        's:Ge/RangeShifter/Color              = "Orange"',
        f'd:Ge/RangeShifter/TransZ            = {-myfield.range_shifter_distance:.2f} mm\n',  # TODO: not to center of RS.
        "\n"
    ]
    return "\n".join(lines)


def _topas_footer() -> str:
    "Add a footer to the topas file with generation date and username."

    lines = [
        "\n",
        f"# Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by user '{getpass.getuser()}'" +
        f" using dicomfix {__version__}",
        "# https://github.com/nbassler/dicomfix"]

    return "\n".join(lines)
