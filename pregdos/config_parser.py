import argparse
from pathlib import Path

from pregdos.__version__ import __version__


def create_parser():
    parser = argparse.ArgumentParser(
        description="Convert DICOM-RT Ion plans to MC-compatible spot lists using a beam model."
    )

    parser.add_argument('fin', type=Path, help="Input DICOM-RN or IBA .pld file")
    parser.add_argument('fout', nargs='?', type=Path, default="plan.txt",
                        help="Output file, default: plan.txt. Field number will be "
                        "appended automatically to the name before the extension.")
    parser.add_argument('-b', '--beam-model', type=Path, dest='fbm', help="Beam model CSV path", default=None)
    parser.add_argument('-p', '--beam-model-position', type=float, dest='beam_model_position',
                        help="Beam model position in mm", default=500.0)
    parser.add_argument('-f', '--field', type=int, dest='field_nr', default=0,
                        help="Field number to export. If not specified, all fields will be exported.")
    parser.add_argument('-d', '--diag', action='store_true', dest="diag",
                        help="Print plan diagnostics and exit", default=False)
    parser.add_argument('-a', '--actual-energy', action='store_true', dest="actual",
                        help="Plan does actual energy lookup, rather than nominal energy lookup.", default=False)
    parser.add_argument('-s', '--scale', type=float, dest='scale',
                        help="additional scaling multiplier for MC plan", default=1.0)
    parser.add_argument('-N', '--nstat', type=int, dest='nstat', help="Target protons for simulation", default=int(1e6))

    parser.add_argument('-v', '--verbosity', action='count', help="Increase verbosity", default=0)
    parser.add_argument('-V', '--version', action='version', version=__version__)

    return parser
