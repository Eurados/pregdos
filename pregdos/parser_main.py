import argparse
from pathlib import Path

from pregdos.__version__ import __version__


def create_parser():
    parser = argparse.ArgumentParser(
        description="Convert DICOM CT and RTSTRUCT files to geometry needed for TOPAS.")

    parser.add_argument('study_dir', type=Path,
                        help="(required) Path to folder containing the study."
                        "The folder should contain"
                        " a) DICOM CT series(CT*.dcm) and"
                        " b) one DICOM RTSTRUCT file (RS*.dcm) and"
                        " c) one DICOM RTPLAN file (RN*.dcm) and"
                        " d) at least one DICOM RTDOSE file (RD*.dcm) where the resulting dose distribution will be stored.")

    parser.add_argument('output_base_path', nargs='?', type=Path, default="topas.txt",
                        help="Output TOPAS geometry file (default: topas.txt). \
                            Field number will be appended automatically to the name before the extension.")

    parser.add_argument('-b', '--beam-model', type=Path, dest='bm', help="(required) Beam model CSV path", default=None)

    parser.add_argument('-s', '--spr-to-material', type=Path, dest='spr_to_material_path',
                        help="(required) SPR to material mapping CSV path", default=None)

    parser.add_argument('-p', '--beam-model-position', type=float, dest='beam_model_position',
                        help="Beam model position in mm.", default=500.0)

    parser.add_argument('-f', '--field', type=int, dest='field_nr', default=0,
                        help="Field number to export. If not specified, all fields will be exported.")

    parser.add_argument('-N', '--nstat', type=int, dest='nstat', help="Target protons for simulation", default=int(1e6))

    parser.add_argument('-v', '--verbosity', action='count', default=0,
                        help="Increase verbosity (can use -v, -vv, etc.).")

    parser.add_argument('-V', '--version', action='version', version=__version__,
                        help="Show version and exit.")

    return parser
