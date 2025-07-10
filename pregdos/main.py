# placeholder for geometry export functionality
import sys
import logging
from pathlib import Path

from pregdos.parser_main import create_parser
from pregdos.beam_model import BeamModel
from pregdos.import_ct import load_ct
from pregdos.import_rtstruct import load_rs
from pregdos.import_plan import load_plan
from pregdos.export_study_topas import export_study_topas

logger = logging.getLogger(__name__)


def get_path_dicom_dose(study_dir: Path) -> Path:
    """
    Get the path to the DICOM RTDOSE file in the study directory.
    The file should start with 'RD' and end with '.dcm'.
    """
    dose_files = list(study_dir.glob('RD*.dcm'))
    if not dose_files:
        raise FileNotFoundError("No DICOM RTDOSE file found in the study directory.")
    if len(dose_files) > 1:
        # we will use the first one found
        logger.warning("Multiple DICOM RTDOSE files found, using the first one.")
    return dose_files[0]


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
    study_dir = parsed_args.study_dir
    ct = load_ct(study_dir)
    ct.spr_to_material_path = parsed_args.spr_to_material_path

    rs = load_rs(study_dir)
    bm = BeamModel(parsed_args.bm,
                   nominal=True,
                   beam_model_position=parsed_args.beam_model_position)

    pn = load_plan(study_dir)
    pn.beam_model = bm
    pn.apply_beammodel()

    rd_path = get_path_dicom_dose(study_dir)

    # export the geometry file
    export_study_topas(ct, rs, pn,
                       parsed_args.output_base_path,
                       field_nr=parsed_args.field_nr,
                       dose_path=rd_path,
                       nstat=parsed_args.nstat)

    return 0


if __name__ == '__main__':
    sys.exit(main())
