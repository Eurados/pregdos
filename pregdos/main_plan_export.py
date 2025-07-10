import sys
import logging

from parser_plan_export import create_parser
from pregdos.beam_model import BeamModel
from pregdos.import_plan import load_plan
from pregdos.export_plan_topas import export_plan

logger = logging.getLogger(__name__)


def main(args=None) -> int:

    if args is None:
        args = sys.argv[1:]

    parser = create_parser()
    parsed_args = parser.parse_args(args)

    if parsed_args.verbosity == 1:
        logging.basicConfig(level=logging.INFO)

    if parsed_args.verbosity > 1:
        logging.basicConfig(level=logging.DEBUG)

    # Check plan file
    if not parsed_args.fin.exists():
        logger.error(f"Input plan file not found: {parsed_args.fin}")
        return 1

    # set nominal/actual energy lookup mode
    param_nominal = not parsed_args.actual

    # load the plan
    pln = load_plan(parsed_args.fin)

    if parsed_args.diag:
        print("Plan diagnostics:")
        print(pln)
        return 0

    # Next, load the beam model.
    if not parsed_args.fbm:
        logger.error("No beam model provided. Use -b to specify a beam model CSV file.")
        raise ValueError("Beam model file is required.")

    logger.info(f"beam model position: {parsed_args.beam_model_position} mm")

    pln.beam_model = BeamModel(parsed_args.fbm,
                               nominal=not parsed_args.actual,
                               beam_model_position=parsed_args.beam_model_position)
    logger.debug("Applying beam model to plan...")
    pln.apply_beammodel()

    logger.debug("Exporting plan to Topas format...")
    export_plan(pln, pln.beam_model, parsed_args.fout,
                field_nr=parsed_args.field_nr,
                nominal=param_nominal,
                nstat=parsed_args.nstat)

    return 0


if __name__ == '__main__':
    sys.exit(main())
