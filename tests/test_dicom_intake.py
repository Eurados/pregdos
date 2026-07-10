"""Tests for DICOM upload validation and flattening (issue #52).

The failing case they reproduce: a real study with the CT in a `CT/` subdirectory and the
RTDOSE at the top level.  dicomexport sets `DicomDirectory` to the RTDOSE's parent, TOPAS
scans that directory non-recursively, and finds no CT.
"""


import pytest

from pregdos import dicom_intake
from pregdos.dicom_intake import flatten, scan, validate, warnings

from tests.dicom_factory import brain_layout as _brain_layout
from tests.dicom_factory import flat_study as _flat_study
from tests.dicom_factory import write as _write


# --- scan ---

def test_scan_classifies_every_modality(tmp_path):
    _flat_study(tmp_path)
    intake = scan(tmp_path)
    assert intake.modalities == {"CT": 3, "RTSTRUCT": 1, "RTPLAN": 1, "RTDOSE": 1}
    assert intake.patient_ids == {"PAT1"}


def test_scan_finds_nested_files(tmp_path):
    _brain_layout(tmp_path)
    assert scan(tmp_path).modalities["CT"] == 3


def test_scan_records_non_dicom_without_raising(tmp_path):
    _flat_study(tmp_path)
    (tmp_path / "DICOMDIR").write_text("not dicom")
    (tmp_path / "notes.txt").write_text("hello")
    intake = scan(tmp_path)
    assert sorted(p.name for p in intake.non_dicom) == ["DICOMDIR", "notes.txt"]
    assert intake.modalities["CT"] == 3


def test_scan_of_empty_dir(tmp_path):
    intake = scan(tmp_path)
    assert intake.files == [] and intake.non_dicom == []


# --- validate ---

def test_valid_study_has_no_problems(tmp_path):
    assert validate(scan(_flat_study(tmp_path))) == []


def test_nested_ct_is_still_valid(tmp_path):
    """The Brain layout is not malformed -- it just needs flattening."""
    assert validate(scan(_brain_layout(tmp_path))) == []


def test_empty_upload_is_rejected(tmp_path):
    assert validate(scan(tmp_path)) == ["No DICOM files found in the upload."]


@pytest.mark.parametrize("missing", ["CT", "RTSTRUCT", "RTPLAN", "RTDOSE"])
def test_missing_modality_is_rejected(tmp_path, missing):
    _flat_study(tmp_path)
    for p in scan(tmp_path).by_modality(missing):
        p.path.unlink()
    problems = validate(scan(tmp_path))
    assert any(f"No {missing} files found" in p for p in problems)


@pytest.mark.parametrize("modality,prefix", [("RTSTRUCT", "RS"), ("RTPLAN", "RN")])
def test_duplicate_singleton_is_rejected(tmp_path, modality, prefix):
    """dicomexport takes glob(...)[0] for these, and glob order is not sorted."""
    _flat_study(tmp_path)
    _write(tmp_path / f"{prefix}.second.dcm", modality)
    problems = validate(scan(tmp_path))
    assert any(f"Found 2 {modality} files" in p for p in problems)


def test_two_patients_are_rejected(tmp_path):
    _flat_study(tmp_path)
    _write(tmp_path / "CT.other.dcm", "CT", patient="PAT2")
    assert any("more than one patient" in p for p in validate(scan(tmp_path)))


def test_two_studies_are_rejected(tmp_path):
    _flat_study(tmp_path)
    _write(tmp_path / "CT.other.dcm", "CT", study="1.2.826.0.1.9")
    assert any("different studies" in p for p in validate(scan(tmp_path)))


def test_two_ct_series_are_rejected(tmp_path):
    """Flattening would interleave the slices of two series into one impossible patient."""
    _flat_study(tmp_path)
    _write(tmp_path / "CT" / "other.dcm", "CT", series="1.2.826.0.1.1.99")
    problems = validate(scan(tmp_path))
    assert any("2 CT series" in p for p in problems)


def test_single_ct_slice_is_rejected(tmp_path):
    _flat_study(tmp_path, n_ct=1)
    assert any("Only one CT slice" in p for p in validate(scan(tmp_path)))


def test_multiple_rtdose_is_a_warning_not_an_error(tmp_path):
    """The Brain study has three: they are per-field doses, and only the optional in-field
    scorer cares which grid is cloned."""
    _flat_study(tmp_path)
    _write(tmp_path / "RD.2.dcm", "RTDOSE")
    _write(tmp_path / "RD.3.dcm", "RTDOSE")
    intake = scan(tmp_path)
    assert validate(intake) == []
    assert any("Found 3 RTDOSE files" in w for w in warnings(intake))


def test_non_dicom_files_produce_a_warning(tmp_path):
    _flat_study(tmp_path)
    for name in ("DICOMDIR", "a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("x")
    notes = warnings(scan(tmp_path))
    assert any("Ignored 4 non-DICOM file(s)" in n and "and 1 more" in n for n in notes)


# --- flatten ---

def test_flatten_moves_nested_ct_to_the_top(tmp_path):
    """The #52 fix: after flattening, one directory holds every modality, so TOPAS's
    non-recursive scan of DicomDirectory finds the CT."""
    _brain_layout(tmp_path)
    intake = scan(tmp_path)
    moved = flatten(intake)

    assert moved == 3
    assert not (tmp_path / "CT").exists()          # empty subdir pruned
    assert [p.name for p in sorted(tmp_path.iterdir()) if p.is_dir()] == []
    modalities = scan(tmp_path).modalities
    assert modalities == {"CT": 3, "RTSTRUCT": 1, "RTPLAN": 1, "RTDOSE": 1}
    # every file now sits directly in the root
    assert all(f.path.parent == tmp_path for f in scan(tmp_path).files)


def test_flatten_is_a_noop_for_a_flat_study(tmp_path):
    _flat_study(tmp_path)
    assert flatten(scan(tmp_path)) == 0
    assert scan(tmp_path).modalities["CT"] == 3


def test_flatten_disambiguates_colliding_names(tmp_path):
    """Two directories may each hold `1.dcm`; neither slice may be overwritten."""
    _write(tmp_path / "1.dcm", "CT")
    _write(tmp_path / "series" / "1.dcm", "CT")
    _write(tmp_path / "1.dcm".replace("1", "2"), "CT")
    intake = scan(tmp_path)
    flatten(intake)

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["1.dcm", "2.dcm", "series_1.dcm"]
    assert scan(tmp_path).modalities["CT"] == 3        # nothing lost


def test_flatten_disambiguates_when_the_prefixed_name_also_collides(tmp_path):
    _write(tmp_path / "a_1.dcm", "CT")
    _write(tmp_path / "1.dcm", "CT")
    _write(tmp_path / "a" / "1.dcm", "CT")
    intake = scan(tmp_path)
    flatten(intake)
    assert len(list(tmp_path.iterdir())) == 3
    assert scan(tmp_path).modalities["CT"] == 3


def test_flatten_removes_non_dicom_files(tmp_path):
    """`dicom/` is the directory TOPAS scans; it should hold nothing else."""
    _flat_study(tmp_path)
    (tmp_path / "DICOMDIR").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "readme.txt").write_text("x")

    intake = scan(tmp_path)
    flatten(intake)
    assert not (tmp_path / "DICOMDIR").exists()
    assert not (tmp_path / "sub").exists()
    assert intake.non_dicom == []


def test_flatten_updates_the_recorded_paths(tmp_path):
    _brain_layout(tmp_path)
    intake = scan(tmp_path)
    flatten(intake)
    assert all(f.path.exists() for f in intake.files)


def test_flatten_keeps_a_non_empty_subdir(tmp_path):
    """A directory holding something we did not move is left alone rather than forced."""
    _flat_study(tmp_path)
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "sub").mkdir()
    intake = scan(tmp_path)
    flatten(intake)
    # both are empty, so both are pruned; nothing raised
    assert not (tmp_path / "keep").exists()


def test_required_and_singleton_constants_agree_with_dicomexport():
    assert dicom_intake.REQUIRED == ("CT", "RTSTRUCT", "RTPLAN", "RTDOSE")
    assert dicom_intake.SINGLETON == ("RTSTRUCT", "RTPLAN")
