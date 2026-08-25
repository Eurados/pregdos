"""Generate and inject TOPAS scorer configuration blocks for fetus dosimetry.

Workflow
--------
dicomexport produces a TOPAS input file (topas_fieldNN.txt) that contains one
scorer: absorbed dose to water on the RTDoseGrid.  That scorer is suitable for
in-field dose verification but not for out-of-field fetus dosimetry.

This module post-processes those files by:
  1. Optionally keeping the original in-field DoseToWater scorer.
  2. Defining a user-chosen set of out-of-field scorers (neutron dose equivalent,
     gamma dose, primary-proton dose, secondary-proton dose from neutrons).
  3. Choosing a scoring volume: structure-averaged (one voxel restricted to an
     RT structure) or a user-defined Cartesian grid box.

The main entry points are:
  - ``scorer_block()``       — build a single TOPAS scorer text block
  - ``append_scorers()``     — post-process a TOPAS file on disk
  - ``scorer_config_from_form()`` — parse user choices from a Flask form
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from importlib.resources import files
from pathlib import Path
from typing import List, Optional, Tuple

from .textio import read_text_lenient


# ---------------------------------------------------------------------------
# Regex patterns for locating sections inside a dicomexport-generated file
# ---------------------------------------------------------------------------

# Matches the "SCORER SET UP" banner that dicomexport writes.
# Tolerates minor whitespace differences between dicomexport versions.
_SCORER_MARKER_RE = re.compile(
    r"#{40,50}\n###\s+S\s*C\s*O\s*R\s*E\s*R\s+S\s*E\s*T\s+U\s*P\s*###\n#{40,50}\n",
    re.MULTILINE,
)

# Matches the "TIME FEATURES" banner that immediately follows the scorer block.
# Time features encode the spot-delivery sequence and must be preserved unchanged.
_TIME_MARKER_RE = re.compile(
    r"#{40,50}\n###\s+T\s+I\s+M\s+E\s+F\s+E\s+A\s+T\s+U\s+R\s+E\s+S\s+###\n#{40,50}\n",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Neutron fluence-to-dose-equivalent table -- loaded from the package data CSV file.
# Edit pregdos/data/neutron_dose_equivalent.csv to change the table; do not touch
# the strings below, they are populated automatically at import time.
# ---------------------------------------------------------------------------

def _load_neutron_table() -> Tuple[str, str]:
    """Read the neutron fluence-to-dose-equivalent table from the package data CSV.

    Returns a pair of TOPAS-formatted strings ready to be embedded directly
    in a ``dv:Sc/.../FluenceToDoseConversion...`` parameter line:

    * ``energies_str`` -- space-separated energy values ending with ``MeV``
    * ``values_str``   -- space-separated coefficient values ending with ``Sv*mm2``

    Lines starting with ``#`` and the CSV header row are ignored, so the
    source file can carry as many explanatory comments as needed.
    """
    csv_path = files("pregdos") / "data" / "neutron_dose_equivalent.csv"
    energies: List[str] = []
    values: List[str] = []
    for line in csv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("energy_"):
            continue  # skip comments and the CSV header row
        parts = line.split(",")
        energies.append(parts[0].strip())
        values.append(parts[1].strip())
    return " ".join(energies) + " MeV", " ".join(values) + " Sv*mm2"


# Cache the loaded strings at module level so the file is read only once.
# _NEUTRON_ENERGIES / _NEUTRON_VALUES are used inside scorer_block() below.
_NEUTRON_ENERGIES, _NEUTRON_VALUES = _load_neutron_table()



# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ScorerType(str, Enum):
    """The out-of-field dose quantities supported by this module."""

    NEUTRON_DOSE_EQUIV = "neutron"
    """Neutron dose equivalent, H = Q(E) * fluence(n).

    *Not* ambient dose equivalent, despite the TOPAS scorer used to compute it: TOPAS's
    AmbientDoseEquivalent scorer carries no coefficients of its own and simply folds fluence
    with the table it is given, so it is the mechanism for any fluence-to-dose-equivalent
    conversion.  See pregdos/data/neutron_dose_equivalent.csv for why the coefficients are
    deliberately not h*(10).  TOPAS writes "AmbientDoseEquivalent" into its CSV header, and the
    files on disk keep it -- they are the record of what ran -- but PregDos corrects the name
    everywhere it is read: in the reports (``reporting.display_quantity``) and in scorer CSVs
    served for download (``reporting.canonicalize_header_bytes``)."""

    GAMMA_DOSE = "gamma"
    """Absorbed dose to medium from photons and their descendants (DoseToMedium)."""

    DOSE_TO_WATER = "dose_to_water"
    """All-particle absorbed dose to water (TOPAS DoseToWater)."""

    PROTON_PRIMARY = "proton_primary"
    """Absorbed dose from protons whose full ancestry contains no neutron.
    These are beam protons that were scattered without producing a neutron
    somewhere in their history."""

    PROTON_SECONDARY = "proton_secondary"
    """Absorbed dose from protons that have a neutron somewhere in their ancestry.
    These are recoil protons knocked out of tissue nuclei by neutrons produced
    in the beam target or patient."""


class VolumeType(str, Enum):
    """How the scoring volume is defined."""

    STRUCTURE = "structure"
    """Score inside a single named RT structure.  TOPAS accumulates dose into
    one voxel that spans the entire structure bounding box (XBins=YBins=ZBins=1),
    giving a mean dose over the structure volume.  The structure must be present
    in the RTSTRUCT file used by dicomexport."""

    USER_GRID = "user_grid"
    """Score on a user-defined Cartesian grid (TsBox) placed at the world origin.
    Useful when the target is not delineated as an RT structure."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class UserDefinedGrid:
    """Physical dimensions and voxel count for a user-defined scoring box.

    The box is centred at (0, 0, 0) in world coordinates.  In the TOPAS patient
    coordinate system this corresponds to the dicom origin; adjust TransX/Y/Z
    in the generated file if you need to move it.
    """

    size_x_mm: float
    """Total extent in X (mm).  TOPAS half-length HLX = size_x_mm / 2."""
    size_y_mm: float
    size_z_mm: float
    nx: int
    """Number of voxels along X.  Voxel size = size_x_mm / nx."""
    ny: int
    nz: int


@dataclass(slots=True)
class ScorerEntry:
    """One scorer to be added to a TOPAS input file."""

    scorer_type: ScorerType
    """Which dose quantity to score."""
    volume_type: VolumeType
    """Structure-averaged or user-defined grid."""
    structure_name: str = ""
    """Name of the RT structure to restrict scoring to (only used when
    volume_type == VolumeType.STRUCTURE).  Must match a ROI name in the RTSTRUCT
    exactly (case-sensitive)."""


@dataclass(slots=True)
class ScorerConfig:
    """Complete scorer configuration for one conversion run."""

    scorers: List[ScorerEntry] = field(default_factory=list)
    """Out-of-field scorers to append."""
    keep_infield: bool = True
    """Whether to keep the dicomexport DoseToWater/RTDoseGrid scorer.
    Set to False for pure out-of-field runs to avoid wasting CPU on in-field
    DICOM output that is not needed."""
    grid: Optional[UserDefinedGrid] = None
    """Grid parameters — required when any entry uses VolumeType.USER_GRID."""


# ---------------------------------------------------------------------------
# UI metadata — maps scorer IDs used in the HTML form to their ScorerType
# ---------------------------------------------------------------------------

SCORER_DEFS = [
    {
        "id": "neutron",
        "scorer_type": ScorerType.NEUTRON_DOSE_EQUIV,
        "label": "Neutron dose equivalent",
        "description": "H = Q(E) * neutron fluence, folded via the TOPAS AmbientDoseEquivalent scorer",
    },
    {
        "id": "gamma",
        "scorer_type": ScorerType.GAMMA_DOSE,
        "label": "Gamma absorbed dose",
        "description": "DoseToMedium, gamma and gamma-ancestor particles",
    },
    {
        "id": "dose_to_water",
        "scorer_type": ScorerType.DOSE_TO_WATER,
        "label": "Absorbed dose to water",
        "description": "DoseToWater, all particles; useful for Eclipse RTDOSE comparison",
    },
    {
        "id": "proton_primary",
        "scorer_type": ScorerType.PROTON_PRIMARY,
        "label": "Proton dose — primary (no neutron ancestors)",
        "description": "DoseToMedium, protons not descending from neutrons",
    },
    {
        "id": "proton_secondary",
        "scorer_type": ScorerType.PROTON_SECONDARY,
        "label": "Proton dose — secondary (neutron ancestors)",
        "description": "DoseToMedium, protons with a neutron ancestor",
    },
]

# TOPAS scorer object name prefix for each type.
# The full name is built dynamically as "{prefix}_{sanitized_structure_name}"
# so multiple structures can each have their own scorer in the same file.
_SCORER_NAME = {
    # TOPAS reports this row's quantity as the bare "AmbientDoseEquivalent", which is wrong
    # twice over: the scorer is neutron-filtered, and the coefficients are not h*(10).  The
    # scorer name is ours, so it says what the number actually is, like its siblings.
    ScorerType.NEUTRON_DOSE_EQUIV: "DoseEquivNeutron",
    ScorerType.GAMMA_DOSE: "DoseGamma",
    ScorerType.DOSE_TO_WATER: "DoseWater",
    ScorerType.PROTON_PRIMARY: "DoseProtonPrimary",
    ScorerType.PROTON_SECONDARY: "DoseProtonSecondary",
}


@dataclass(frozen=True, slots=True)
class _AbsorbedDoseSpec:
    """What distinguishes one absorbed-dose scorer from another.

    An empty ``quantity`` means "whatever the volume mode implies": EnergyDeposit in a
    structure -- PregDos converts that to Gy from the ROI mass, because TOPAS's single-bin dose
    denominator is not the ROI mass -- and DoseToMedium on a grid.  ``{name}`` inside a line is
    substituted with the scorer's name.
    """

    filters: Tuple[str, ...] = ()
    quantity: str = ""
    before_component: Tuple[str, ...] = ()
    always_reference_patient: bool = False


_ABSORBED_DOSE_SPECS = {
    # Captures prompt gammas and the secondary electrons they produce -- Compton electrons
    # carry a "gamma ancestor" -- which is why the filter is on ancestry, not on the particle.
    ScorerType.GAMMA_DOSE: _AbsorbedDoseSpec(
        filters=('sv:Sc/{name}/OnlyIncludeIfParticleOrAncestorNamed  = 1 "gamma"',),
    ),
    # Mainly for validation against Eclipse RTDOSE exports.  In structure mode PregDos corrects
    # the single-bin TOPAS denominator by the structure volume ratio from the mask pre-pass.
    ScorerType.DOSE_TO_WATER: _AbsorbedDoseSpec(
        quantity="DoseToWater",
        before_component=('b:Sc/{name}/PreCalculateStoppingPowerRatios          = "True"',),
        always_reference_patient=True,
    ),
    # "Primary" means no neutron anywhere in the ancestry: beam protons, and protons scattered
    # from them, that arrived without passing through a neutron interaction.
    ScorerType.PROTON_PRIMARY: _AbsorbedDoseSpec(
        filters=(
            'sv:Sc/{name}/OnlyIncludeParticlesNamed              = 1 "proton"',
            'sv:Sc/{name}/OnlyIncludeIfParticleOrAncestorNotNamed = 1 "neutron"',
        ),
    ),
    # Recoil protons knocked out of tissue nuclei by neutrons.  Important out of field: neutrons
    # travel far from the beam and still produce high-LET protons where they stop.
    ScorerType.PROTON_SECONDARY: _AbsorbedDoseSpec(
        filters=(
            'sv:Sc/{name}/OnlyIncludeParticlesNamed              = 1 "proton"',
            'sv:Sc/{name}/OnlyIncludeIfParticleOrAncestorNamed  = 1 "neutron"',
        ),
    ),
}


# Suffix appended to the TOPAS file stem to form the output CSV filename.
# E.g. "topas_field01" + "_neutron" → "topas_field01_neutron.csv"
_OUTPUT_SUFFIX = {
    ScorerType.NEUTRON_DOSE_EQUIV: "_neutron",
    ScorerType.GAMMA_DOSE: "_gamma",
    ScorerType.DOSE_TO_WATER: "_dose_to_water",
    ScorerType.PROTON_PRIMARY: "_proton_primary",
    ScorerType.PROTON_SECONDARY: "_proton_secondary",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sanitize_name(s: str) -> str:
    """Convert an RT structure name to a TOPAS-safe identifier fragment.

    TOPAS parameter names must be alphanumeric (plus underscore).  Structure
    names from DICOM can contain spaces, hyphens, dots, etc.  This function
    replaces every non-alphanumeric character with ``_`` and strips any
    leading/trailing underscores.  Returns ``"unknown"`` for empty input.
    """
    result = re.sub(r"[^A-Za-z0-9]", "_", s).strip("_")
    return result or "unknown"


def _user_grid_geometry(grid: UserDefinedGrid) -> str:
    """Return TOPAS geometry lines that define the ScoringGrid TsBox component.

    IsParallel = True makes this a parallel-world geometry: it overlaps the
    patient CT voxels without physically replacing them, so dose is scored in
    whatever medium occupies each voxel of the patient CT.
    """
    lines = [
        "# User-defined scoring grid (parallel world box centred at origin)",
        's:Ge/ScoringGrid/Parent     = "World"',
        's:Ge/ScoringGrid/Type       = "TsBox"',
        # Parallel geometry overlaps the patient without replacing the CT medium
        'b:Ge/ScoringGrid/IsParallel = "True"',
        # TOPAS expects half-lengths; divide the user-supplied full size by 2
        f"d:Ge/ScoringGrid/HLX       = {grid.size_x_mm / 2:.2f} mm",
        f"d:Ge/ScoringGrid/HLY       = {grid.size_y_mm / 2:.2f} mm",
        f"d:Ge/ScoringGrid/HLZ       = {grid.size_z_mm / 2:.2f} mm",
        f"i:Ge/ScoringGrid/XBins     = {grid.nx}",
        f"i:Ge/ScoringGrid/YBins     = {grid.ny}",
        f"i:Ge/ScoringGrid/ZBins     = {grid.nz}",
        # Centred at world origin — adjust these if you need to shift the grid
        "d:Ge/ScoringGrid/TransX     = 0.0 mm",
        "d:Ge/ScoringGrid/TransY     = 0.0 mm",
        "d:Ge/ScoringGrid/TransZ     = 0.0 mm",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scorer_block(entry: ScorerEntry, output_base: str, grid: Optional[UserDefinedGrid] = None) -> str:
    """Build and return a TOPAS scorer configuration block as a string.

    Parameters
    ----------
    entry:
        Which quantity to score and how to define the scoring volume.
    output_base:
        Stem of the TOPAS file being processed (e.g. ``"topas_field01"``).
        Used to derive the scorer's output filename so each field produces
        a distinct CSV (e.g. ``"topas_field01_neutron"``).
    grid:
        Required when ``entry.volume_type == VolumeType.USER_GRID``.
        Provides the bin counts that determine the output array dimensions.

    Returns
    -------
    str
        Multi-line TOPAS parameter string ready to be appended to a ``.txt`` file.
    """
    is_structure = entry.volume_type == VolumeType.STRUCTURE

    # Build a unique TOPAS scorer name that encodes both the quantity and the
    # target: DoseEquivNeutron_Fetus, DoseGamma_GTV_T1, DoseProtonPrimary_Grid, etc.
    base_name = _SCORER_NAME[entry.scorer_type]
    if is_structure:
        name = f"{base_name}_{_sanitize_name(entry.structure_name)}"
        output_file = output_base + _OUTPUT_SUFFIX[entry.scorer_type] + "_" + _sanitize_name(entry.structure_name)
    else:
        name = f"{base_name}_Grid"
        output_file = output_base + _OUTPUT_SUFFIX[entry.scorer_type]

    # For structure mode the scoring component is the entire "Patient" CT volume;
    # the OnlyIncludeIfInRTStructure filter then restricts hits to that ROI.
    # For grid mode the component is the user-defined TsBox defined separately.
    component = "Patient" if is_structure else "ScoringGrid"

    # Structure mode uses a single voxel (XBins=YBins=ZBins=1).  For absorbed-dose
    # quantities we score EnergyDeposit, not DoseToMedium: TOPAS's structure filter
    # filters energy deposition but its single-bin dose denominator is not the ROI mass.
    # PregDos converts the scored energy to Gy after computing the ROI mass from the
    # TOPAS RTSTRUCT mask pre-pass.
    xbins = 1 if is_structure else (grid.nx if grid else 1)
    ybins = 1 if is_structure else (grid.ny if grid else 1)
    zbins = 1 if is_structure else (grid.nz if grid else 1)
    absorbed_quantity = "EnergyDeposit" if is_structure else "DoseToMedium"

    lines: List[str] = []

    if entry.scorer_type == ScorerType.NEUTRON_DOSE_EQUIV:
        # ── Neutron dose equivalent, H = Q(E) * fluence(n) ────────────────
        # TOPAS's AmbientDoseEquivalent quantity folds the neutron fluence
        # spectrum with a lookup table it does not supply itself, so it is the
        # mechanism here rather than the quantity: the coefficients are Q(E)-
        # weighted, not h*(10).  The result is in Sv (integrated over the
        # scoring volume / simulation).
        lines += [
            f'sv:Sc/{name}/OnlyIncludeParticlesNamed              = 1 "neutron"',
            f's:Sc/{name}/Quantity                                = "AmbientDoseEquivalent"',
            f's:Sc/{name}/Component                               = "{component}"',
        ]
        if is_structure:
            # Restrict to the named RT structure (e.g. the fetus contour)
            lines.append(f'sv:Sc/{name}/OnlyIncludeIfInRTStructure         = 1 "{entry.structure_name}"')
        lines += [
            f"i:Sc/{name}/XBins                                   = {xbins}",
            f"i:Sc/{name}/YBins                                   = {ybins}",
            f"i:Sc/{name}/ZBins                                   = {zbins}",
            f'b:Sc/{name}/OutputToConsole                         = "F"',
            # Increment: if the output CSV already exists, append a new run index
            # rather than overwriting — safe for multi-field runs in the same folder
            f's:Sc/{name}/IfOutputFileAlreadyExists               = "Increment"',
            f's:Sc/{name}/OutputFile                              = "{output_file}"',
            # EBinEnergy = PreStep: use the particle's kinetic energy at the
            # step entry point for the fluence-to-dose lookup (standard choice)
            f's:Sc/{name}/EBinEnergy                              = "PreStep"',
            f's:Sc/{name}/OutputType                              = "csv"',
            f'sv:Sc/{name}/Report                                 = 2 "Sum" "Standard_Deviation"',
            # Tell TOPAS which particle type drives the conversion lookup
            f's:Sc/{name}/GetAmbientDoseEquivalentForParticleNamed = "neutron"',
            # Embed the full fluence-to-dose-equivalent table (114 points)
            f"dv:Sc/{name}/FluenceToDoseConversionEnergies        = 114",
            _NEUTRON_ENERGIES,
            f"dv:Sc/{name}/FluenceToDoseConversionValues          = 114",
            _NEUTRON_VALUES,
        ]

    else:
        # The four absorbed-dose scorers differ only in the quantity they score and the
        # particle filters they apply; component, bins, structure filter and the output block
        # are common.  A spec per type plus one emitter keeps them from drifting apart, and
        # adding a fifth becomes a table entry rather than another copied branch.
        spec = _ABSORBED_DOSE_SPECS[entry.scorer_type]
        lines.append(f's:Sc/{name}/Quantity                                = "{spec.quantity or absorbed_quantity}"')
        lines += [line.format(name=name) for line in spec.before_component]
        lines.append(f's:Sc/{name}/Component                               = "{component}"')
        lines += [line.format(name=name) for line in spec.filters]
        lines += [
            f"i:Sc/{name}/XBins                                   = {xbins}",
            f"i:Sc/{name}/YBins                                   = {ybins}",
            f"i:Sc/{name}/ZBins                                   = {zbins}",
        ]
        if is_structure:
            lines.append(f'sv:Sc/{name}/OnlyIncludeIfInRTStructure         = 1 "{entry.structure_name}"')
        # ReferencedDicomPatient links voxels to CT HU values.  DoseToWater wants it in both
        # modes; the others only on a grid, where the quantity is DoseToMedium.
        if spec.always_reference_patient or not is_structure:
            lines.append(f's:Sc/{name}/ReferencedDicomPatient                  = "Patient"')
        lines += [
            f's:Sc/{name}/IfOutputFileAlreadyExists               = "Increment"',
            f's:Sc/{name}/OutputType                              = "csv"',
            f's:Sc/{name}/OutputFile                              = "{output_file}"',
            f'sv:Sc/{name}/Report                                 = 2 "Sum" "Standard_Deviation"',
        ]

    return "\n".join(lines) + "\n"


def append_scorers(topas_file_path: str, config: ScorerConfig) -> None:
    """Post-process a dicomexport TOPAS input file to add/replace scorers.

    The function:
    1. Reads the existing file.
    2. Locates the ``SCORER SET UP`` block (written by dicomexport).
    3. Optionally strips the DoseToWater scorer if ``config.keep_infield`` is False.
    4. Appends the user-selected out-of-field scorer blocks.
    5. Re-inserts the ``TIME FEATURES`` section unchanged at the end.
    6. Writes the modified content back to the same file path.

    Parameters
    ----------
    topas_file_path:
        Absolute path to the ``topas_fieldNN.txt`` file to modify.
    config:
        Scorer configuration built from the user's form submission.

    Notes
    -----
    The file is only re-written when there is something to change.  If
    ``config.scorers`` is empty **and** ``keep_infield`` is True the function
    returns immediately without touching the file.
    """
    # Nothing to do if no new scorers and the original scorer is kept as-is
    if not config.scorers and config.keep_infield:
        return

    path = Path(topas_file_path)
    content = read_text_lenient(path)

    # The stem of the TOPAS file (e.g. "topas_field01") is used as the base
    # name for all scorer output files so they land in the TOPAS working dir
    # with recognisable, field-specific names.
    output_base = path.stem

    # Locate the start of the scorer section and the start of the time features
    scorer_match = _SCORER_MARKER_RE.search(content)
    tf_match = _TIME_MARKER_RE.search(content)

    if scorer_match:
        # Split the file into three regions:
        #   before       — geometry, setup, beam definition
        #   scorer_header — the "### SCORER SET UP ###" banner itself
        #   old_scorer_body — existing scorer lines (DoseToWater etc.)
        #   time_features — spot delivery parameters that must be kept intact
        before = content[: scorer_match.start()]
        scorer_header = content[scorer_match.start() : scorer_match.end()]

        if tf_match and tf_match.start() > scorer_match.start():
            # Normal case: scorer section is followed by time features
            old_scorer_body = content[scorer_match.end() : tf_match.start()]
            time_features = content[tf_match.start() :]
        else:
            # Edge case: no time features (e.g. file generated without beam model)
            old_scorer_body = content[scorer_match.end() :]
            time_features = ""
    else:
        # No existing scorer section found — append a fresh one at the end
        before = content
        scorer_header = (
            "##############################################\n"
            "###       S C O R E R    S E T U P         ###\n"
            "##############################################\n"
        )
        old_scorer_body = ""
        time_features = ""

    # ── Assemble the new file content ─────────────────────────────────────
    parts: List[str] = [before, scorer_header]

    if config.keep_infield and old_scorer_body.strip():
        # Preserve the original DoseToWater scorer as-is
        parts.append(old_scorer_body)

    # If any scorer uses USER_GRID we must first define the TsBox geometry
    needs_grid = config.grid and any(
        e.volume_type == VolumeType.USER_GRID for e in config.scorers
    )
    if needs_grid:
        parts.append("\n")
        parts.append(_user_grid_geometry(config.grid))  # type: ignore[arg-type]

    # Append each requested out-of-field scorer block
    for entry in config.scorers:
        parts.append("\n")
        parts.append(scorer_block(entry, output_base, config.grid))

    parts.append("\n")

    # Re-attach the time features section that TOPAS needs for spot delivery
    if time_features:
        parts.append(time_features)

    path.write_text("".join(parts))


def scorer_config_from_form(form) -> ScorerConfig:
    """Build a :class:`ScorerConfig` from a Flask ``request.form`` mapping.

    Expects the following form field names (produced by ``setup.html``):

    * ``keep_infield``      — checkbox; present = True
    * ``score_{id}``        — multi-value checkbox (one value per structure name)
                              where ``id`` is a key from SCORER_DEFS

    For example, checking Neutron for structures "Fetus" and "Abdomen" produces
    two values for ``score_neutron``: ``["Fetus", "Abdomen"]``.  Each produces a
    separate :class:`ScorerEntry` with ``VolumeType.STRUCTURE``.

    Parameters
    ----------
    form:
        A dict-like object (``flask.request.form`` or a plain dict in tests).
        Must support ``.get(key)`` and ``.getlist(key)`` — same interface as
        Flask's ImmutableMultiDict.

    Returns
    -------
    ScorerConfig
        Populated configuration ready to pass to :func:`append_scorers`.
    """
    keep_infield = bool(form.get("keep_infield"))

    scorers: List[ScorerEntry] = []
    for scorer_def in SCORER_DEFS:
        sid = scorer_def["id"]
        # getlist returns one value per checked structure for this scorer type
        for structure_name in form.getlist(f"score_{sid}"):
            if structure_name:
                scorers.append(
                    ScorerEntry(
                        scorer_type=scorer_def["scorer_type"],
                        volume_type=VolumeType.STRUCTURE,
                        structure_name=structure_name,
                    )
                )

    return ScorerConfig(scorers=scorers, keep_infield=keep_infield)
