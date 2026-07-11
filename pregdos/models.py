"""Data classes shared across the pregdos package.

These are plain dataclasses with no behaviour — they exist only to give a
named, type-checked shape to the data that flows between the web routes,
the dicomexport subprocess call, and the TOPAS scorer post-processing step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(slots=True)
class ConversionParameters:
    """Input parameters for a single dicomexport invocation.

    dicomexport converts a DICOM RT plan + CT study into one TOPAS input file
    per treatment field.

    Every path here is **relative to** :attr:`run_dir`, which is also the working
    directory dicomexport is invoked from and the directory TOPAS will later run in.
    dicomexport copies its arguments verbatim into the generated TOPAS input, so
    relative arguments produce relative ``DicomDirectory`` / ``includeFile`` entries,
    and the study directory stays movable.  See :mod:`pregdos.studies`.
    """

    study_name: str
    """Name of the study this conversion belongs to (never a filesystem path)."""
    run_dir: str
    """Absolute path of the freshly created run directory (the dicomexport/TOPAS cwd)."""
    dicom_rel: str
    """DICOM directory, relative to ``run_dir`` -- normally ``"../dicom"``."""
    beam_model_rel: str
    """Pencil-beam model CSV, relative to ``run_dir``.  Read by dicomexport at
    generation time only; its path is never embedded in the TOPAS input."""
    spr_table_rel: str
    """HU-to-material (SPR) table, relative to ``run_dir``.  Embedded verbatim into
    the generated file as ``includeFile``, so it must stay relative."""
    output_basename: str
    """Base name for output files; dicomexport appends ``_fieldNN.txt`` suffixes."""
    field_nr: Optional[int] = None
    """If set, export only this treatment field (1-based index).
    None means export all fields."""
    nstat: Optional[int] = None
    """Number of primary proton histories per spot weight.
    Passed through as the ``-N`` flag to dicomexport; None uses the default."""


@dataclass(slots=True)
class ConversionResult:
    """Output produced by a successful dicomexport run.

    The generated files are simply *the contents of the run directory*, which did not
    exist before this conversion started -- there is nothing to disambiguate.
    """

    out_files: List[str]
    """Basenames of the generated TOPAS input files, e.g. ``["topas_field01.txt"]``."""
    study_name: str
    """Study the conversion belongs to; used to construct download/submit URLs."""
    run_id: str
    """Identifier of the run directory holding these files, e.g. ``"run_20260709_143000"``."""
    selected_structures: List[str] = field(default_factory=list)
    """ROI names the user chose to score in this conversion."""
    stdout: Optional[str] = None
    """Captured standard output from the dicomexport process (for diagnostics)."""
    stderr: Optional[str] = None
    """Captured standard error from the dicomexport process (for diagnostics)."""
