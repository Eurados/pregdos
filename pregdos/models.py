"""Data classes shared across the pregdos package.

These are plain dataclasses with no behaviour — they exist only to give a
named, type-checked shape to the data that flows between the web routes,
the dicomexport subprocess call, and the TOPAS scorer post-processing step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(slots=True)
class StructureSelection:
    """RT structure names available in a study and the subset chosen by the user.

    Populated after parsing the RTSTRUCT DICOM file; passed to the structure
    selection page so the user can decide which ROIs to keep in the conversion.
    """

    study_dir: str
    """Absolute path to the study directory containing CT/RS/RN/RP DICOM files."""
    available_structures: List[str] = field(default_factory=list)
    """All ROI names found in the RTSTRUCT file."""
    selected_structures: List[str] = field(default_factory=list)
    """ROI names chosen by the user; subset of available_structures."""


@dataclass(slots=True)
class ConversionParameters:
    """Input parameters for a single dicomexport invocation.

    dicomexport converts a DICOM RT plan + CT study into one TOPAS input file
    per treatment field.  These parameters control which study and beam model
    are used and how many primary particles the simulation will run.
    """

    study_dir: str
    """Path to the (possibly ROI-filtered) study directory passed to dicomexport."""
    beam_model_path: str
    """CSV file containing the pencil-beam model (energy-dependent spot parameters)."""
    spr_table_path: str
    """Text file mapping CT Hounsfield units to stopping-power ratios (SPR)."""
    output_base: str
    """Base path for output files; dicomexport appends ``_fieldNN.txt`` suffixes."""
    field_nr: Optional[int] = None
    """If set, export only this treatment field (1-based index).
    None means export all fields."""
    nstat: Optional[int] = None
    """Number of primary proton histories per spot weight.
    Passed through as the ``-N`` flag to dicomexport; None uses the default."""


@dataclass(slots=True)
class ConversionResult:
    """Output produced by a successful dicomexport run.

    Contains both the basenames (used in the web UI and for SLURM job
    submission) and the absolute paths (used for immediate post-processing
    by the scorer injection step).
    """

    out_files: List[str]
    """Basenames of the generated TOPAS input files, e.g. ``["topas_field01.txt"]``."""
    out_file_paths: List[str]
    """Absolute paths of the same files.  Used by append_scorers() to modify
    the files immediately after dicomexport finishes."""
    study_name: str
    """Name of the (filtered) study directory; used to construct download URLs."""
    selected_structures: List[str] = field(default_factory=list)
    """ROI names that were retained in the RTSTRUCT for this conversion."""
    stdout: Optional[str] = None
    """Captured standard output from the dicomexport process (for diagnostics)."""
    stderr: Optional[str] = None
    """Captured standard error from the dicomexport process (for diagnostics)."""
