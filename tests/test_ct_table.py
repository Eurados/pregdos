from pathlib import Path

import dicomexport
import numpy as np
import pytest

from pregdos import ct_table

SHIPPED = Path(dicomexport.__file__).parent / "defaults/ct_to_material/HUtoMaterialSchneider.txt"


def _table(first=-1000, last=-997, corrections=None, min_value=None, converter='"Schneider"', extra=""):
    """A small Schneider table; by default valid, with sections -1000, -998, last."""
    n = (last - first) if corrections is None else corrections
    lines = [
        f"s:Ge/Patient/ImagingToMaterialConverter = {converter}",
        f"iv:Ge/Patient/SchneiderHounsfieldUnitSections = 3 {first} {first + 2} {last}",
        "uv:Ge/Patient/SchneiderDensityOffset = 2 1.0 2.0",
        "uv:Ge/Patient/SchneiderDensityFactor = 2 0.0 0.0",
        "uv:Ge/Patient/SchneiderDensityFactorOffset = 2 0.0 0.0",
        # Over two lines, like OpenTOPAS's own table.
        f"uv:Ge/Patient/DensityCorrection = {n} " + " ".join(["1.0"] * (n - n // 2)) + "\n    "
        + " ".join(["1.0"] * (n // 2)),
    ]
    if min_value is not None:
        lines.append(f"i:Ge/Patient/MinImagingValue = {min_value}")
    return "\n".join(lines) + "\n" + extra


def test_the_shipped_table_passes():
    """OpenTOPAS's example: sections from -1000, TOPAS's default MinImagingValue, and
    2996 - (-1000) = 3996 corrections over many lines, ending in a unit."""
    assert ct_table.problems(SHIPPED.read_text()) == []


def test_densities_from_the_shipped_table_are_unchanged():
    """Representative values, as documented for this table before #131 replaced the parser."""
    table = ct_table.parse(SHIPPED.read_text())
    densities = table.density(np.array([-1000.0, -999.0, -950.0, 0.0, 1200.0]))
    assert densities == pytest.approx([0.01132, 0.01244, 0.05946, 0.98822, 1.79392], abs=5e-6)


def test_a_table_starting_below_the_default_needs_its_min_imaging_value():
    """dicomexport#99: sections from -1024 without MinImagingValue made TOPAS read
    DensityCorrection[-24].  The message names the numbers and the fix."""
    (problem,) = ct_table.problems(_table(first=-1024, last=3072))
    assert problem == ("sections start at -1024 but MinImagingValue is -1000 (the TOPAS default), so "
                       "TOPAS would read DensityCorrection out of bounds; set i:Ge/Patient/MinImagingValue = -1024")


def test_the_same_table_with_its_min_imaging_value_passes():
    text = _table(first=-1024, last=3072, min_value=-1024)
    assert ct_table.problems(text) == []
    table = ct_table.parse(text)
    assert table.density(np.array([-2000.0, -1024.0, 5000.0])) == pytest.approx([1.0, 1.0, 2.0])


def test_a_correction_vector_of_the_wrong_length_is_refused():
    """TOPAS aborts unless len(DensityCorrection) == sections[-1] - sections[0]; longer is
    not fine either."""
    (problem,) = ct_table.problems(_table(corrections=4))
    assert "DensityCorrection has 4 values but the sections span -1000 to -997, which needs 3" in problem


@pytest.mark.parametrize("text, message", [
    (_table(converter='"DefaultConverter"'), 'ImagingToMaterialConverter is "DefaultConverter", not "Schneider"'),
    (_table().replace("SchneiderDensityOffset = 2 1.0 2.0", "SchneiderDensityOffset = 2 1.0"),
     "SchneiderDensityOffset declares 2 values but has 1"),
    (_table().replace("SchneiderDensityOffset = 2 1.0 2.0", "SchneiderDensityOffset = 2 1.0 2.0 3.0 4.0"),
     "SchneiderDensityOffset declares 2 values but has more"),
    (_table(extra="i:Ge/Patient/MinImagingValue = -1000\ni:Ge/Patient/MinImagingValue = -1000\n"),
     "MinImagingValue is defined twice"),
    (_table().replace("uv:Ge/Patient/SchneiderDensityFactor = 2 0.0 0.0\n", ""), "SchneiderDensityFactor is missing"),
])
def test_a_malformed_table_is_refused_with_why(text, message):
    assert any(message in p for p in ct_table.problems(text))


def test_a_table_that_is_not_utf8_is_refused():
    """dicomexport pastes the table with -S, which refuses anything but UTF-8."""
    (problem,) = ct_table.problems_in_bytes(_table().encode("utf-8") + b"# \xfc\n")
    assert "not UTF-8" in problem


# --- Section order, as OpenTOPAS 4.3.0 checks it (Codex review of #132) ------------------------

def _swap_two_interior_boundaries(text: str) -> str:
    import re
    m = re.search(r"(iv:Ge/Patient/SchneiderHounsfieldUnitSections\s*=\s*\d+\s+)(\S+)\s+(\S+)\s+(\S+)", text)
    assert m
    return text.replace(m.group(0), f"{m.group(1)}{m.group(2)} {m.group(4)} {m.group(3)}", 1)


def test_hounsfield_sections_out_of_order_are_refused():
    """The reproduction: two interior boundaries of the shipped table swapped.  TOPAS aborts
    ("not monotonically increasing"); PregDos passed it."""
    (problem,) = ct_table.problems(_swap_two_interior_boundaries(SHIPPED.read_text()))
    assert problem == ("SchneiderHounsfieldUnitSections decrease at 15 -> -98; they must not "
                       "decrease, or TOPAS aborts")


def test_equal_neighbouring_sections_pass_as_in_topas():
    """TOPAS only refuses a decrease."""
    text = _table().replace("= 3 -1000 -998 -997", "= 3 -1000 -1000 -997")
    assert ct_table.problems(text) == []


def _with_material_sections(values):
    return _table(extra=f"iv:Ge/Patient/SchneiderHUToMaterialSections = {len(values)} "
                        + " ".join(map(str, values)) + "\n")


def test_material_sections_matching_the_density_sections_pass():
    assert ct_table.problems(_with_material_sections([-1000, -999, -997])) == []


def test_material_sections_out_of_order_are_refused():
    (problem,) = ct_table.problems(_with_material_sections([-1000, -998, -999, -997]))
    assert "SchneiderHUToMaterialSections decrease at -998 -> -999" in problem


@pytest.mark.parametrize("values, end", [([-999, -998, -997], "first"), ([-1000, -998, -990], "last")])
def test_material_sections_must_share_the_ends(values, end):
    (problem,) = ct_table.problems(_with_material_sections(values))
    assert problem.startswith(f"SchneiderHUToMaterialSections' {end} value is")
    assert "TOPAS aborts unless they match" in problem


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_a_non_finite_boundary_is_refused_not_a_crash(value):
    """int(nan) raised an uncaught ValueError at upload."""
    text = _table().replace("= 3 -1000 -998 -997", f"= 3 -1000 {value} -997")
    assert ct_table.problems(text) == ["SchneiderHounsfieldUnitSections holds a value that is not a finite number"]


def test_a_fractional_boundary_is_refused():
    text = _table().replace("= 3 -1000 -998 -997", "= 3 -1000 -998.5 -997")
    assert ct_table.problems(text) == [
        "SchneiderHounsfieldUnitSections must hold whole numbers, as TOPAS reads it (an iv: vector)"]
