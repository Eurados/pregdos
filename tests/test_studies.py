"""Tests for the study directory layout and path resolution (issue #41)."""

import pytest

from pregdos import studies
from pregdos.studies import StudyError


def test_create_study_makes_dicom_subdir(tmp_path):
    name, path = studies.create_study(tmp_path, "headphantom")
    assert name == "headphantom"
    assert (path / "dicom").is_dir()


def test_create_study_deduplicates_names(tmp_path):
    """Re-uploading a study must never merge into or overwrite the previous one."""
    first, _ = studies.create_study(tmp_path, "headphantom")
    second, _ = studies.create_study(tmp_path, "headphantom")
    third, _ = studies.create_study(tmp_path, "headphantom")
    assert [first, second, third] == ["headphantom", "headphantom_2", "headphantom_3"]


def test_study_path_rejects_traversal(tmp_path):
    # secure_filename strips the separators; the containment check is belt-and-braces.
    assert studies.study_path(tmp_path, "../../etc").name == "etc"
    with pytest.raises(StudyError):
        studies.study_path(tmp_path, "")
    with pytest.raises(StudyError):
        studies.study_path(tmp_path, "..")


def test_run_path_rejects_bad_run_id(tmp_path):
    studies.create_study(tmp_path, "s")
    with pytest.raises(StudyError):
        studies.run_path(tmp_path, "s", "not-a-run")
    with pytest.raises(StudyError):
        studies.run_path(tmp_path, "s", "../escape")


def test_create_run_is_always_fresh(tmp_path):
    """Two conversions of one study get distinct, empty run directories.

    This is what makes #41 structural: the generated files are simply the contents of a
    directory that did not exist a moment ago.
    """
    studies.create_study(tmp_path, "s")
    run_a, path_a = studies.create_run(tmp_path, "s")
    (path_a / "topas_field01.txt").write_text("stale")

    run_b, path_b = studies.create_run(tmp_path, "s")
    assert run_a != run_b
    assert list(path_b.iterdir()) == []
    # the earlier run is untouched and cannot leak into the new one
    assert (path_a / "topas_field01.txt").read_text() == "stale"


def test_list_studies_and_runs(tmp_path):
    studies.create_study(tmp_path, "b")
    studies.create_study(tmp_path, "a")
    # a bare directory without dicom/ is not a study
    (tmp_path / "not_a_study").mkdir()
    assert studies.list_studies(tmp_path) == ["a", "b"]

    run_id, _ = studies.create_run(tmp_path, "a")
    assert studies.list_runs(tmp_path, "a") == [run_id]


def test_relative_to_run_points_out_of_the_run_dir(tmp_path):
    """dicomexport bakes its arguments into the TOPAS file, so they must be relative."""
    studies.create_study(tmp_path, "s")
    _, run_dir = studies.create_run(tmp_path, "s")

    assert studies.relative_to_run(studies.dicom_path(tmp_path, "s"), run_dir) == "../dicom"
    assert studies.relative_to_run(studies.study_path(tmp_path, "s") / "spr.txt", run_dir) == "../spr.txt"


def test_find_rtstruct_searches_recursively(tmp_path):
    """A ZIP upload may nest the DICOM one level deep; that is harmless."""
    _, path = studies.create_study(tmp_path, "s")
    assert studies.find_rtstruct(tmp_path, "s") is None

    nested = path / "dicom" / "inner"
    nested.mkdir()
    (nested / "RS.1.dcm").write_bytes(b"")
    assert studies.find_rtstruct(tmp_path, "s").name == "RS.1.dcm"
