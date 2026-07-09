import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pregdos import studies
from pregdos.webserver import app, allowed_file
from pregdos.models import ConversionParameters, ConversionResult, StructureSelection


class FakeSbatchResult:
    returncode = 0
    stdout = "Submitted batch job 42\n"
    stderr = ""


@pytest.fixture
def client(tmp_path):
    app.config["TESTING"] = True
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with app.test_client() as c:
        yield c


def _make_study(tmp_path, name="mystudy"):
    """Create a study on disk as /upload would leave it."""
    _, path = studies.create_study(tmp_path, name)
    (path / "beam.csv").write_text("col1,col2")
    (path / "spr.txt").write_text("hu,material")
    return path


# --- allowed_file ---

def test_allowed_file_accepts_valid_extensions():
    assert allowed_file("scan.dcm")
    assert allowed_file("beam.csv")
    assert allowed_file("spr.txt")


def test_allowed_file_rejects_invalid_extensions():
    assert not allowed_file("script.py")
    assert not allowed_file("archive.zip")
    assert not allowed_file("noextension")


# --- GET / ---

def test_upload_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"form" in response.data.lower()


# --- POST /upload validation ---

def test_upload_missing_beam_model(client):
    response = client.post("/upload", data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Beam model required" in response.data


def test_upload_missing_study(client):
    data = {
        "beam_model": (io.BytesIO(b"col1,col2"), "beam.csv"),
        "spr_table": (io.BytesIO(b"data"), "spr.txt"),
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"Provide either a ZIP or a folder" in response.data


def test_upload_both_zip_and_folder_rejected(client):
    data = {
        "beam_model": (io.BytesIO(b"col1,col2"), "beam.csv"),
        "spr_table": (io.BytesIO(b"data"), "spr.txt"),
        "study_zip": (io.BytesIO(b"PK\x03\x04"), "study.zip"),
        "study_dir": (io.BytesIO(b"data"), "study/file.dcm"),
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"either ZIP or Folder" in response.data


def test_upload_without_rtstruct_leaves_no_study_behind(client, tmp_path):
    """A failed upload must not leave a half-populated study directory."""
    data = {
        "beam_model": (io.BytesIO(b"col1,col2"), "beam.csv"),
        "spr_table": (io.BytesIO(b"data"), "spr.txt"),
        "study_dir": (io.BytesIO(b"data"), "study/CT.1.dcm"),
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"No RS-file or structures found" in response.data
    assert studies.list_studies(tmp_path) == []


# --- Download path traversal ---

def test_download_nonexistent_file_redirects(client):
    response = client.get("/download/somestudy/run_20260101_000000/topas_field01.txt", follow_redirects=True)
    assert response.status_code == 200
    assert b"form" in response.data.lower()


def test_download_rejects_bad_run_id(client):
    response = client.get("/download/somestudy/../../etc/passwd", follow_redirects=True)
    # Flask's router will not match a run_id containing slashes at all.
    assert response.status_code in (200, 404)


def test_download_traversal_stripped_by_secure_filename(client):
    from werkzeug.utils import secure_filename
    assert secure_filename("../../etc/passwd") == "etc_passwd"
    assert secure_filename("..") == ""


# --- extract_zip path traversal ---

def test_extract_zip_rejects_traversal(tmp_path):
    from pregdos.webserver import extract_zip

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    dest_dir = tmp_path / "study" / "dicom"

    zip_path = source_dir / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../outside.txt", "malicious content")

    class FakeUpload:
        filename = "evil.zip"

        def save(self, path):
            import shutil
            shutil.copy(zip_path, path)

    with pytest.raises(Exception, match="Unsafe zip entry"):
        extract_zip(FakeUpload(), str(dest_dir))


def test_extract_zip_leaves_no_archive_behind(tmp_path):
    from pregdos.webserver import extract_zip

    zip_path = tmp_path / "study.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("CT.1.dcm", "pixels")

    class FakeUpload:
        filename = "study.zip"

        def save(self, path):
            import shutil
            shutil.copy(zip_path, path)

    dest = tmp_path / "study" / "dicom"
    extract_zip(FakeUpload(), str(dest))
    assert (dest / "CT.1.dcm").read_text() == "pixels"
    # the scratch copy of the archive is removed
    assert list(dest.parent.glob("*.part")) == []


# --- /submit route ---

def test_submit_rejects_unknown_run(client, tmp_path):
    _make_study(tmp_path)
    response = client.post(
        "/submit",
        data={"study_name": "mystudy", "run_id": "run_20260101_000000", "out_files": "topas_field01.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Run directory not found" in response.data


def test_submit_rejects_bad_run_id(client, tmp_path):
    _make_study(tmp_path)
    response = client.post(
        "/submit",
        data={"study_name": "mystudy", "run_id": "../../etc", "out_files": "x.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Invalid study path" in response.data


def test_submit_missing_file_flashes_error(client, tmp_path):
    _make_study(tmp_path)
    run_id, _ = studies.create_run(tmp_path, "mystudy")
    response = client.post(
        "/submit",
        data={"study_name": "mystudy", "run_id": run_id, "out_files": "topas_field01.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"not found" in response.data


def test_submit_runs_topas_in_the_run_dir_without_copying(client, tmp_path, mocker, monkeypatch):
    """sbatch must --chdir into the run dir: the .txt's relative DicomDirectory and
    includeFile only resolve from there, and the file is never moved (#41)."""
    monkeypatch.setenv("PREGDOS_EXECUTOR", "slurm")
    _make_study(tmp_path)
    run_id, run_dir = studies.create_run(tmp_path, "mystudy")
    (run_dir / "topas_field01.txt").write_text("# topas input")

    mock_run = mocker.patch("pregdos.executor.subprocess.run", return_value=FakeSbatchResult())
    mocker.patch("pregdos.executor.os.geteuid", return_value=1000)
    response = client.post(
        "/submit",
        data={"study_name": "mystudy", "run_id": run_id, "out_files": "topas_field01.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"42" in response.data

    cmd = mock_run.call_args[0][0]
    assert "sbatch" in cmd
    assert f"--chdir={run_dir}" in cmd
    # the generated file stays where it was generated
    assert (run_dir / "topas_field01.txt").is_file()


def test_submit_without_slurm_runs_locally(client, tmp_path, monkeypatch):
    """The regression that motivated #43: a workstation has TOPAS but no sbatch/runuser,
    and /submit used to crash with FileNotFoundError: 'runuser'."""
    monkeypatch.delenv("PREGDOS_EXECUTOR", raising=False)
    monkeypatch.setattr("pregdos.executor.shutil.which", lambda name: None)  # no sbatch
    monkeypatch.setenv("TOPAS_BIN", "true")

    _make_study(tmp_path)
    run_id, run_dir = studies.create_run(tmp_path, "mystudy")
    (run_dir / "topas_field01.txt").write_text("# topas input")

    response = client.post(
        "/submit",
        data={"study_name": "mystudy", "run_id": run_id, "out_files": "topas_field01.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"locally in the background" in response.data
    assert (run_dir / "run.json").is_file()


def test_submit_sbatch_failure_flashes_error(client, tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("PREGDOS_EXECUTOR", "slurm")
    _make_study(tmp_path)
    run_id, run_dir = studies.create_run(tmp_path, "mystudy")
    (run_dir / "topas_field01.txt").write_text("# topas input")

    failed = FakeSbatchResult()
    failed.returncode = 1
    failed.stdout = ""
    failed.stderr = "slurmctld not running"
    mocker.patch("pregdos.executor.subprocess.run", return_value=failed)
    mocker.patch("pregdos.executor.os.geteuid", return_value=1000)

    response = client.post(
        "/submit",
        data={"study_name": "mystudy", "run_id": run_id, "out_files": "topas_field01.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"slurmctld not running" in response.data


# --- POST /convert input validation ---

def _convert_form(tmp_path, overrides=None):
    """Minimal valid /convert form data for a study that exists on disk."""
    _make_study(tmp_path)
    data = {
        "study_name": "mystudy",
        "beam_model_name": "beam.csv",
        "spr_table_name": "spr.txt",
        "nstat": "1000000",
        "output_basename": "topas",
    }
    if overrides:
        data.update(overrides)
    return data


def test_convert_invalid_preset_nstat_flashes_error(client, tmp_path):
    data = _convert_form(tmp_path, {"nstat": "abc"})
    resp = client.post("/convert", data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid number of primaries" in resp.data


def test_convert_invalid_custom_nstat_flashes_error(client, tmp_path):
    data = _convert_form(tmp_path, {"nstat": "custom", "nstat_custom": "notanumber"})
    resp = client.post("/convert", data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid number of primaries" in resp.data


def test_convert_negative_custom_nstat_flashes_error(client, tmp_path):
    data = _convert_form(tmp_path, {"nstat": "custom", "nstat_custom": "-5"})
    resp = client.post("/convert", data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid number of primaries" in resp.data


def test_convert_invalid_basename_flashes_error(client, tmp_path):
    data = _convert_form(tmp_path, {"output_basename": "my.plan"})
    resp = client.post("/convert", data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid output basename" in resp.data


def test_convert_basename_with_spaces_flashes_error(client, tmp_path):
    data = _convert_form(tmp_path, {"output_basename": "my plan"})
    resp = client.post("/convert", data=data, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Invalid output basename" in resp.data


def test_convert_invalid_nstat_creates_no_run_dir(client, tmp_path):
    """Validation happens before anything is created on disk."""
    data = _convert_form(tmp_path, {"nstat": "abc"})
    client.post("/convert", data=data, follow_redirects=True)
    assert studies.list_runs(tmp_path, "mystudy") == []


# --- /convert scorer post-processing (issues #36, #41) ---

# Minimal stand-in for a dicomexport-generated TOPAS file: a SCORER SET UP block holding
# only the in-field DoseToWater scorer, followed by a TIME FEATURES banner.  The two
# banners are what append_scorers() keys off.  The DicomDirectory / includeFile lines
# mimic dicomexport echoing its arguments verbatim.
_FAKE_TOPAS_TEMPLATE = (
    '# fake dicomexport output\n'
    'includeFile                          = {spr}\n'
    's:Ge/Patient/DicomDirectory          = "{dicom}"\n'
    '\n'
    '##############################################\n'
    '###       S C O R E R    S E T U P         ###\n'
    '##############################################\n'
    's:Sc/Dose/Quantity                   = "DoseToWater"\n'
    's:Sc/Dose/Component                  = "Patient/RTDoseGrid"\n'
    's:Sc/Dose/OutputFile                 = "topas_field1"\n'
    '\n'
    '##############################################\n'
    '###       T I M E   F E A T U R E S        ###\n'
    '##############################################\n'
    'd:Tf/TimelineEnd = 1 ms\n'
)


def _fake_dicomexport(cmd, *args, **kwargs):
    """subprocess.run side_effect mimicking dicomexport.

    Crucially it honours ``cwd`` and echoes its path arguments into the generated file
    verbatim -- exactly as the real tool does (it performs no path resolution).
    """
    cwd = Path(kwargs["cwd"])
    output_base = cmd[-1]
    dicom_arg = cmd[-2]
    spr_arg = cmd[cmd.index("-s") + 1]
    (cwd / f"{output_base}_field01.txt").write_text(
        _FAKE_TOPAS_TEMPLATE.format(spr=spr_arg, dicom=dicom_arg)
    )
    result = MagicMock()
    result.returncode = 0
    result.stdout = "ok"
    result.stderr = ""
    return result


def _scorer_form(tmp_path):
    form = _convert_form(tmp_path)
    form.update({"keep_infield": "1", "score_neutron": "CTV", "score_gamma": "CTV"})
    return form


def test_convert_invokes_dicomexport_in_run_dir_with_relative_paths(client, tmp_path, mocker):
    """The "generate where you execute" invariant (#41).

    dicomexport runs with cwd == the run dir and is handed relative paths, so the paths it
    bakes into the TOPAS input are already correct for a TOPAS run started from there.
    """
    mock_run = mocker.patch("pregdos.webserver.subprocess.run", side_effect=_fake_dicomexport)
    resp = client.post("/convert", data=_scorer_form(tmp_path), follow_redirects=True)
    assert resp.status_code == 200

    (run_id,) = studies.list_runs(tmp_path, "mystudy")
    run_dir = studies.run_path(tmp_path, "mystudy", run_id)

    cmd = mock_run.call_args[0][0]
    assert mock_run.call_args.kwargs["cwd"] == str(run_dir)
    assert cmd[-2] == "../dicom"
    assert cmd[cmd.index("-s") + 1] == "../spr.txt"
    assert cmd[cmd.index("-b") + 1] == "../beam.csv"


def test_generated_topas_file_contains_no_absolute_paths(client, tmp_path, mocker):
    """A generated study must survive `mv`, so nothing may reference the studies root."""
    mocker.patch("pregdos.webserver.subprocess.run", side_effect=_fake_dicomexport)
    client.post("/convert", data=_scorer_form(tmp_path), follow_redirects=True)

    (run_id,) = studies.list_runs(tmp_path, "mystudy")
    text = (studies.run_path(tmp_path, "mystudy", run_id) / "topas_field01.txt").read_text()

    assert str(tmp_path) not in text
    assert 's:Ge/Patient/DicomDirectory          = "../dicom"' in text
    assert "includeFile                          = ../spr.txt" in text


def test_convert_appends_selected_scorers_and_survives_rerun(client, tmp_path, mocker):
    """Selecting multiple quantities for one structure appends those scorer blocks, and a
    re-run of the same study does not silently drop them (regression for issue #36)."""
    mocker.patch("pregdos.webserver.subprocess.run", side_effect=_fake_dicomexport)
    form = _scorer_form(tmp_path)

    for run in ("first", "rerun"):
        resp = client.post("/convert", data=form, follow_redirects=True)
        assert resp.status_code == 200, run
        run_id = studies.list_runs(tmp_path, "mystudy")[0]
        text = (studies.run_path(tmp_path, "mystudy", run_id) / "topas_field01.txt").read_text()
        assert "DoseToWater" in text, run       # original in-field scorer preserved …
        assert "AmBDose_CTV" in text, run       # … plus both selected out-of-field scorers
        assert "DoseGamma_CTV" in text, run


def test_rerun_creates_a_new_run_dir_and_cannot_inherit_stale_files(client, tmp_path, mocker):
    """Each conversion is isolated in a fresh directory, so a previous run's output can
    never be discovered as if it belonged to this one (#41)."""
    mocker.patch("pregdos.webserver.subprocess.run", side_effect=_fake_dicomexport)
    form = _scorer_form(tmp_path)

    client.post("/convert", data=form, follow_redirects=True)
    first_run = studies.list_runs(tmp_path, "mystudy")[0]
    # a stale field file from a conversion that produced more fields
    (studies.run_path(tmp_path, "mystudy", first_run) / "topas_field99.txt").write_text("stale")

    resp = client.post("/convert", data=form, follow_redirects=True)
    runs = studies.list_runs(tmp_path, "mystudy")
    assert len(runs) == 2
    second_dir = studies.run_path(tmp_path, "mystudy", runs[0])
    assert sorted(p.name for p in second_dir.glob("*.txt")) == ["topas_field01.txt"]
    assert b"topas_field99.txt" not in resp.data


def test_convert_scorer_failure_is_reported_and_not_successful(client, tmp_path, mocker):
    """If scorer post-processing fails, the user gets a visible error naming the affected
    file and the conversion is not presented as successful (issue #36)."""
    mocker.patch("pregdos.webserver.subprocess.run", side_effect=_fake_dicomexport)
    mocker.patch("pregdos.webserver.append_scorers", side_effect=RuntimeError("boom"))

    form = _convert_form(tmp_path)
    form.update({"keep_infield": "1", "score_neutron": "CTV"})
    resp = client.post("/convert", data=form, follow_redirects=True)

    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Scorer post-processing failed" in body   # error names the affected file …
    assert "topas_field01.txt" in body
    assert "Submit Jobs" not in body                 # … and is not treated as success
    # the failed run leaves nothing behind
    assert studies.list_runs(tmp_path, "mystudy") == []


def test_convert_failure_removes_the_run_dir(client, tmp_path, mocker):
    """A dicomexport failure must not leave an empty run directory lying around."""
    def _boom(cmd, *args, **kwargs):
        import subprocess
        raise subprocess.CalledProcessError(1, cmd, stderr="dicomexport exploded")

    mocker.patch("pregdos.webserver.subprocess.run", side_effect=_boom)
    resp = client.post("/convert", data=_convert_form(tmp_path), follow_redirects=True)
    assert b"dicomexport exploded" in resp.data
    assert studies.list_runs(tmp_path, "mystudy") == []


# --- /jobs listing ---

def test_list_jobs_shows_runs_across_studies(client, tmp_path):
    _make_study(tmp_path, "alpha")
    _make_study(tmp_path, "beta")
    run_a, dir_a = studies.create_run(tmp_path, "alpha")
    (dir_a / "topas_field01.txt").write_text("x")

    resp = client.get("/jobs")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "alpha" in body and run_a in body
    # beta has no runs yet, so it contributes no rows
    assert body.count("run_") >= 1


# --- Models smoke tests ---

def test_conversion_parameters_defaults():
    p = ConversionParameters(
        study_name="mystudy",
        run_dir="/studies/mystudy/run_20260101_000000",
        dicom_rel="../dicom",
        beam_model_rel="../beam.csv",
        spr_table_rel="../spr.txt",
        output_basename="topas",
    )
    assert p.field_nr is None
    assert p.nstat is None


def test_conversion_result_fields():
    r = ConversionResult(
        out_files=["topas_field01.txt"],
        study_name="mystudy",
        run_id="run_20260101_000000",
    )
    assert r.out_files == ["topas_field01.txt"]
    assert r.selected_structures == []
    assert r.stdout is None


def test_structure_selection_defaults():
    s = StructureSelection(study_dir="/tmp/study")
    assert s.available_structures == []
    assert s.selected_structures == []
