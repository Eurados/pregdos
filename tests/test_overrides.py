import pydicom
import pytest
from werkzeug.datastructures import MultiDict

from pregdos import overrides
from tests import dicom_factory


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


def _form(*rows, other=None):
    """The form's lists.  ``other`` is the "Other Geant4 material" text field of each row."""
    fields = [("override_roi", r) for r, _ in rows] + [("override_material", m) for _, m in rows]
    if other is not None:
        fields += [("override_material_other", o) for o in other]
    return MultiDict(fields)


STRUCTURES = ["BODY", "Hip implant", "CTV"]
OFFERED = ["ImplantSteel", *overrides.BUILTIN_MATERIALS]


def test_rows_keep_their_order_and_blank_rows_are_skipped():
    """Order is what TOPAS applies: the first row wins where structures overlap."""
    form = _form(("Hip implant", "ImplantSteel"), ("", ""), ("BODY", "G4_WATER"))
    assert overrides.from_form(form, STRUCTURES, OFFERED) == [
        ("Hip implant", "ImplantSteel"), ("BODY", "G4_WATER")]


@pytest.mark.parametrize("rows, message", [
    ((("", "Steel"),), "Steel has no structure"),
    ((("Femur", "Steel"),), "'Femur' is not a structure of this study"),
    ((("Hip implant", "Unobtainium"),), "'Unobtainium' is not one of the offered materials"),
    ((("Hip implant", "Steel"), ("Hip implant", "Vacuum")), "'Hip implant' is given more than once"),
])
def test_an_override_the_page_did_not_offer_is_refused(rows, message):
    with pytest.raises(ValueError, match=message):
        overrides.from_form(_form(*rows), STRUCTURES, OFFERED)


# --- "No override", and "Other Geant4 material" (#126) ------------------------------------------

def test_a_structure_left_at_no_override_is_skipped():
    """Undoing a choice: a prefilled Eclipse row set back to "No override" is not an error."""
    form = _form(("Hip implant", ""), ("BODY", "G4_WATER"))
    assert overrides.from_form(form, STRUCTURES, OFFERED) == [("BODY", "G4_WATER")]


def test_other_takes_the_typed_geant4_material():
    offered = [*OFFERED, *overrides.GEANT4_MATERIALS]
    form = _form(("Hip implant", overrides.OTHER), ("BODY", "G4_WATER"),
                 other=["G4_BONE_COMPACT_ICRU", ""])
    assert overrides.from_form(form, STRUCTURES, offered) == [
        ("Hip implant", "G4_BONE_COMPACT_ICRU"), ("BODY", "G4_WATER")]


def test_a_mistyped_geant4_material_is_refused_with_a_suggestion():
    offered = [*OFFERED, *overrides.GEANT4_MATERIALS]
    form = _form(("Hip implant", overrides.OTHER), other=["G4_BONE_COMPACT"])
    with pytest.raises(ValueError, match="did you mean G4_BONE_COMPACT_ICRU"):
        overrides.from_form(form, STRUCTURES, offered)


def test_other_with_nothing_typed_is_no_override():
    form = _form(("Hip implant", overrides.OTHER), other=["  "])
    assert overrides.from_form(form, STRUCTURES, OFFERED) == []


def test_geant4_materials_are_geant4s_nist_names():
    names = overrides.GEANT4_MATERIALS
    assert len(names) == len(set(names)) == 309
    assert all(name.startswith("G4_") for name in names)
    assert {"G4_WATER", "G4_AIR", "G4_Ti", "G4_BONE_COMPACT_ICRU", "G4_STAINLESS-STEEL"} <= set(names)


# --- What Eclipse declares, and the rows it prefills (#126) -------------------------------------
#
# Modelled on an Eclipse RTSTRUCT from DCPT: a skin marker overridden with a relative electron
# density only, two structures with a material from Eclipse's library, the rest untouched.

ECLIPSE = {
    "beekley": [("REL_ELEC_DENSITY", "0.001")],
    "HU_Steel": "S45C_Steel",
    "HU_highZ": "S45C_Steel",
}
ROIS = ("BODY", "beekley", "CTV", "HU_Steel", "HU_highZ")


def _eclipse_rtstruct(tmp_path):
    path = dicom_factory.write(tmp_path / "RS.dcm", "RTSTRUCT", rois=ROIS, declared=ECLIPSE)
    return pydicom.dcmread(path)


def test_declared_overrides_are_eclipses_in_rtstruct_order(tmp_path):
    declared = overrides.declared_overrides(_eclipse_rtstruct(tmp_path))

    assert list(declared) == ["beekley", "HU_Steel", "HU_highZ"]
    assert str(declared["beekley"]) == "REL_ELEC_DENSITY=0.001"
    assert declared["beekley"].material_id is None
    assert str(declared["HU_Steel"]) == "MaterialID=S45C_Steel"


def test_an_rtstruct_without_overrides_declares_none(tmp_path):
    path = dicom_factory.write(tmp_path / "RS.dcm", "RTSTRUCT", rois=ROIS)
    assert overrides.declared_overrides(pydicom.dcmread(path)) == {}


def test_rows_start_at_no_override_unless_the_site_maps_the_material(tmp_path):
    """Never a guess: the density-only marker and unmapped names start at "No override"."""
    declared = overrides.declared_overrides(_eclipse_rtstruct(tmp_path))

    rows = overrides.initial_rows(declared, {}, OFFERED)
    assert [(r.roi, r.material) for r in rows] == [("beekley", ""), ("HU_Steel", ""), ("HU_highZ", "")]
    assert rows[0].note == "Eclipse: REL_ELEC_DENSITY=0.001"

    rows = overrides.initial_rows(declared, {"S45C_Steel": "ImplantSteel"}, OFFERED)
    assert [(r.roi, r.material) for r in rows] == [
        ("beekley", ""), ("HU_Steel", "ImplantSteel"), ("HU_highZ", "ImplantSteel")]
    assert rows[1].note == "Eclipse: MaterialID=S45C_Steel, preselected by the site"


def test_a_mapping_to_a_material_not_offered_preselects_nothing(tmp_path):
    declared = overrides.declared_overrides(_eclipse_rtstruct(tmp_path))

    rows = overrides.initial_rows(declared, {"S45C_Steel": "S45C"}, OFFERED)

    assert rows[1].material == ""
    assert "maps S45C_Steel to S45C, which is not offered" in rows[1].note
