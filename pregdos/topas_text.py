import datetime
import getpass  # used for recording the user who generated the file
from pathlib import Path

from pregdos.model_plan import Field
from pregdos.model_ct import CTModel
from pregdos.model_rtstruct import RTStruct
from pregdos.__version__ import __version__


class TopasText:
    @staticmethod
    def header(field: Field, nstat_scale: float, nstat: int) -> str:
        lines = [
            f"# Topas input file for field {field.field_number}",
            '# ' + '-' * 40,
            f"# SOP_INSTANCE_UID {field.sop_instance_uid}",
            "# ",
            f"# TOTAL_NUMBER_OF_PARTICLES: {field.n_particles:.0f}",
            f"# TOTAL_MU: {field.cum_mu:.2f}",
            f"# REQUESTED_HISTORIES: {nstat:.0f}",
            f"# PARTICLE_SCALING: {nstat_scale:.2f}",
            "#\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def header2() -> str:
        "Add a footer to the topas file with generation date and username."

        lines = [
            f"# Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by user '{getpass.getuser()}'",
            f"# using pregdos {__version__}",
            "# https://github.com/Eurados/pregdos",
            "#\n"
        ]

        return "\n".join(lines)

    @staticmethod
    def spr_to_material(spr_path: Path) -> str:
        lines = [
            "##############################################",
            "###        SPR TO MATERIAL PATH            ###",
            "##############################################",
            f'includeFile                          = {spr_path}',
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def variables(myfield: Field) -> str:
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

    @staticmethod
    def setup(show_history_interval: int = 100000, nr_threads: int = 0) -> str:
        """
        Generate the TOPAS setup section.

        show_history_interval: Interval at which the history count is shown.
        nr_threads: 0 for using all cores, -1 for all but one.
        """

        # model1 = (
        #     'sv:Ph/Default/Modules                = 6 '
        #     '"g4em-standard_opt3" '
        #     '"g4h-phy_QGSP_BIC_HP" '
        #     '"g4decay" '
        #     '"g4ion-binarycascade" '
        #     '"g4h-elastic_HP" '
        #     '"g4stopping"'
        # )
        model2 = (
            'sv:Ph/Default/Modules                = 6 '
            '"g4em-standard_opt4" '
            '"g4h-phy_QGSP_BIC_AllHP" '
            '"g4decay" '
            '"g4ion-binarycascade" '
            '"g4h-elastic_HP" '
            '"g4stopping"'
        )

        lines = [
            "##############################################",
            "###         T O P A S    S E T U P         ###",
            "##############################################",
            f"# {model2}",
            f"i:Ts/ShowHistoryCountAtInterval         = {show_history_interval}",
            f"i:Ts/NumberOfThreads                    = {nr_threads}",
            "b:Ts/DumpParameters                     = \"False\"",
            "b:Ge/Patient/IgnoreInconsistentFrameOfReferenceUID = \"True\"",
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def world_setup() -> str:
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

    @staticmethod
    def geometry_patient_dicom(rd_path: Path) -> str:
        dicom_dir = str(rd_path.parent)
        rtdose_file = rd_path.name
        lines = [
            "##############################################",
            "###            G E O M E T R Y             ###",
            "##############################################",
            's:Ge/Patient/Parent                  = "World"',
            's:Ge/Patient/Type                    = "TsDicomPatient"',
            f's:Ge/Patient/DicomDirectory          = "{dicom_dir}"',
            'sv:Ge/Patient/DicomModalityTags      = 1 "CT"',
            f's:Ge/Patient/CloneRTDoseGridFrom     = Ge/Patient/DicomDirectory + "/{rtdose_file}"',
            'd:Ge/Patient/TransX                  = Ge/Patient/DicomOriginX - Rt/Plan/IsoCenterX mm',
            'd:Ge/Patient/TransY                  = Ge/Patient/DicomOriginY - Rt/Plan/IsoCenterY mm',
            'd:Ge/Patient/TransZ                  = Ge/Patient/DicomOriginZ - Rt/Plan/IsoCenterZ mm',
            'd:Ge/Patient/RotX                    = 0.00 deg',
            'd:Ge/Patient/RotY                    = 0.00 deg',
            'd:Ge/Patient/RotZ                    = 0.00 deg',
            's:Ge/Patient/Color                   = "Red"',
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def geometry_patient(ct: CTModel, rs: RTStruct) -> str:
        """
        Generate the geometry section for the patient.
        """
        lines = [
            "##############################################",
            "###         G E O M E T R Y   P A T I E N T ###",
            "##############################################",
            's:Ge/Patient/Parent                   = "World"',
            's:Ge/Patient/Type                     = "Group"',
            f'd:Ge/Patient/DicomOriginX             = {ct.dicom_origin[0]:.2f} mm',
            f'd:Ge/Patient/DicomOriginY             = {ct.dicom_origin[1]:.2f} mm',
            f'd:Ge/Patient/DicomOriginZ             = {ct.dicom_origin[2]:.2f} mm',
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def geometry_water_phantom(size: float = 300.0) -> str:
        """
        Generate the geometry section for the water phantom.
        """
        lines = [
            "##############################################",
            "###      G E O M E T R Y   WATERPHANTOM    ###",
            "##############################################",
            's:Ge/WaterPhantom/Parent                     = "Patient"',
            's:Ge/WaterPhantom/Type                       = "TsBox"',
            's:Ge/WaterPhantom/Material                   = "G4_Water"',
            f"d:Ge/WaterPhantom/HLX                        = {size:.2f} mm",
            f"d:Ge/WaterPhantom/HLY                        = {size:.2f} mm",
            f"d:Ge/WaterPhantom/HLZ                        = {size:.2f} mm",
            f'd:Ge/WaterPhantom/TransX                     = {0.0:.2f} mm',
            f'd:Ge/WaterPhantom/TransY                     = {0.0:.2f} mm',
            f'd:Ge/WaterPhantom/TransZ                     = {0.0:.2f} mm',
            f'd:Ge/WaterPhantom/RotX                       = {0.0:.2f} deg',
            f'd:Ge/WaterPhantom/RotY                       = {0.0:.2f} deg',
            f'd:Ge/WaterPhantom/RotZ                       = {0.0:.2f} deg',
            f'd:Ge/WaterPhantom/MaxStepSize                = {0.5:.2f} mm',
            'c:Ge/WaterPhantom/Color                     = "Blue"',
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def geometry_gantry() -> str:
        lines = [
            "##############################################",
            "###     G E O M E T R Y   G A N T R Y      ###",
            "##############################################",
            's:Ge/Gantry/Parent                   = "DCM_to_IEC"',
            's:Ge/Gantry/Type                     = "Group"',
            "d:Ge/Gantry/TransX                   = 0.00 mm",
            "d:Ge/Gantry/TransY                   = 0.00 mm",
            "d:Ge/Gantry/TransZ                   = 0.00 mm",
            "d:Ge/Gantry/RotX                     = 0.00 deg",
            "d:Ge/Gantry/RotY                     = Ge/gantryAngle deg",
            "d:Ge/Gantry/RotZ                     = 0.00 deg",
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def geometry_couch() -> str:
        lines = [
            "##############################################",
            "###      G E O M E T R Y    C O U C H      ###",
            "##############################################",
            's:Ge/Couch/Parent                  = "World"',
            's:Ge/Couch/Type                    = "Group"',
            "d:Ge/Couch/RotX                    = 0. deg",
            "d:Ge/Couch/RotY                    = -1.0 * Ge/couchAngle deg",
            "d:Ge/Couch/RotZ                    = 0. deg",
            "d:Ge/Couch/TransX                  = 0.0 mm",
            "d:Ge/Couch/TransY                  = 0.0 mm",
            "d:Ge/Couch/TransZ                  = 0.0 mm",
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def geometry_dcm_to_iec() -> str:
        lines = [
            "##############################################",
            "###      G E O M E T R Y    DCM_to_IEC     ###",
            "##############################################",
            's:Ge/DCM_to_IEC/Parent               = "Couch"',
            's:Ge/DCM_to_IEC/Type                 = "Group"',
            "d:Ge/DCM_to_IEC/TransX               = 0.0 mm",
            "d:Ge/DCM_to_IEC/TransY               = 0.0 mm",
            "d:Ge/DCM_to_IEC/TransZ               = 0.0 mm",
            "d:Ge/DCM_to_IEC/RotX                 = 90.00 deg",
            "d:Ge/DCM_to_IEC/RotY                 = 0.0 deg",
            "d:Ge/DCM_to_IEC/RotZ                 = 0.0 deg",
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def geometry_beam_position_timefeature(beam_model_position: float = 500.0) -> str:
        lines = [
            "##############################################",
            "###    GEOM.  B E A M   P O S I T I O N    ###",
            "##############################################",
            's:Ge/BeamPosition/Parent             = "Gantry"',
            's:Ge/BeamPosition/Type               = "Group"',
            f"d:Ge/BeamPosition/TransZ             = -{beam_model_position} mm",
            "d:Ge/BeamPosition/TransX             = Tf/spotPositionX/Value mm",
            "d:Ge/BeamPosition/TransY             = -1.0 * Tf/spotPositionY/Value mm",
            "d:Ge/BeamPosition/RotX               = -1.0 * Tf/spotAngleY/Value deg",
            "d:Ge/BeamPosition/RotY               = -1.0 * Tf/spotAngleX/Value deg",
            "d:Ge/BeamPosition/RotZ               = 0.00 deg",
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def geometry_range_shifter(myfield: Field) -> str:
        if myfield.range_shifter is None:
            return ""

        rs = myfield.range_shifter

        lines = [
            "##############################################",
            "###        R A N G E   S H I F T E R       ###",
            "##############################################",
            's:Ge/RangeShifter/Parent             = "Gantry"',
            's:Ge/RangeShifter/Type               = "TsBox"',
            f's:Ge/RangeShifter/Material           = "{rs.material}"',
            'b:Ge/RangeShifter/Isparallel         = "True"',
            'sv:Ph/Default/LayeredMassGeometryWorlds = 2 "Patient/RTDoseGrid" "RangeShifter"',
            f"d:Ge/RangeShifter/HLX                = {200:.2f} mm",
            f"d:Ge/RangeShifter/HLY                = {200:.2f} mm",
            f"d:Ge/RangeShifter/HLZ                = {rs.thickness*0.5:.2f} mm",
            's:Ge/RangeShifter/Color              = "Orange"',
            # TODO: not to center of RS?
            f'd:Ge/RangeShifter/TransZ            = {-(rs.isocenter_distance+rs.thickness*0.5):.2f} mm\n',
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def field_beam_timefeature() -> str:
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

    @staticmethod
    def scorer_setup_dicom(dose_to_water: bool = True, topas_output_path: str = "") -> str:
        lines = [
            "##############################################",
            "###       S C O R E R    S E T U P         ###",
            "##############################################",
        ]
        if dose_to_water:
            lines.append('s:Sc/Dose/Quantity                   = "DoseToWater"')
            lines.append('b:Sc/Dose/PreCalculateStoppingPowerRatios = "True"')
        else:
            lines.append('s:Sc/Dose/Quantity                   = "DoseToMedium"')
        lines.append('s:Sc/Dose/Component                  = "Patient/RTDoseGrid"')
        lines.append('s:Sc/Dose/ReferencedDicomPatient     = "Patient"')
        lines.append('s:Sc/Dose/IfOutputFileAlreadyExists  = "Overwrite"')
        lines.append('s:Sc/Dose/OutputType                 = "DICOM"')
        lines.append(f's:Sc/Dose/OutputFile                 = "{topas_output_path}"')
        lines.append('b:Sc/Dose/DICOMOutput32BitsPerPixel  = "F"')
        lines.append('\n')
        return "\n".join(lines)

    @staticmethod
    def scoring_box_x(size: float = 300.0) -> str:
        lines = [
            "##############################################",
            "###       S C O R E R    B O X     X       ###",
            "##############################################",
            's:Ge/ScoringXBox/Parent     = "World"',
            's:Ge/ScoringXBox/Type       = "TsBox"',
            'b:Ge/ScoringXBox/IsParallel = "TRUE"',
            f's:Ge/ScoringXBox/HLX       = {size:.2f} mm',
            f's:Ge/ScoringXBox/HLY       = {10.0:.2f} mm',
            f's:Ge/ScoringXBox/HLZ       = {10.0:.2f} mm',
            f's:Ge/ScoringXBox/XBins     = {size:d}',
            's:Ge/ScoringXBox/YBins     = 1',
            's:Ge/ScoringXBox/ZBins     = 1',
            's:Ge/ScoringXBox/Color      = "green"',
            's:Ge/ScoringXBox/TransX     = 0.0 mm',
            's:Ge/ScoringXBox/TransY     = 0.0 mm',
            's:Ge/ScoringXBox/TransZ     = 0.0 mm',
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def scoring_box_y(size: float = 300.0) -> str:
        lines = [
            "##############################################",
            "###       S C O R E R    B O X     Y       ###",
            "##############################################",
            's:Ge/ScoringYBox/Parent     = "World"',
            's:Ge/ScoringYBox/Type       = "TsBox"',
            'b:Ge/ScoringYBox/IsParallel = "TRUE"',
            f's:Ge/ScoringYBox/HLX       = {10.0:.2f} mm',
            f's:Ge/ScoringYBox/HLY       = {size:.2f} mm',
            f's:Ge/ScoringYBox/HLZ       = {10.0:.2f} mm',
            's:Ge/ScoringYBox/XBins     = 1',
            f's:Ge/ScoringYBox/YBins     = {size:d}',
            's:Ge/ScoringYBox/ZBins     = 1',
            's:Ge/ScoringYBox/Color      = "green"',
            's:Ge/ScoringYBox/TransX     = 0.0 mm',
            's:Ge/ScoringYBox/TransY     = 0.0 mm',
            's:Ge/ScoringYBox/TransZ     = 0.0 mm',
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def scoring_box_z(size: float = 300.0) -> str:
        lines = [
            "##############################################",
            "###       S C O R E R    B O X     Z       ###",
            "##############################################",
            's:Ge/ScoringZBox/Parent     = "World"',
            's:Ge/ScoringZBox/Type       = "TsBox"',
            'b:Ge/ScoringZBox/IsParallel = "TRUE"',
            f's:Ge/ScoringZBox/HLX       = {10.0:.2f} mm',
            f's:Ge/ScoringZBox/HLY       = {10.0:.2f} mm',
            f's:Ge/ScoringZBox/HLZ       = {size:.2f} mm',
            's:Ge/ScoringZBox/XBins     = 1',
            's:Ge/ScoringZBox/YBins     = 1',
            f's:Ge/ScoringZBox/ZBins     = {size:d}',
            's:Ge/ScoringZBox/Color      = "green"',
            's:Ge/ScoringZBox/TransX     = 0.0 mm',
            's:Ge/ScoringZBox/TransY     = 0.0 mm',
            's:Ge/ScoringZBox/TransZ     = 0.0 mm',
            "\n"
        ]
        return "\n".join(lines)

    # here do a XY scoring box with 2D binning in X an Y
    def scoring_box_xy(size_x: float = 300.0, size_y: float = 300.0) -> str:
        lines = [
            "##############################################",
            "###       S C O R E R    B O X     XY      ###",
            "##############################################",
            's:Ge/ScoringXYBox/Parent     = "World"',
            's:Ge/ScoringXYBox/Type       = "TsBox"',
            'b:Ge/ScoringXYBox/IsParallel = "TRUE"',
            f's:Ge/ScoringXYBox/HLX       = {size_x:.2f} mm',
            f's:Ge/ScoringXYBox/HLY       = {size_y:.2f} mm',
            f's:Ge/ScoringXYBox/HLZ       = {10.0:.2f} mm',
            f's:Ge/ScoringXYBox/XBins     = {size_x:d}',
            f's:Ge/ScoringXYBox/YBins     = {size_y:d}',
            's:Ge/ScoringXYBox/ZBins     = 1',
            's:Ge/ScoringXYBox/Color      = "green"',
            's:Ge/ScoringXYBox/TransX     = 0.0 mm',
            's:Ge/ScoringXYBox/TransY     = 0.0 mm',
            's:Ge/ScoringXYBox/TransZ     = 0.0 mm',
            "\n"
        ]
        return "\n".join(lines)

    def scoring_box_xz(size_x: float = 300.0, size_z: float = 300.0) -> str:
        lines = [
            "##############################################",
            "###       S C O R E R    B O X     XZ      ###",
            "##############################################",
            's:Ge/ScoringXZBox/Parent     = "World"',
            's:Ge/ScoringXZBox/Type       = "TsBox"',
            'b:Ge/ScoringXZBox/IsParallel = "TRUE"',
            f's:Ge/ScoringXZBox/HLX       = {size_x:.2f} mm',
            f's:Ge/ScoringXZBox/HLY       = {10.0:.2f} mm',
            f's:Ge/ScoringXZBox/HLZ       = {size_z:.2f} mm',
            f's:Ge/ScoringXZBox/XBins     = {size_x:d}',
            's:Ge/ScoringXZBox/YBins     = 1',
            f's:Ge/ScoringXZBox/ZBins     = {size_z:d}',
            's:Ge/ScoringXZBox/Color      = "green"',
            's:Ge/ScoringXZBox/TransX     = 0.0 mm',
            's:Ge/ScoringXZBox/TransY     = 0.0 mm',
            's:Ge/ScoringXZBox/TransZ     = 0.0 mm',
            "\n"
        ]
        return "\n".join(lines)

    def scoring_box_yz(size_y: float = 300.0, size_z: float = 300.0) -> str:
        lines = [
            "##############################################",
            "###       S C O R E R    B O X     YZ      ###",
            "##############################################",
            's:Ge/ScoringYZBox/Parent     = "World"',
            's:Ge/ScoringYZBox/Type       = "TsBox"',
            'b:Ge/ScoringYZBox/IsParallel = "TRUE"',
            f's:Ge/ScoringYZBox/HLX       = {10.0:.2f} mm',
            f's:Ge/ScoringYZBox/HLY       = {size_y:.2f} mm',
            f's:Ge/ScoringYZBox/HLZ       = {size_z:.2f} mm',
            's:Ge/ScoringYZBox/XBins     = 1',
            f's:Ge/ScoringYZBox/YBins     = {size_y:d}',
            f's:Ge/ScoringYZBox/ZBins     = {size_z:d}',
            's:Ge/ScoringYZBox/Color      = "green"',
            's:Ge/ScoringYZBox/TransX     = 0.0 mm',
            's:Ge/ScoringYZBox/TransY     = 0.0 mm',
            's:Ge/ScoringYZBox/TransZ     = 0.0 mm',
            "\n"
        ]
        return "\n".join(lines)

    @staticmethod
    def scoring_water_phantom(component: str, outpath: Path) -> str:
        """ Generate the scoring section for the water phantom.
        Args:
            component (str): The component to score, e.g., "ScoringYBox".
            outpath (Path): The output file path for the scoring data.
        """
        lines = [
            "##############################################",
            "###       S C O R E R    W A T E R         ###",
            "##############################################",
            's:Sc/Scoring_WaterPhantom/Quantity                   = "DoseToWater"',
            f's:Sc/Scoring_WaterPhantom/Component                  = "{component}"',
            's:Sc/Scoring_WaterPhantom/IfOutputFileAlreadyExists  = "Overwrite"',
            's:Sc/Scoring_WaterPhantom/PropagateToChildren        = "True"',
            f's:Sc/Scoring_WaterPhantom/OutputFile                 = "{outpath}"',
            "\n"
        ]
        return "\n".join(lines)
