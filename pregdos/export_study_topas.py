import logging
from pathlib import Path

from pregdos.model_ct import CTModel
from pregdos.model_rtstruct import RTStruct
from pregdos.beam_model import BeamModel
from pregdos.topas_text import TopasText
from pregdos.model_plan import Plan, Field
from pregdos.export_plan_topas import TopasPlan


logger = logging.getLogger(__name__)


def export_study_topas(ct: CTModel, rs: RTStruct, plan: Plan, output_base_path: Path,
                       field_nr: int = 0, dose_path: Path = None, nstat: int = int(1e6)) -> None:
    """
    Export the CT and RTStruct models to a Topas-compatible geometry file.
    """

    if field_nr < 0 or field_nr >= len(plan.fields):
        raise ValueError(f"Invalid field number: {field_nr}. Must be between 0 and {len(plan.fields) - 1}.")
    if field_nr == 0:
        # Export all fields
        for field in plan.fields:
            logger.info("=" * 50)
            logger.info(f"Exporting field {field.field_number} to Topas geometry file...")
            logger.info("=" * 50)
            _export_study_field_topas(ct, rs, field, plan.beam_model, output_base_path, dose_path, nstat=nstat)
            logger.info("-" * 50 + "\n")
    else:
        # Export a single field
        field = plan.fields[field_nr]
        _export_study_field_topas(ct, rs, field, plan.beam_model, output_base_path, dose_path, nstat=nstat)


def _export_study_field_topas(ct: CTModel, rs: RTStruct, fld: Field, bm: BeamModel, output_base_path: Path,
                              dose_path: Path = None, nstat: int = int(1e6)) -> None:
    """
    Export a single field to a Topas-compatible geometry file.
    """
    # topas results will be written to output/field_number (no extension, will be handled by Topas
    # make target string for output file:
    topas_output_file_str_no_suffix = output_base_path.with_name(f"{output_base_path.stem}_field{fld.field_number}")

    nstat_scale = TopasPlan.calculate_scaling_factor(fld, nstat)

    lines = []
    lines.append(TopasText.header(fld, nstat_scale, nstat))
    lines.append(TopasText.header2())
    lines.append(TopasText.spr_to_material(ct.spr_to_material_path))
    lines.append(TopasText.variables(fld))
    lines.append(TopasText.setup())
    lines.append(TopasText.world_setup())
    lines.append(TopasText.geometry_patient_dicom(dose_path))
    lines.append(TopasText.geometry_gantry())
    lines.append(TopasText.geometry_couch())
    lines.append(TopasText.geometry_dcm_to_iec())
    lines.append(TopasText.geometry_beam_position_timefeature(bm.beam_model_position))
    lines.append(TopasText.geometry_range_shifter(fld))
    lines.append(TopasText.field_beam_timefeature())
    lines.append(TopasText.scorer_setup_dicom(topas_output_path=topas_output_file_str_no_suffix))
    lines.append(TopasPlan.time_features_string(fld, bm, nominal=True, nstat=1000000))
    topas_string = "\n".join(lines)

    # show some information about the field
    TopasPlan.show_plan_data(fld, bm, nstat=nstat)

    output_path = output_base_path.with_name(f"{output_base_path.stem}_field{fld.field_number}.txt")
    output_path.write_text(topas_string)
    logger.info(f"Wrote Topas geometry file for field {fld.field_number}: {output_path.resolve()}")
