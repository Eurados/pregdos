import argparse
from pathlib import Path

from pregdos.__version__ import __version__


def create_parser():
    parser = argparse.ArgumentParser(
        description="Convert DICOM CT and RTSTRUCT files to geometry needed for TOPAS.")

    parser.add_argument('ct_dir', type=Path,
                        help="(required) Path to DICOM CT directory containing the CT slices.")

    parser.add_argument('rs_file', nargs='?', type=Path, default=None,
                        help="Path to DICOM RTSTRUCT file. If omitted, will search CT_DIR for the first RS*.dcm file.")

    parser.add_argument('fout', nargs='?', type=Path, default="geometry.txt",
                        help="Output TOPAS geometry file (default: geometry.txt).")

    parser.add_argument('-v', '--verbosity', action='count', default=0,
                        help="Increase verbosity (can use -v, -vv, etc.).")

    parser.add_argument('-V', '--version', action='version', version=__version__,
                        help="Show version and exit.")

    return parser
