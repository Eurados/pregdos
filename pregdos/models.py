from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(slots=True)
class StructureSelection:
    """
    study_dir: Absolute path to the study directory containing CT/RS/RN/RP files.
    available_structures: ROI names discovered in the RTSTRUCT.
    selected_structures: ROI names chosen for conversion (subset of available).
    """

    study_dir: str
    available_structures: List[str] = field(default_factory=list)
    selected_structures: List[str] = field(default_factory=list)


@dataclass(slots=True)
class ConversionParameters:
    """
    study_dir: Path to (possibly filtered) study directory.
    beam_model_path: CSV file with beam model.
    spr_table_path: SPR-to-material table file.
    output_base: Base path for output; dicomexport appends field suffixes.
    field_nr: Optional single field number (1-based) to export; None = all.
    nstat: Optional number of primary particles (-N) passed through to dicomexport.
    """

    study_dir: str
    beam_model_path: str
    spr_table_path: str
    output_base: str
    field_nr: Optional[int] = None
    nstat: Optional[int] = None


@dataclass(slots=True)
class ConversionResult:
    """
    out_files: Basenames of generated TOPAS input files.
    study_name: Name of (filtered) study directory used in conversion.
    selected_structures: Structures retained in RTSTRUCT.
    stdout/stderr: Captured process output for diagnostics.
    """

    out_files: List[str]
    study_name: str
    selected_structures: List[str] = field(default_factory=list)
    stdout: Optional[str] = None
    stderr: Optional[str] = None
