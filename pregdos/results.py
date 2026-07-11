"""Parse TOPAS scorer CSV output and scale it to plan dose.

A finished run directory holds one CSV per scorer, next to the TOPAS input that produced
them.  This module turns those files into rows for the results page.

Reading the CSV
---------------
TOPAS writes a comment header and then the data.  Everything we need is in the header, so
nothing is inferred from the filename::

    # TOPAS Version: 4.2.p3
    # Parameter File: topas_field01.txt
    # Results for scorer: AmBDose_BrainStem
    # Filtered by: OnlyIncludeParticlesNamed = 1 "neutron"
    # Filtered by: OnlyIncludeIfInRTStructure = 1 "BrainStem"
    # Scored in component: Patient
    # AmbientDoseEquivalent ( Sv ) : Sum   Standard_Deviation
    1.049973996636311e-11, 5.038408614663083e-15

The final comment line names the quantity, its unit, and the reported columns.  A scorer
with more than one bin prepends its bin indices to every data row, so the number of leading
index columns is derived per row as ``len(row) - len(reported columns)`` rather than assumed
to be three (structure-average scorers are ``1x1x1`` and have none).

Scaling to plan dose
--------------------
TOPAS reports the dose accumulated over the histories it actually simulated.  To express it
as the dose the *plan* delivers, multiply by the ratio of real particles to simulated
histories.  dicomexport records the plan's particle budget in the TOPAS input's header::

    # TOTAL_NUMBER_OF_PARTICLES: 19063585498
    # REQUESTED_HISTORIES: 20000
    # PARTICLE_SCALING: 953179.27

``PARTICLE_SCALING`` is ``TOTAL_NUMBER_OF_PARTICLES / REQUESTED_HISTORIES`` times a
field-specific factor, so it -- not the raw ratio -- is the value to build on.  But TOPAS does
not simulate ``REQUESTED_HISTORIES``: the per-spot weights are integers, and their sum is what
actually runs (19990 rather than 20000 in the example above; at low statistics whole spots
round away and the gap grows).  Hence::

    scale = PARTICLE_SCALING * REQUESTED_HISTORIES / sum(spotWeight)

Uncertainty
-----------
TOPAS's ``Standard_Deviation`` column is the sample SD of the *per-history* contributions --
not a standard error, and √N too small as an error on the reported ``Sum``.  The statistical
uncertainty we report is the standard error of the estimated dose, ``√N · SD / Sum`` (N =
simulated histories), which is invariant under the deterministic plan scaling.  See
:meth:`ScorerResult.relative_uncertainty`.

.. warning::
   The **absolute** dose values are still under validation (issue #50): how TOPAS normalises a
   structure-filtered scorer is being confirmed against a known reference dose.  The scaling
   and the uncertainty implemented here are separate, independently correct steps applied to
   whatever TOPAS reports at the units it claims.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The final comment line, e.g. `# DoseToMedium ( Gy ) : Sum   Standard_Deviation`
_QUANTITY_RE = re.compile(r"^#\s*(?P<quantity>\w+)\s*\(\s*(?P<unit>[^)]*?)\s*\)\s*:\s*(?P<columns>.+?)\s*$")
_SCORER_RE = re.compile(r"^#\s*Results for scorer:\s*(?P<name>.+?)\s*$")
_PARAMETER_FILE_RE = re.compile(r"^#\s*Parameter File:\s*(?P<name>.+?)\s*$")
_STRUCTURE_RE = re.compile(r'^#\s*Filtered by:\s*OnlyIncludeIfInRTStructure\s*=\s*\d+\s*"(?P<name>.+?)"\s*$')
_COMPONENT_RE = re.compile(r"^#\s*Scored in component:\s*(?P<name>.+?)\s*$")
_VERSION_RE = re.compile(r"^#\s*TOPAS Version:\s*(?P<version>.+?)\s*$")

# Plan scaling, from the header dicomexport writes into the TOPAS input.
_HEADER_VALUE_RE = re.compile(r"^#\s*(?P<key>[A-Z_]+):\s*(?P<value>[-\d.eE+]+)\s*$", re.MULTILINE)
_SPOT_WEIGHT_RE = re.compile(r"uv:Tf/spotWeight/Values\s*=\s*(?P<count>\d+)(?P<values>(?:\s+\d+)+)")

# TOPAS writes `-nan` / `nan` / `inf`; float() accepts them, so a NaN reaches us silently.
_SUM = "Sum"
_STANDARD_DEVIATION = "Standard_Deviation"

# Scorer CSVs produced by a run.  `IfOutputFileAlreadyExists = "Increment"` can append an
# index, so `topas_field01_neutron_Fetus_1.csv` is the same scorer, run again.
_INCREMENT_RE = re.compile(r"^(?P<stem>.+?)_(?P<index>\d+)$")

# dicomexport names its output `<base>_field<NN>.txt`, where NN is the DICOM **BeamNumber**
# -- not the beam's position in the plan.  So the number in the filename maps straight onto
# the RTPLAN, and the two can never drift apart.
_FIELD_NUMBER_RE = re.compile(r"_field(?P<number>\d+)\.txt$")


class ResultsError(Exception):
    """A scorer CSV could not be understood.  Callers flash this, they do not crash on it."""


@dataclass(slots=True)
class PlanScaling:
    """Factor converting scored dose into the dose the whole plan delivers."""

    particle_scaling: float
    """``PARTICLE_SCALING`` from the TOPAS input header."""
    requested_histories: int
    """``REQUESTED_HISTORIES`` -- what was asked for, not what ran."""
    simulated_histories: int
    """``sum(uv:Tf/spotWeight/Values)`` -- what TOPAS actually ran."""
    total_particles: Optional[float] = None
    """``TOTAL_NUMBER_OF_PARTICLES``, kept for display."""

    @property
    def factor(self) -> float:
        """Multiply a scored value by this to get the plan value."""
        if self.simulated_histories <= 0:
            return self.particle_scaling
        return self.particle_scaling * self.requested_histories / self.simulated_histories

    @property
    def rounding_correction(self) -> float:
        """How much the integer spot weights shifted the scaling, as a ratio.

        1.0 means the simulation ran exactly the requested histories.  Values far from 1.0
        mean the statistics were low enough that whole spots rounded away.
        """
        if self.simulated_histories <= 0:
            return 1.0
        return self.requested_histories / self.simulated_histories


@dataclass(slots=True)
class ScorerResult:
    """One scorer CSV, parsed and scaled."""

    csv_name: str
    scorer: str
    quantity: str
    unit: str
    columns: List[str]
    rows: List[Tuple[Tuple[int, ...], Dict[str, float]]] = field(default_factory=list)
    """``(bin_index, {column: value})``.  Structure scorers have exactly one row and an
    empty bin index."""
    structure: str = ""
    """RT structure the scorer was restricted to, or "" for an unfiltered scorer."""
    component: str = ""
    """TOPAS component the scorer was attached to, e.g. ``Patient``."""
    parameter_file: str = ""
    topas_version: str = ""
    run_index: Optional[int] = None
    """Set when `IfOutputFileAlreadyExists = Increment` produced `..._1.csv` etc."""

    @property
    def field_number(self) -> Optional[int]:
        """DICOM BeamNumber of the field that produced this scorer, from the TOPAS input name."""
        if (m := _FIELD_NUMBER_RE.search(self.parameter_file)):
            return int(m.group("number"))
        return None

    @property
    def is_single_bin(self) -> bool:
        return len(self.rows) == 1 and not self.rows[0][0]

    def _value(self, column: str) -> Optional[float]:
        if not self.is_single_bin:
            return None
        return self.rows[0][1].get(column)

    @property
    def raw_sum(self) -> Optional[float]:
        return self._value(_SUM)

    @property
    def raw_standard_deviation(self) -> Optional[float]:
        return self._value(_STANDARD_DEVIATION)

    @property
    def problem(self) -> Optional[str]:
        """Why this result must not be shown as a dose, or None when it is usable.

        A NaN ``Sum`` is the signature of the OpenTOPAS < 4.2.3 multithreaded scorer merge
        (issue #49): the value is not a small dose, it is not a dose at all.
        """
        if not self.is_single_bin:
            return None  # multi-bin grids are not summarised as a single dose
        total = self.raw_sum
        if total is None:
            return f"scorer reported no {_SUM} column"
        if math.isnan(total):
            return ("scorer Sum is NaN — this run used OpenTOPAS < 4.2.3, whose multithreaded "
                    "scorer merge corrupts Sum and under-estimates the uncertainty (see #49)")
        if math.isinf(total):
            return "scorer Sum is infinite"
        return None

    @property
    def usable(self) -> bool:
        return self.problem is None

    def relative_uncertainty(self, scaling: Optional[PlanScaling]) -> Optional[float]:
        """Fractional 1σ Monte-Carlo statistical uncertainty on the dose, or None.

        TOPAS reports ``Sum`` (the total over ``N`` simulated histories) and
        ``Standard_Deviation`` = ``s``, the sample SD of the *per-history* contributions --
        **not** a standard error, and not the uncertainty on ``Sum``.  The estimate we report
        is the mean dose per proton (``Sum/N``) multiplied by the plan's proton count, so the
        relevant error is the standard error of that mean:

            SE(dose)/dose = √N · s / Sum   =   (s / mean) / √N

        ``N`` is the number of histories actually simulated (``Σ spotWeight``), which we
        already parse for the plan scaling.  This ratio is dimensionless and **invariant under
        the plan scaling** -- multiplying ``Sum`` by a constant to reach plan dose multiplies
        the absolute error by the same constant -- so it applies unchanged to the scaled dose.
        It depends on how many histories were *simulated*, never on the plan's proton count.
        """
        if scaling is None:
            return None
        sd = self.raw_standard_deviation
        total = self.raw_sum
        n = scaling.simulated_histories
        if sd is None or math.isnan(sd) or total in (None, 0.0) or math.isnan(total) or n <= 0:
            return None
        return math.sqrt(n) * sd / abs(total)

    def scaled(self, scaling: Optional[PlanScaling]) -> Tuple[Optional[float], Optional[float]]:
        """``(dose, uncertainty)`` scaled to plan dose, or ``(None, None)`` if unusable.

        ``uncertainty`` is the **absolute 1σ statistical uncertainty** on the reported dose
        (``dose × relative_uncertainty``), not the raw ``Standard_Deviation`` column -- see
        :meth:`relative_uncertainty`.  It is None when the histories needed to form a proper
        error are unavailable (no plan scaling).
        """
        if not self.usable:
            return None, None
        factor = scaling.factor if scaling else 1.0
        total = self.raw_sum
        dose = None if total is None else total * factor
        rel = self.relative_uncertainty(scaling)
        uncertainty = None if (dose is None or rel is None) else abs(dose) * rel
        return dose, uncertainty


# ---------------------------------------------------------------------------
# Plan scaling
# ---------------------------------------------------------------------------

def parse_plan_scaling(topas_input: str | Path) -> Optional[PlanScaling]:
    """Read the plan particle budget from a generated TOPAS input file.

    Returns None when the header is absent -- an older dicomexport, or a hand-written file --
    so callers can fall back to reporting unscaled values rather than failing.
    """
    try:
        text = Path(topas_input).read_text()
    except OSError:
        return None

    header = {m.group("key"): m.group("value") for m in _HEADER_VALUE_RE.finditer(text)}
    if "PARTICLE_SCALING" not in header or "REQUESTED_HISTORIES" not in header:
        return None

    try:
        particle_scaling = float(header["PARTICLE_SCALING"])
        requested = int(float(header["REQUESTED_HISTORIES"]))
        total = float(header["TOTAL_NUMBER_OF_PARTICLES"]) if "TOTAL_NUMBER_OF_PARTICLES" in header else None
    except ValueError:
        return None

    # TOPAS runs sum(spotWeight) histories, not `requested`: the weights are integers.
    simulated = 0
    match = _SPOT_WEIGHT_RE.search(text)
    if match:
        simulated = sum(int(v) for v in match.group("values").split())

    return PlanScaling(
        particle_scaling=particle_scaling,
        requested_histories=requested,
        simulated_histories=simulated,
        total_particles=total,
    )


# ---------------------------------------------------------------------------
# Scorer CSV
# ---------------------------------------------------------------------------

def parse_scorer_csv(path: str | Path) -> ScorerResult:
    """Parse one TOPAS scorer CSV.

    Raises :class:`ResultsError` on anything unrecognisable; callers turn that into a flashed
    warning rather than a 500.
    """
    path = Path(path)
    try:
        lines = path.read_text().splitlines()
    except OSError as e:
        raise ResultsError(f"{path.name}: cannot read ({e})") from e

    scorer = quantity = unit = structure = component = parameter_file = topas_version = ""
    columns: List[str] = []
    data: List[str] = []

    for line in lines:
        if not line.startswith("#"):
            if line.strip():
                data.append(line)
            continue
        if (m := _SCORER_RE.match(line)):
            scorer = m.group("name")
        elif (m := _STRUCTURE_RE.match(line)):
            structure = m.group("name")
        elif (m := _COMPONENT_RE.match(line)):
            component = m.group("name")
        elif (m := _PARAMETER_FILE_RE.match(line)):
            parameter_file = m.group("name")
        elif (m := _VERSION_RE.match(line)):
            topas_version = m.group("version")
        elif (m := _QUANTITY_RE.match(line)):
            # The quantity line is the last comment before the data.  Other `# Filtered by:`
            # lines never match, because they have no `( unit )`.
            quantity = m.group("quantity")
            unit = m.group("unit")
            columns = m.group("columns").split()

    if not columns:
        raise ResultsError(f"{path.name}: no quantity/column header found")
    if not data:
        raise ResultsError(f"{path.name}: no data rows (job may still be running)")

    rows: List[Tuple[Tuple[int, ...], Dict[str, float]]] = []
    for line in data:
        cells = [c.strip() for c in line.split(",")]
        # A multi-bin scorer prepends its bin indices; a 1x1x1 scorer prepends nothing.
        n_index = len(cells) - len(columns)
        if n_index < 0:
            raise ResultsError(f"{path.name}: row has {len(cells)} values, expected at least {len(columns)}")
        try:
            index = tuple(int(c) for c in cells[:n_index])
            values = {name: float(cells[n_index + i]) for i, name in enumerate(columns)}
        except ValueError as e:
            raise ResultsError(f"{path.name}: unparseable row {line!r}") from e
        rows.append((index, values))

    # `IfOutputFileAlreadyExists = Increment` only appends `_1` when the un-suffixed file is
    # already there, so require that sibling before reading a trailing number as a run index.
    # Otherwise a structure legitimately named e.g. "PTV_2" would be mistaken for a re-run.
    run_index = None
    if (m := _INCREMENT_RE.match(path.stem)) and (path.with_name(m.group("stem") + path.suffix)).exists():
        run_index = int(m.group("index"))

    return ScorerResult(
        csv_name=path.name,
        scorer=scorer or path.stem,
        quantity=quantity,
        unit=unit,
        columns=columns,
        rows=rows,
        structure=structure,
        component=component,
        parameter_file=parameter_file,
        topas_version=topas_version,
        run_index=run_index,
    )


def beam_names(rtplan_path: Optional[str | Path]) -> Dict[int, str]:
    """Map DICOM ``BeamNumber`` to ``BeamName`` from an RTPLAN.

    Clinicians identify a field by its name ("RPO", "Field 2"), not by the order it happens
    to appear in the plan -- and the two need not agree.  dicomexport encodes the BeamNumber
    in the TOPAS filename, so the lookup is exact.

    Proton plans are *RT Ion Plan* and use ``IonBeamSequence``; photon plans use
    ``BeamSequence``.  Returns an empty mapping when the plan is missing or unreadable, so a
    results page still renders with bare field numbers.
    """
    if rtplan_path is None:
        return {}
    try:
        import pydicom

        ds = pydicom.dcmread(str(rtplan_path), stop_before_pixels=True)
    except Exception:  # noqa: BLE001 - a bad RTPLAN must not break the results page
        return {}

    names: Dict[int, str] = {}
    for attr in ("IonBeamSequence", "BeamSequence"):
        for beam in getattr(ds, attr, []) or []:
            number = getattr(beam, "BeamNumber", None)
            name = getattr(beam, "BeamName", None) or getattr(beam, "BeamDescription", None)
            if number is not None and name:
                names[int(number)] = str(name).strip()
    return names


def collect_results(run_dir: str | Path) -> Tuple[List[ScorerResult], List[str]]:
    """Parse every scorer CSV in a run directory.

    Returns the results (sorted for a stable table) and a list of warnings for files that
    could not be parsed.  Never raises: a broken CSV must not take the results page down.
    """
    run_dir = Path(run_dir)
    results: List[ScorerResult] = []
    warnings: List[str] = []

    for csv_path in sorted(run_dir.glob("*.csv")):
        try:
            results.append(parse_scorer_csv(csv_path))
        except ResultsError as e:
            warnings.append(str(e))

    # Group by field first: a clinician reads the table one field at a time.
    results.sort(key=lambda r: (r.field_number or 0, r.structure, r.quantity, r.scorer, r.run_index or 0))
    return results, warnings


def scaling_for(result: ScorerResult, run_dir: str | Path) -> Optional[PlanScaling]:
    """Plan scaling for one result, from the TOPAS input its header names.

    The CSV records which parameter file produced it, so a run directory holding several
    fields scales each scorer by its own field's particle budget.
    """
    if not result.parameter_file:
        return None
    return parse_plan_scaling(Path(run_dir) / Path(result.parameter_file).name)
