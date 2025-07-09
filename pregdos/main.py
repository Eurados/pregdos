import sys
import logging
from pathlib import Path

# from pregdos.__version__ import __version__

from pregdos.config_parser import create_parser
from pregdos.beam_model import BeamModel
from pregdos.plan_model import Plan
from pregdos.import_plan import load_plan
from pregdos.export_plan_topas import Topas

logger = logging.getLogger(__name__)


def export_plan(p: Plan,
                fbm: BeamModel,
                fout: Path,
                field_nr: int = 0,
                nominal: bool = True,
                nstat: int = int(1e6)) -> None:
    """
    Export a plan to a Topas-compatible format.
    """
    if field_nr < 1 or field_nr > p.n_fields:
        raise ValueError(f"Invalid field number: {field_nr}. Valid range is 1 to {p.n_fields}.")

    # append field number to output file name
    fout = fout.with_name(f"{fout.stem}_field{field_nr}{fout.suffix}")
    Topas.export(fout, p.fields[field_nr-1], fbm, nominal=nominal, nstat=nstat)


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

    # If field number is specified, export only that field.
    if parsed_args.field_nr >= 0:
        field_idx = parsed_args.field_nr
        logger.info(f"Exporting field number {field_idx} of {pln.n_fields} fields.")
        export_plan(pln, pln.beam_model, parsed_args.fout, field_nr=field_idx, nominal=param_nominal, nstat=parsed_args.nstat)
    else:
        logger.info("Exporting all fields.")
        for i in range(pln.n_fields):
            logger.info(f"Exporting field number {i} of {pln.n_fields} fields.")
            export_plan(pln, pln.beam_model, parsed_args.fout, field_nr=i, nominal=param_nominal, nstat=parsed_args.nstat)

    return 0


if __name__ == '__main__':
    sys.exit(main())
