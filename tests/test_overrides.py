import pytest
from werkzeug.datastructures import MultiDict

from pregdos import overrides


MATERIALS = """\
# Material definitions for structure overrides.
# sv:Ma/Commented/Components = 1 "Iron"
sv:Ma/ImplantSteel/Components = 8 "Carbon" "Silicon" "Phosphorus" "Sulfur" "Chromium" "Manganese" "Iron" "Nickel"
uv:Ma/ImplantSteel/Fractions  = 8 0.0015 0.01 0.00045 0.0003 0.19 0.02 0.67775 0.1
d:Ma/ImplantSteel/Density     = 7.8 g/cm3
d:Ma/Titanium64/Density       = 4.43 g/cm3
"""


def test_site_materials_are_the_ma_names_in_file_order(tmp_path):
    path = tmp_path / "materials.txt"
    path.write_text(MATERIALS)
    assert overrides.site_materials(path) == ["ImplantSteel", "Titanium64"]


def test_no_materials_file_means_no_site_materials(tmp_path):
    assert overrides.site_materials(None) == []
    assert overrides.site_materials(tmp_path / "absent.txt") == []


@pytest.mark.parametrize("entry, expected", [
    ({"path": "/cfg/materials.txt", "exists": True}, "/cfg/materials.txt"),
    ({"path": "/cfg/materials.txt", "exists": False}, None),
    (None, None),
])
def test_materials_file_comes_from_the_listing(entry, expected):
    assert overrides.materials_file_of({"materials": entry} if entry else {}) == expected


def _form(*rows):
    return MultiDict([("override_roi", r) for r, _ in rows] + [("override_material", m) for _, m in rows])


STRUCTURES = ["BODY", "Hip implant", "CTV"]
OFFERED = ["ImplantSteel", *overrides.BUILTIN_MATERIALS]


def test_rows_keep_their_order_and_blank_rows_are_skipped():
    """Order is what TOPAS applies: the first row wins where structures overlap."""
    form = _form(("Hip implant", "ImplantSteel"), ("", ""), ("BODY", "G4_WATER"))
    assert overrides.from_form(form, STRUCTURES, OFFERED) == [
        ("Hip implant", "ImplantSteel"), ("BODY", "G4_WATER")]


@pytest.mark.parametrize("rows, message", [
    ((("Hip implant", ""),), "both a structure and a material"),
    ((("Femur", "Steel"),), "'Femur' is not a structure of this study"),
    ((("Hip implant", "Unobtainium"),), "'Unobtainium' is not one of the offered materials"),
    ((("Hip implant", "Steel"), ("Hip implant", "Vacuum")), "'Hip implant' is given more than once"),
])
def test_an_override_the_page_did_not_offer_is_refused(rows, message):
    with pytest.raises(ValueError, match=message):
        overrides.from_form(_form(*rows), STRUCTURES, OFFERED)
