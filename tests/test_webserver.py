import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from pregdos import dicom_intake, studies
from tests import dicom_factory
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


@pytest.fixture(autouse=True)
def _allow_submit(monkeypatch):
    """Let /submit through by default; the #49 toolchain guard has its own test.  Otherwise
    every submit test would depend on the host's installed TOPAS version."""
    monkeypatch.setattr("pregdos.webserver.versions.submit_blocker", lambda: None)


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


def test_upload_of_non_dicom_leaves_no_study_behind(client, tmp_path):
    """A failed upload must not leave a half-populated study directory."""
    data = {
        "beam_model": (io.BytesIO(b"col1,col2"), "beam.csv"),
        "spr_table": (io.BytesIO(b"data"), "spr.txt"),
        "study_dir": (io.BytesIO(b"data"), "study/CT.1.dcm"),
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"No DICOM files found in the upload" in response.data
    assert studies.list_studies(tmp_path) == []


# --- DICOM intake through the upload route (issue #52) ---

def _dir_upload(source_root, prefix="study"):
    """Build the multipart `study_dir` payload a browser folder-upload produces."""
    files = []
    for p in sorted(source_root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(source_root)
            files.append((io.BytesIO(p.read_bytes()), f"{prefix}/{rel.as_posix()}"))
    return files


def _upload_data(source_root, prefix="study"):
    return {
        "beam_model": (io.BytesIO(b"col1,col2"), "beam.csv"),
        "spr_table": (io.BytesIO(b"data"), "spr.txt"),
        "study_dir": _dir_upload(source_root, prefix),
    }


def test_upload_flattens_a_nested_ct(client, tmp_path):
    """The #52 failure: CT in a `CT/` subdirectory, RTDOSE at the top.  TOPAS scans
    DicomDirectory non-recursively, so everything must end up side by side."""
    source = tmp_path / "src"
    dicom_factory.brain_layout(source)

    resp = client.post("/upload", data=_upload_data(source, "Brain"),
                       content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200
    assert b"CTV" in resp.data and b"Fetus" in resp.data      # the setup page rendered

    dicom = studies.dicom_path(tmp_path, "Brain")
    assert [p.name for p in dicom.iterdir() if p.is_dir()] == []   # no subdirectories left
    modalities = dicom_intake.scan(dicom).modalities
    assert modalities == {"CT": 3, "RTSTRUCT": 1, "RTPLAN": 1, "RTDOSE": 1}


def test_upload_accepts_an_already_flat_study(client, tmp_path):
    source = tmp_path / "src"
    dicom_factory.flat_study(source)
    resp = client.post("/upload", data=_upload_data(source),
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"CTV" in resp.data
    assert dicom_intake.scan(studies.dicom_path(tmp_path, "study")).modalities["CT"] == 3


@pytest.mark.parametrize("modality", ["RTSTRUCT", "RTPLAN", "RTDOSE"])
def test_upload_rejects_a_study_missing_a_modality(client, tmp_path, modality):
    source = tmp_path / "src"
    dicom_factory.flat_study(source)
    for f in dicom_intake.scan(source).by_modality(modality):
        f.path.unlink()

    resp = client.post("/upload", data=_upload_data(source),
                       content_type="multipart/form-data", follow_redirects=True)
    assert f"No {modality} files found".encode() in resp.data
    assert studies.list_studies(tmp_path) == []      # nothing left behind


def test_upload_rejects_two_ct_series(client, tmp_path):
    """Flattening would merge them into one impossible patient."""
    source = tmp_path / "src"
    dicom_factory.flat_study(source)
    dicom_factory.write(source / "other" / "CT.x.dcm", "CT", series="1.2.826.0.1.1.99")

    resp = client.post("/upload", data=_upload_data(source),
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"2 CT series" in resp.data
    assert studies.list_studies(tmp_path) == []


def test_upload_rejects_two_patients(client, tmp_path):
    source = tmp_path / "src"
    dicom_factory.flat_study(source)
    dicom_factory.write(source / "CT.other.dcm", "CT", patient="PAT2")

    resp = client.post("/upload", data=_upload_data(source),
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"more than one patient" in resp.data
    assert studies.list_studies(tmp_path) == []


def test_upload_warns_about_multiple_rtdose_but_proceeds(client, tmp_path):
    """The Brain study has three per-field RTDOSE files; only the optional in-field scorer
    cares which grid is cloned."""
    source = tmp_path / "src"
    dicom_factory.flat_study(source)
    dicom_factory.write(source / "RD.2.dcm", "RTDOSE")
    dicom_factory.write(source / "RD.3.dcm", "RTDOSE")

    resp = client.post("/upload", data=_upload_data(source),
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"Found 3 RTDOSE files" in resp.data
    assert b"CTV" in resp.data                        # warned, but the setup page rendered


def test_upload_discards_unusable_files(client, tmp_path):
    """`dicom/` is the directory TOPAS scans; nothing else belongs in it."""
    source = tmp_path / "src"
    dicom_factory.flat_study(source)
    (source / "DICOMDIR").write_text("junk")

    resp = client.post("/upload", data=_upload_data(source),
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"Discarded 1 unusable file" in resp.data
    assert not (studies.dicom_path(tmp_path, "study") / "DICOMDIR").exists()


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


def test_submit_refused_on_broken_toolchain(client, tmp_path, monkeypatch):
    """#49 pre-flight guard: an unsupported OpenTOPAS (or missing G4 data) must stop the
    submission before any hours-long field is launched."""
    monkeypatch.setattr("pregdos.webserver.versions.submit_blocker",
                        lambda: "OpenTOPAS 4.0.0 corrupts the scorer Sum (issue #49).")
    _make_study(tmp_path)
    run_id, run_dir = studies.create_run(tmp_path, "mystudy")
    (run_dir / "topas_field01.txt").write_text("# topas input")

    resp = client.post(
        "/submit",
        data={"study_name": "mystudy", "run_id": run_id, "out_files": "topas_field01.txt"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Cannot submit" in resp.data and b"#49" in resp.data
    assert not (run_dir / "run.json").exists()      # nothing was launched


def test_debug_is_off_by_default(monkeypatch, mocker):
    from pregdos import webserver
    monkeypatch.delenv("PREGDOS_DEBUG", raising=False)
    run = mocker.patch.object(webserver.app, "run")
    webserver.main()
    assert run.call_args.kwargs["debug"] is False


def test_debug_opt_in_via_env(monkeypatch, mocker):
    from pregdos import webserver
    monkeypatch.setenv("PREGDOS_DEBUG", "1")
    run = mocker.patch.object(webserver.app, "run")
    webserver.main()
    assert run.call_args.kwargs["debug"] is True


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


# --- results viewer (#32) ---

_REAL_CSV = Path(__file__).parent / "data" / "topas_field01_neutron_BrainStem.csv"
_REAL_TOPAS = Path(__file__).parent / "data" / "topas_field01.txt"


def _completed_run(tmp_path, study="alpha"):
    """A study with one finished run holding a real scorer CSV."""
    _make_study(tmp_path, study)
    run_id, run_dir = studies.create_run(tmp_path, study)
    for src in (_REAL_CSV, _REAL_TOPAS):
        (run_dir / src.name).write_bytes(src.read_bytes())
    (run_dir / "topas_field01.exit_code").write_text("0\n")
    (run_dir / "run.json").write_text(json.dumps({
        "backend": "local", "submitted": "2026-07-09T21:05:06",
        "fields": [{"topas_file": "topas_field01.txt", "ident": "1"}],
    }))
    return run_id, run_dir


def test_studies_page_lists_tiles_with_status(client, tmp_path):
    run_id, _ = _completed_run(tmp_path, "alpha")
    _make_study(tmp_path, "beta")  # uploaded, never converted

    resp = client.get("/studies")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "alpha" in body and run_id in body and "completed" in body
    assert "beta" in body and "not converted yet" in body
    assert ">View</a>" in body                         # the per-run button


def test_studies_page_shows_a_progress_pie_for_a_running_run(client, tmp_path):
    """A running run gets a history-weighted progress pie next to the badge; a completed
    one does not (nothing to show)."""
    import json

    _make_study(tmp_path, "alpha")
    run_id, run_dir = studies.create_run(tmp_path, "alpha")
    # one field, half its histories started
    (run_dir / "topas_field01.txt").write_text(
        "uv:Tf/spotWeight/Values                   = 2 100 100\n")
    (run_dir / "topas_field01.log").write_text(
        "Begin processing for Run: 0, History: 0\n")     # run 0 of 2 -> 50%
    (run_dir / "run.json").write_text(json.dumps({
        "backend": "local", "submitted": "now",
        "fields": [{"topas_file": "topas_field01.txt", "ident": "1"}],
    }))

    body = client.get("/studies").data.decode()
    assert "progress-pie" in body
    assert "--pct: 50" in body
    assert "50% of all fields complete" in body          # the title/alt text
    assert "setTimeout" in body                          # auto-refresh while a run is live


def test_jobs_url_redirects_to_studies(client, tmp_path):
    resp = client.get("/jobs")
    assert resp.status_code == 302
    assert "/studies" in resp.headers["Location"]


def test_run_detail_shows_scaled_scorer_results(client, tmp_path):
    run_id, _ = _completed_run(tmp_path)
    resp = client.get(f"/studies/alpha/{run_id}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "AmBDose_BrainStem" in body and "BrainStem" in body and "Sv" in body
    # 1.049973996636311e-11 Sv * 953656.09 = 1.0013e-05
    assert "1.001e-05" in body
    assert "under validation" in body         # the #50 caveat is surfaced


def test_run_detail_of_running_job_says_so(client, tmp_path):
    _make_study(tmp_path, "alpha")
    run_id, run_dir = studies.create_run(tmp_path, "alpha")
    (run_dir / "run.json").write_text(json.dumps({
        "backend": "local", "submitted": "now",
        "fields": [{"topas_file": "topas_field01.txt", "ident": "1"}],
    }))
    resp = client.get(f"/studies/alpha/{run_id}")
    assert b"still going" in resp.data


def test_run_detail_unparseable_csv_warns_not_500(client, tmp_path):
    run_id, run_dir = _completed_run(tmp_path)
    (run_dir / "broken.csv").write_text("# nothing useful\n")
    resp = client.get(f"/studies/alpha/{run_id}")
    assert resp.status_code == 200
    assert b"Could not read scorer output" in resp.data
    assert b"AmBDose_BrainStem" in resp.data      # the good CSV still renders


def test_run_detail_missing_run_redirects(client, tmp_path):
    _make_study(tmp_path, "alpha")
    resp = client.get("/studies/alpha/run_20260101_000000", follow_redirects=True)
    assert b"Run directory not found" in resp.data


def test_report_csv_download(client, tmp_path):
    run_id, _ = _completed_run(tmp_path)
    resp = client.get(f"/studies/alpha/{run_id}/report.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    body = resp.data.decode()
    assert "AmBDose_BrainStem" in body and "AmbientDoseEquivalent" in body
    assert "#50" in body                          # caveat travels with the data


def test_report_csv_marks_nan_rows_unusable(client, tmp_path):
    run_id, run_dir = _completed_run(tmp_path)
    (run_dir / "old.csv").write_text(
        "# Parameter File: topas_field01.txt\n"
        "# Results for scorer: AmBDose_Fetus\n"
        "# AmbientDoseEquivalent ( Sv ) : Sum   Standard_Deviation   \n"
        "-nan, 1e-15\n"
    )
    body = client.get(f"/studies/alpha/{run_id}/report.csv").data.decode()
    assert "AmBDose_Fetus" in body and "4.2.3" in body   # flagged, not rendered as a dose

    page = client.get(f"/studies/alpha/{run_id}").data.decode()
    assert "unusable" in page
    assert "-nan" not in page and "nan," not in page


# --- grouping and per-scorer totals ---

def _row(scorer="S", field=1, total=1.0, sd=0.1, problem=None, unit="Gy"):
    return {"scorer": scorer, "structure": "CTV", "quantity": "DoseToMedium", "unit": unit,
            "field": field, "field_name": f"Field {field}", "sum": total, "sd": sd,
            "problem": problem, "raw_sum": total, "scale": 1.0, "csv_name": "x.csv"}


def test_group_rows_totals_fields_and_adds_sd_in_quadrature():
    from pregdos.webserver import _group_rows
    groups = _group_rows([_row(field=2, total=2.0, sd=0.3), _row(field=1, total=1.0, sd=0.4)])
    assert len(groups) == 1
    g = groups[0]
    assert [r["field"] for r in g["rows"]] == [1, 2]        # sorted by field within the scorer
    assert g["total_sum"] == pytest.approx(3.0)
    # independent Monte Carlo runs: sqrt(0.4^2 + 0.3^2) = 0.5, NOT 0.7
    assert g["total_sd"] == pytest.approx(0.5)


def test_group_rows_sorts_by_scorer_then_field():
    from pregdos.webserver import _group_rows
    groups = _group_rows([_row(scorer="Zeta", field=1), _row(scorer="Alpha", field=2),
                          _row(scorer="Alpha", field=1)])
    assert [g["scorer"] for g in groups] == ["Alpha", "Zeta"]
    assert [r["field"] for r in groups[0]["rows"]] == [1, 2]


def test_no_total_for_a_single_field():
    from pregdos.webserver import _group_rows
    (g,) = _group_rows([_row(field=1)])
    assert g["total_sum"] is None       # a "total" of one row is just noise


def test_no_total_when_any_field_is_unusable():
    """A partial sum over fields would understate the dose while looking authoritative."""
    from pregdos.webserver import _group_rows
    (g,) = _group_rows([_row(field=1, total=1.0), _row(field=2, total=None, problem="NaN Sum")])
    assert g["total_sum"] is None
    assert g["n_fields"] == 2


def test_total_sd_absent_when_a_field_lacks_one():
    from pregdos.webserver import _group_rows
    (g,) = _group_rows([_row(field=1, sd=0.1), _row(field=2, sd=None)])
    assert g["total_sum"] == pytest.approx(2.0)
    assert g["total_sd"] is None


def test_run_detail_renders_a_total_row(client, tmp_path):
    run_id, run_dir = _completed_run(tmp_path)
    _second_field_csv(run_dir)           # a second field, so the group gets a total
    body = client.get(f"/studies/alpha/{run_id}").data.decode()
    assert "All 2 fields" in body
    assert "scorer-total" in body


def _second_field_csv(run_dir, param_file="topas_field02.txt"):
    (run_dir / "topas_field02_neutron_BrainStem.csv").write_text(
        f"# Parameter File: {param_file}\n"
        "# Results for scorer: AmBDose_BrainStem\n"
        '# Filtered by: OnlyIncludeIfInRTStructure = 1 "BrainStem"\n'
        "# AmbientDoseEquivalent ( Sv ) : Sum   Standard_Deviation   \n"
        "2.0e-11, 1.0e-15\n"
    )
    (run_dir / param_file).write_bytes(_REAL_TOPAS.read_bytes())


def test_report_csv_includes_all_field_totals(client, tmp_path):
    run_id, run_dir = _completed_run(tmp_path)
    _second_field_csv(run_dir)
    body = client.get(f"/studies/alpha/{run_id}/report.csv").data.decode()
    assert "ALL," in body and "sum over 2 fields" in body
    assert "quadrature" in body          # the caveat travels with the data


def test_no_total_when_two_csvs_share_a_field(client, tmp_path):
    """`IfOutputFileAlreadyExists = Increment` re-runs a field into `..._1.csv`.  Summing the
    two would double-count that field."""
    from pregdos.webserver import _group_rows
    (g,) = _group_rows([_row(field=1, total=1.0), _row(field=1, total=1.0)])
    assert g["total_sum"] is None
    assert g["n_fields"] == 2


# --- delete study ---

def test_delete_study_removes_everything(client, tmp_path, mocker):
    _completed_run(tmp_path, "alpha")
    _make_study(tmp_path, "beta")
    cancel = mocker.patch("pregdos.webserver.executor.cancel_run")

    resp = client.post("/studies/alpha/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Deleted study alpha" in resp.data
    assert studies.list_studies(tmp_path) == ["beta"]
    cancel.assert_not_called()          # the run had finished


def test_delete_study_cancels_an_unfinished_run(client, tmp_path, mocker):
    """A TOPAS job left running would keep writing into a directory we just removed."""
    _make_study(tmp_path, "alpha")
    run_id, run_dir = studies.create_run(tmp_path, "alpha")
    (run_dir / "run.json").write_text(json.dumps({
        "backend": "local", "submitted": "now",
        "fields": [{"topas_file": "topas_field01.txt", "ident": "999"}],
    }))
    cancel = mocker.patch("pregdos.webserver.executor.cancel_run")

    resp = client.post("/studies/alpha/delete", follow_redirects=True)
    assert b"Cancelled 1 unfinished run" in resp.data
    cancel.assert_called_once()
    assert studies.list_studies(tmp_path) == []


def test_delete_unknown_study_flashes(client, tmp_path):
    resp = client.post("/studies/ghost/delete", follow_redirects=True)
    assert b"Study not found" in resp.data


def test_delete_is_not_reachable_by_get(client, tmp_path):
    """A GET must never destroy anything -- crawlers, prefetchers and history all issue GETs.

    /studies/<study>/delete is POST-only, so on GET it falls through to run_detail with
    run_id="delete", which the run-id validator rejects.  Either way, nothing is removed.
    """
    _make_study(tmp_path, "alpha")
    resp = client.get("/studies/alpha/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Run directory not found" in resp.data
    assert studies.list_studies(tmp_path) == ["alpha"]


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
