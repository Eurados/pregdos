# placeholder for geometry export functionality
import sys
import logging
from pathlib import Path

from parser_geo_export import create_parser
from pregdos.import_ct import load_ct
from pregdos.import_rtstruct import load_rs
from pregdos.export_geo_topas import TopasGeo

logger = logging.getLogger(__name__)


def export_geo(ct, rs, fout: Path) -> None:
    TopasGeo.export(ct, rs, fout)
    return None


def main(args=None) -> int:

    if args is None:
        args = sys.argv[1:]

    parser = create_parser()
    parsed_args = parser.parse_args(args)

    if parsed_args.verbosity == 1:
        logging.basicConfig(level=logging.INFO)

    if parsed_args.verbosity > 1:
        logging.basicConfig(level=logging.DEBUG)

    # load the CT files
    ct_dir = parsed_args.ct_dir
    ct = load_ct(ct_dir)

    if parsed_args.rs_file is None:
        # try to find the first RTSTRUCT file in the CT directory
        rs = load_rs(ct_dir)
    else:
        rs = load_rs(parsed_args.rs_file)

    # export the geometry file
    export_geo(ct, rs, parsed_args.fout)

    return 0


if __name__ == '__main__':
    sys.exit(main())
