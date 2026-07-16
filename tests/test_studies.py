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


def test_create_study_survives_a_lost_race(tmp_path, monkeypatch):
    """Flask serves requests in threads, so two uploads can agree on the same free name.
    The mkdir() must be the claim: the loser retries rather than raising FileExistsError."""
    real_mkdir = studies.Path.mkdir
    calls = []

    def racing_mkdir(self, *args, **kwargs):
        # First claim of "headphantom" loses: simulate another request creating it first.
        if not calls and self.name == "headphantom":
            calls.append(self)
            real_mkdir(self, *args, **kwargs)
            raise FileExistsError(str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(studies.Path, "mkdir", racing_mkdir)
    name, path = studies.create_study(tmp_path, "headphantom")

    assert name == "headphantom_2"       # stepped aside instead of crashing
    assert (path / "dicom").is_dir()


def test_create_study_concurrently_never_collides(tmp_path):
    """Real threads, real filesystem: every winner gets a distinct directory."""
    import threading
    names, errors = [], []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()               # maximise the overlap
        try:
            names.append(studies.create_study(tmp_path, "study")[0])
        except Exception as e:       # noqa: BLE001 - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sorted(names) == sorted(set(names))   # no two threads got the same name
    assert len(names) == 8
    for n in names:
        assert (studies.study_path(tmp_path, n) / "dicom").is_dir()


def test_create_run_survives_a_lost_race(tmp_path, monkeypatch):
    """Two conversions of one study in the same second must not raise FileExistsError."""
    studies.create_study(tmp_path, "s")
    real_mkdir = studies.Path.mkdir
    tripped = []

    def racing_mkdir(self, *args, **kwargs):
        if not tripped and self.name.startswith(studies.RUN_PREFIX):
            tripped.append(self)
            real_mkdir(self, *args, **kwargs)
            raise FileExistsError(str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(studies.Path, "mkdir", racing_mkdir)
    run_id, path = studies.create_run(tmp_path, "s")

    assert run_id.endswith("_2")
    assert path.is_dir() and list(path.iterdir()) == []
    assert studies._RUN_ID_RE.match(run_id)    # still a valid, resolvable run id


def test_create_run_concurrently_never_collides(tmp_path):
    import threading
    studies.create_study(tmp_path, "s")
    ids, errors = [], []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        try:
            ids.append(studies.create_run(tmp_path, "s")[0])
        except Exception as e:       # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sorted(ids) == sorted(set(ids))
    assert all(studies._RUN_ID_RE.match(i) for i in ids)
    assert sorted(studies.list_runs(tmp_path, "s")) == sorted(ids)


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


def test_ensure_root_locks_a_newly_created_root_to_the_owner(tmp_path):
    """The studies root holds patient DICOM, so a root we create ourselves is 0700 (#71)."""
    import stat

    root = tmp_path / "pregdos"
    assert studies.ensure_root(root) is None
    assert stat.S_IMODE((root).stat().st_mode) == 0o700


def test_ensure_root_preserves_an_existing_roots_permissions(tmp_path):
    """An admin-provisioned shared dir (e.g. group-shared 0770) keeps its permissions."""
    import stat

    root = tmp_path / "shared"
    root.mkdir(mode=0o770)
    root.chmod(0o770)  # umask-proof the intent
    assert studies.ensure_root(root) is None
    assert stat.S_IMODE(root.stat().st_mode) == 0o770
