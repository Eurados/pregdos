import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock
import pytest

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
    app.config["JOBS_FOLDER"] = str(tmp_path / "jobs")
    with app.test_client() as c:
        yield c


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


# --- Download path traversal ---

def test_download_nonexistent_file_redirects(client):
    # File does not exist → should redirect back to upload page
    response = client.get("/download/somestudy/topas_field1.txt", follow_redirects=True)
    assert response.status_code == 200
    assert b"form" in response.data.lower()


def test_download_traversal_stripped_by_secure_filename(client):
    # secure_filename strips ".." so traversal attempts are neutralised before path check
    from werkzeug.utils import secure_filename
    assert secure_filename("../../etc/passwd") == "etc_passwd"
    assert secure_filename("..") == ""


# --- extract_zip path traversal ---

def test_extract_zip_rejects_traversal(tmp_path):
    from pregdos.webserver import extract_zip

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    zip_path = source_dir / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../outside.txt", "malicious content")

    class FakeUpload:
        filename = "evil.zip"
        def save(self, path):
            import shutil
            shutil.copy(zip_path, path)

    with pytest.raises(Exception, match="Unsafe zip entry"):
        extract_zip(FakeUpload(), str(upload_dir))


# --- /submit route ---

def test_submit_rejects_path_outside_upload_folder(client, tmp_path):
    response = client.post(
        "/submit",
        data={"study_dir": "/etc", "study_name": "evil", "out_files": "topas_field1.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Invalid study path" in response.data


def test_submit_missing_file_flashes_error(client, tmp_path):
    study_dir = tmp_path / "mystudy"
    study_dir.mkdir()
    response = client.post(
        "/submit",
        data={"study_dir": str(study_dir), "study_name": "mystudy", "out_files": "topas_field1.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"not found" in response.data


def test_submit_calls_sbatch(client, tmp_path, mocker):
    study_dir = tmp_path / "mystudy"
    study_dir.mkdir()
    (study_dir / "topas_field1.txt").write_text("# topas input")

    mock_run = mocker.patch("pregdos.webserver.subprocess.run", return_value=FakeSbatchResult())
    response = client.post(
        "/submit",
        data={"study_dir": str(study_dir), "study_name": "mystudy", "out_files": "topas_field1.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"42" in response.data
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "sbatch" in cmd
    assert "topas_field1.txt" in cmd[-1]


def test_submit_sbatch_failure_flashes_error(client, tmp_path, mocker):
    study_dir = tmp_path / "mystudy"
    study_dir.mkdir()
    (study_dir / "topas_field1.txt").write_text("# topas input")

    failed = FakeSbatchResult()
    failed.returncode = 1
    failed.stdout = ""
    failed.stderr = "slurmctld not running"
    mocker.patch("pregdos.webserver.subprocess.run", return_value=failed)

    response = client.post(
        "/submit",
        data={"study_dir": str(study_dir), "study_name": "mystudy", "out_files": "topas_field1.txt"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"slurmctld not running" in response.data


# --- POST /convert input validation ---

def _convert_form(tmp_path, overrides=None):
    """Minimal valid /convert form data pointing at a real directory."""
    study = tmp_path / "study"
    study.mkdir()
    data = {
        "study_dir":        str(study),
        "beam_model_path":  str(tmp_path / "beam.csv"),
        "spr_table_path":   str(tmp_path / "spr.txt"),
        "nstat":            "1000000",
        "output_basename":  "topas",
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


# --- /convert scorer post-processing (issue #36) ---

# Minimal stand-in for a dicomexport-generated TOPAS file: a SCORER SET UP
# block holding only the in-field DoseToWater scorer, followed by a TIME
# FEATURES banner.  append_scorers() keys off both banners.
_FAKE_TOPAS = (
    '# fake dicomexport output\n'
    's:Ge/Patient/DicomDirectory = "study"\n'
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
    """subprocess.run side_effect mimicking dicomexport: write a fresh
    topas_field01.txt (in-field scorer only) to the output_base location,
    overwriting any existing file exactly as the real tool does."""
    output_base = cmd[-1]
    Path(f"{output_base}_field01.txt").write_text(_FAKE_TOPAS)
    result = MagicMock()
    result.returncode = 0
    result.stdout = "ok"
    result.stderr = ""
    return result


def test_convert_appends_selected_scorers_and_survives_rerun(client, tmp_path, mocker):
    """Selecting multiple quantities for one structure appends those scorer
    blocks to the generated file, and a re-run of the same study does not
    silently drop them (regression for issue #36)."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    mocker.patch("pregdos.webserver.subprocess.run", side_effect=_fake_dicomexport)

    form = {
        "study_dir": str(study_dir),
        "beam_model_path": str(tmp_path / "beam.csv"),
        "spr_table_path": str(tmp_path / "spr.txt"),
        "nstat": "1000000",
        "output_basename": "topas",
        "keep_infield": "1",
        "score_neutron": "CTV",
        "score_gamma": "CTV",
    }
    generated = study_dir / "topas_field01.txt"

    for run in ("first", "rerun"):
        resp = client.post("/convert", data=form, follow_redirects=True)
        assert resp.status_code == 200, run
        text = generated.read_text()
        # original in-field scorer preserved …
        assert "DoseToWater" in text, run
        # … plus both selected out-of-field scorers for the chosen structure
        assert "AmBDose_CTV" in text, run
        assert "DoseGamma_CTV" in text, run


# --- Models smoke tests ---

def test_conversion_parameters_defaults():
    p = ConversionParameters(
        study_dir="/tmp/study",
        beam_model_path="/tmp/beam.csv",
        spr_table_path="/tmp/spr.txt",
        output_base="/tmp/topas",
    )
    assert p.field_nr is None
    assert p.nstat is None


def test_conversion_result_fields():
    r = ConversionResult(
        out_files=["topas_field1.txt"],
        out_file_paths=["/tmp/topas_field1.txt"],
        study_name="mystudy",
    )
    assert r.out_files == ["topas_field1.txt"]
    assert r.out_file_paths == ["/tmp/topas_field1.txt"]
    assert r.selected_structures == []
    assert r.stdout is None


def test_structure_selection_defaults():
    s = StructureSelection(study_dir="/tmp/study")
    assert s.available_structures == []
    assert s.selected_structures == []
