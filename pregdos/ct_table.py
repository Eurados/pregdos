"""Check a TOPAS Schneider CT-number-to-material table, and compute densities from it (#131).

The table decides every density and every material in a run.  TOPAS reads its
``DensityCorrection`` vector without a bounds check, so a table can make it read outside the
vector, and the run then carries on with undefined densities.  PregDos therefore checks a table
when it is chosen or uploaded, before anything is converted.

The rules, from OpenTOPAS 4.3.0's ``geometry/TsImagingToMaterialSchneider.cc``:

* TOPAS aborts at startup unless ``len(DensityCorrection) == sections[-1] - sections[0]``
  (``sections`` is ``SchneiderHounsfieldUnitSections``).
* A voxel value is clamped to ``[sections[0], sections[-1] - 1]`` and the correction read as
  ``DensityCorrection[value - MinImagingValue]``, ``MinImagingValue`` defaulting to -1000.
* TOPAS aborts unless ``SchneiderHounsfieldUnitSections`` and ``SchneiderHUToMaterialSections``
  never decrease, and unless the two start and end at the same values.  Equal neighbours pass.

Together they leave exactly one valid setting: ``MinImagingValue == sections[0]``.  A table
breaking it is refused, not clamped: a clamped index would replace an undefined density with a
plausible one and hide the fault.

What cannot be checked: whether the table expects the quantity the images hold -- Hounsfield
units or stopping-power ratio (#106).  ``RescaleType`` can read ``HU`` even on a derived
stopping-power map.  Choosing the right table stays the user's responsibility.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List

import numpy as np

#: TOPAS's ``Ge/Patient/MinImagingValue`` when a table does not set it.
TOPAS_DEFAULT_MIN_IMAGING_VALUE = -1000

_P = "Ge/Patient/"
CONVERTER = _P + "ImagingToMaterialConverter"
SECTIONS = _P + "SchneiderHounsfieldUnitSections"
OFFSET = _P + "SchneiderDensityOffset"
FACTOR = _P + "SchneiderDensityFactor"
FACTOR_OFFSET = _P + "SchneiderDensityFactorOffset"
CORRECTION = _P + "DensityCorrection"
MIN_IMAGING_VALUE = _P + "MinImagingValue"
MATERIAL_SECTIONS = _P + "SchneiderHUToMaterialSections"

# "<type>:<name> =".  The value runs until the next parameter, so a vector may continue over
# several lines, as DensityCorrection does in OpenTOPAS's own HUtoMaterialSchneider.txt.
_PARAMETER_RE = re.compile(r"^\s*([a-z]+):(\S+?)\s*=", re.MULTILINE)


class TableError(ValueError):
    """The table would make TOPAS abort or read out of bounds.  ``problems`` says each way."""

    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class SchneiderTable:
    sections: np.ndarray
    offsets: np.ndarray
    factors: np.ndarray
    factor_offsets: np.ndarray
    corrections: np.ndarray
    min_imaging_value: int

    def density(self, ct_numbers: np.ndarray) -> np.ndarray:
        """Mass density in g/cm³ per voxel, as TOPAS computes it."""
        hu = np.clip(ct_numbers, self.sections[0], self.sections[-1] - 1)
        section = np.clip(np.searchsorted(self.sections[1:], hu, side="right"), 0, len(self.offsets) - 1)
        density = self.offsets[section] + self.factors[section] * (self.factor_offsets[section] + hu)
        # Valid by construction: parse() refuses any table where this index leaves the vector.
        index = np.rint(hu - self.min_imaging_value).astype(int)
        return density * self.corrections[index]


def _parameters(text: str) -> tuple[Dict[str, List[str]], List[str]]:
    """Each parameter's whitespace-separated value tokens, and any defined twice."""
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    matches = list(_PARAMETER_RE.finditer(text))
    params: Dict[str, List[str]] = {}
    twice: List[str] = []
    for match, following in zip(matches, matches[1:] + [None]):
        name = match.group(2)
        if name in params:
            twice.append(name)
        params[name] = text[match.end():following.start() if following else len(text)].split()
    return params, twice


def _vector(params: Dict[str, List[str]], name: str, problems: List[str],
            integers: bool = False) -> np.ndarray | None:
    """A TOPAS vector -- a count, that many numbers, at most one unit -- or None, with why."""
    short = name.removeprefix(_P)
    tokens = params.get(name)
    if not tokens:
        problems.append(f"{short} is missing")
        return None
    try:
        count = int(tokens[0])
        values = [float(v) for v in tokens[1:1 + count]]
    except ValueError:
        problems.append(f"{short} is not a count followed by numbers")
        return None
    rest = tokens[1 + count:]
    if len(values) != count:
        problems.append(f"{short} declares {count} values but has {len(values)}")
        return None
    if len(rest) > 1:
        problems.append(f"{short} declares {count} values but has more: {' '.join(rest[:3])} ...")
        return None
    # float() reads "nan" and "inf", which TOPAS does not, and which later crashed int().
    if not all(math.isfinite(v) for v in values):
        problems.append(f"{short} holds a value that is not a finite number")
        return None
    if integers and not all(v.is_integer() for v in values):
        problems.append(f"{short} must hold whole numbers, as TOPAS reads it (an iv: vector)")
        return None
    return np.asarray(values, dtype=float)


def _first_decrease(values: np.ndarray) -> int | None:
    """Index of the first value smaller than the one before it, or None."""
    down = np.flatnonzero(np.diff(values) < 0)
    return int(down[0]) + 1 if down.size else None


def parse(text: str) -> SchneiderTable:
    """The table, checked.  Raises :class:`TableError` naming every problem and its fix."""
    params, twice = _parameters(text)
    problems = [f"{name.removeprefix(_P)} is defined twice; TOPAS aborts" for name in twice]
    converter = params.get(CONVERTER, [])
    if converter != ['"Schneider"']:
        raise TableError(problems + [
            f"ImagingToMaterialConverter is {' '.join(converter) or 'missing'}, not \"Schneider\""])

    sections = _vector(params, SECTIONS, problems, integers=True)
    offsets = _vector(params, OFFSET, problems)
    factors = _vector(params, FACTOR, problems)
    factor_offsets = _vector(params, FACTOR_OFFSET, problems)
    corrections = _vector(params, CORRECTION, problems)
    try:
        min_value = (int(params[MIN_IMAGING_VALUE][0]) if MIN_IMAGING_VALUE in params
                     else TOPAS_DEFAULT_MIN_IMAGING_VALUE)
    except (ValueError, IndexError):
        problems.append("MinImagingValue is not an integer")
        min_value = TOPAS_DEFAULT_MIN_IMAGING_VALUE
    if sections is None or offsets is None or factors is None or factor_offsets is None or corrections is None:
        raise TableError(problems)

    if len(sections) < 2:
        problems.append("SchneiderHounsfieldUnitSections needs at least two boundaries")
        raise TableError(problems)
    if not len(offsets) == len(factors) == len(factor_offsets) == len(sections) - 1:
        problems.append(
            f"{len(sections)} SchneiderHounsfieldUnitSections need {len(sections) - 1} values each in "
            f"SchneiderDensityOffset, SchneiderDensityFactor and SchneiderDensityFactorOffset, which "
            f"have {len(offsets)}, {len(factors)} and {len(factor_offsets)}")
    if (down := _first_decrease(sections)) is not None:
        problems.append(
            f"SchneiderHounsfieldUnitSections decrease at {int(sections[down - 1])} -> "
            f"{int(sections[down])}; they must not decrease, or TOPAS aborts")
    if MATERIAL_SECTIONS in params:
        material_sections = _vector(params, MATERIAL_SECTIONS, problems, integers=True)
        if material_sections is not None and len(material_sections):
            if (down := _first_decrease(material_sections)) is not None:
                problems.append(
                    f"SchneiderHUToMaterialSections decrease at {int(material_sections[down - 1])} -> "
                    f"{int(material_sections[down])}; they must not decrease, or TOPAS aborts")
            for end, here, there in (("first", material_sections[0], sections[0]),
                                     ("last", material_sections[-1], sections[-1])):
                if int(here) != int(there):
                    problems.append(
                        f"SchneiderHUToMaterialSections' {end} value is {int(here)}, but "
                        f"SchneiderHounsfieldUnitSections' is {int(there)}; TOPAS aborts unless they match")
    first, last = int(sections[0]), int(sections[-1])
    if len(corrections) != last - first:
        problems.append(
            f"DensityCorrection has {len(corrections)} values but the sections span {first} to {last}, "
            f"which needs {last - first}; TOPAS aborts")
    if min_value != first:
        default = " (the TOPAS default)" if MIN_IMAGING_VALUE not in params else ""
        problems.append(
            f"sections start at {first} but MinImagingValue is {min_value}{default}, so TOPAS would read "
            f"DensityCorrection out of bounds; set i:Ge/Patient/MinImagingValue = {first}")
    if problems:
        raise TableError(problems)
    return SchneiderTable(sections, offsets, factors, factor_offsets, corrections, min_value)


def problems(text: str) -> List[str]:
    """Every reason this table must not be used, or [] if it may."""
    try:
        parse(text)
    except TableError as e:
        return e.problems
    return []


def problems_in_bytes(data: bytes) -> List[str]:
    """:func:`problems` for a file's bytes.  It must be UTF-8: dicomexport pastes it with ``-S``,
    which refuses anything else."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"it is not UTF-8 text (byte {e.start}); save it as UTF-8"]
    return problems(text)
