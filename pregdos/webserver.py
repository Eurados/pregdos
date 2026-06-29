from flask import (
    Flask,
    request,
    render_template,
    send_from_directory,
    redirect,
    flash,
    url_for,
)
import importlib.metadata
import importlib.resources
import pydicom
import glob

import zipfile
import os
from werkzeug.utils import secure_filename
from pathlib import Path
import datetime
import subprocess
import sys
import shutil
import tempfile
import copy
from typing import List

from .models import ConversionParameters, ConversionResult
from .postprocess import post_process_job
from .topas_scorer import SCORER_DEFS, append_scorers, scorer_config_from_form


def _builtin_spr_tables() -> list[dict]:
    """Return metadata for SPR tables bundled with the package.

    Each entry has ``name`` (filename) and ``label`` (display name for the UI).
    Files live in ``pregdos/data/spr_tables/`` and are included as package data.
    """
    spr_dir = importlib.resources.files("pregdos") / "data" / "spr_tables"
    tables = []
    for entry in spr_dir.iterdir():
        if entry.name.endswith((".txt", ".csv")):
            tables.append({"name": entry.name, "label": entry.name})
    tables.sort(key=lambda t: t["name"])
    return tables


def _builtin_spr_path(filename: str) -> str:
    """Copy a bundled SPR table to the upload folder and return its path."""
    safe = secure_filename(filename)
    src = importlib.resources.files("pregdos") / "data" / "spr_tables" / safe
    if not src.is_file():
        raise FileNotFoundError(f"Unknown built-in SPR table: {filename}")
    dest = os.path.join(app.config["UPLOAD_FOLDER"], safe)
    Path(dest).write_bytes(src.read_bytes())
    return dest


def _builtin_beam_models() -> list[dict]:
    """Return metadata for beam model CSVs bundled with the package."""
    bm_dir = importlib.resources.files("pregdos") / "data" / "beam_models"
    models = []
    for entry in bm_dir.iterdir():
        if entry.name.endswith(".csv"):
            models.append({"name": entry.name, "label": entry.name})
    models.sort(key=lambda m: m["name"], reverse=True)
    return models


def _builtin_beam_model_path(filename: str) -> str:
    """Copy a bundled beam model CSV to the upload folder and return its path."""
    safe = secure_filename(filename)
    src = importlib.resources.files("pregdos") / "data" / "beam_models" / safe
    if not src.is_file():
        raise FileNotFoundError(f"Unknown built-in beam model: {filename}")
    dest = os.path.join(app.config["UPLOAD_FOLDER"], safe)
    Path(dest).write_bytes(src.read_bytes())
    return dest

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(tempfile.gettempdir(), "pregdos_uploads")
JOBS_FOLDER = os.environ.get("JOBS_FOLDER", "/home/slurm/jobs")
TOPAS_BIN = os.environ.get("TOPAS_BIN", "topas")
ALLOWED_EXTENSIONS = {"dcm", "csv", "txt"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["JOBS_FOLDER"] = JOBS_FOLDER
app.secret_key = os.environ.get("PREGDOS_SECRET_KEY", "pregdos_secret_key")

def ensure_upload_folder() -> str | None:
    """Return an error string if the configured upload folder can't be used, else None."""
    path = app.config["UPLOAD_FOLDER"]
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        return f"Cannot create upload folder {path!r}: {e}"
    if not os.path.isdir(path):
        return f"Upload folder path {path!r} exists but is not a directory."
    if not os.access(path, os.W_OK | os.X_OK):
        return f"Upload folder {path!r} is not writable."
    return None


@app.context_processor
def inject_pregdos_version():
    try:
        version = importlib.metadata.version("pregdos")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    return {"pregdos_version": version}


def save_single_file(upload, folder):
    path = os.path.join(folder, secure_filename(upload.filename))
    upload.save(path)
    return path


def extract_zip(study_zip, folder):
    zip_path = save_single_file(study_zip, folder)
    study_dir = os.path.join(folder, Path(study_zip.filename).stem)
    os.makedirs(study_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            member_path = os.path.abspath(os.path.join(study_dir, member))
            if not member_path.startswith(os.path.abspath(study_dir) + os.sep):
                raise Exception(f"Unsafe zip entry detected: {member}")
            if member.endswith("/"):
                os.makedirs(member_path, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(member_path), exist_ok=True)
                with zf.open(member) as source, open(member_path, "wb") as target:
                    shutil.copyfileobj(source, target)
    return study_dir


def save_uploaded_directory(files, base_folder):
    if not files:
        raise ValueError("Empty directory upload")
    # Detect root folder from first file path; browsers include the folder name
    first = files[0].filename
    root = secure_filename(first.split("/")[0]) or "study_upload"
    study_dir = os.path.join(base_folder, root)
    for file in files:
        rel_path = file.filename
        parts = [secure_filename(p) for p in rel_path.split("/") if p]
        # drop first part (root folder)
        if parts and parts[0] == root:
            parts = parts[1:]
        out_path = (
            os.path.join(study_dir, *parts) if parts else os.path.join(study_dir, secure_filename(Path(file.filename).name))
        )
        dir_path = os.path.dirname(out_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        file.save(out_path)
    return study_dir


def get_structures(study_dir):
    rs_files = glob.glob(os.path.join(study_dir, "RS*.dcm"))
    if not rs_files:
        return []
    ds = pydicom.dcmread(rs_files[0])
    return [roi.ROIName for roi in ds.StructureSetROISequence]


def filter_rtstruct_keep_rois(orig_study_dir, selected_rois):
    """Copy orig_study_dir to a temp dir and rewrite the RTSTRUCT to keep only selected_rois.

    Returns the path to the filtered study dir (a copy).
    """
    # make a temp dir sibling to original
    parent = Path(orig_study_dir).parent
    tmpdir = tempfile.mkdtemp(prefix=Path(orig_study_dir).name + "_filtered_", dir=str(parent))
    # Copy only the DICOM inputs into the filtered dir, excluding generated
    # TOPAS files (*.txt).  On a re-run the original study dir may already
    # contain topas_field*.txt from a previous conversion; copying those into
    # the filtered dir would let them shadow the freshly generated output during
    # file discovery in run_conversion(), so the post-processed scorers would be
    # written to a throwaway copy while submit/download keep the stale file (#36).
    shutil.copytree(
        orig_study_dir, tmpdir, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.txt")
    )

    # find RTST in copy
    rs_files = glob.glob(os.path.join(tmpdir, "RS*.dcm"))
    if not rs_files:
        return tmpdir
    rs_path = rs_files[0]
    ds = pydicom.dcmread(rs_path)

    # map ROIName -> ROINumber
    name_to_number = {}
    if hasattr(ds, "StructureSetROISequence"):
        for roi in ds.StructureSetROISequence:
            name = getattr(roi, "ROIName", None)
            number = getattr(roi, "ROINumber", None)
            if name is not None and number is not None:
                name_to_number[str(name)] = int(number)

    keep_numbers = set()
    for sel in selected_rois:
        if sel in name_to_number:
            keep_numbers.add(name_to_number[sel])

    # If nothing matched, keep everything
    if not keep_numbers:
        return tmpdir

    new_ds = copy.deepcopy(ds)

    def filter_seq(seq, attr_name):
        if not hasattr(seq, "__iter__"):
            return seq
        out = []
        for item in seq:
            val = getattr(item, attr_name, None)
            if val in keep_numbers:
                out.append(item)
        return out

    # StructureSetROISequence: keep by ROINumber
    if hasattr(new_ds, "StructureSetROISequence"):
        new_ds.StructureSetROISequence = [
            item for item in new_ds.StructureSetROISequence if getattr(item, "ROINumber", None) in keep_numbers
        ]

    # ROIContourSequence: keep by ReferencedROINumber
    if hasattr(new_ds, "ROIContourSequence"):
        new_ds.ROIContourSequence = [
            item for item in new_ds.ROIContourSequence if getattr(item, "ReferencedROINumber", None) in keep_numbers
        ]

    # RTROIObservationsSequence: keep by ReferencedROINumber
    if hasattr(new_ds, "RTROIObservationsSequence"):
        new_ds.RTROIObservationsSequence = [
            item for item in new_ds.RTROIObservationsSequence if getattr(item, "ReferencedROINumber", None) in keep_numbers
        ]

    # write modified RTSTRUCT back to file
    try:
        new_ds.save_as(rs_path)
    except Exception:
        # if saving fails, return the unmodified copy
        return tmpdir

    return tmpdir


def _dicomexport_cmd_prefix():
    """Return command prefix to invoke dicomexport.

    Prefer the console script installed alongside the current Python executable
    (e.g., venv/bin/dicomexport). Fall back to `python -m dicomexport.main`.
    """
    py_bin = os.path.dirname(sys.executable)
    console = os.path.join(py_bin, "dicomexport")
    if os.path.exists(console) and os.access(console, os.X_OK):
        return [console]
    # fallback
    return [sys.executable, "-m", "dicomexport.main"]


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/upload", methods=["GET", "POST"])
def upload_files():
    folder_err = ensure_upload_folder()
    if folder_err:
        flash(folder_err)
        return render_template("upload.html",
                               builtin_beam_models=_builtin_beam_models(),
                               builtin_spr_tables=_builtin_spr_tables()), 500

    if request.method == "POST":
        study_zip = request.files.get("study_zip")
        study_dir_files = [f for f in (request.files.getlist("study_dir") or []) if f and f.filename]

        # Beam model: either a bundled file or an upload
        bm_source = request.form.get("beam_model_source", "upload")
        beam_model = request.files.get("beam_model")

        # SPR table: either a bundled file or an upload
        spr_source = request.form.get("spr_table_source", "upload")
        spr_table = request.files.get("spr_table")

        # Validate input
        if bm_source == "upload" and not (beam_model and beam_model.filename):
            flash("Beam model required — choose a built-in model or upload one.")
            return redirect(request.url)
        if spr_source == "upload" and not (spr_table and spr_table.filename):
            flash("SPR table required — choose a built-in table or upload one.")
            return redirect(request.url)
        if not study_zip and not study_dir_files:
            flash("Provide either a ZIP or a folder.")
            return redirect(request.url)

        if (study_zip and study_zip.filename) and study_dir_files:
            flash("Please choose either ZIP or Folder, not both.")
            return redirect(request.url)

        upload_folder = app.config["UPLOAD_FOLDER"]

        if bm_source == "upload":
            beam_model_path = save_single_file(beam_model, upload_folder)
        else:
            try:
                beam_model_path = _builtin_beam_model_path(secure_filename(bm_source))
            except Exception:
                flash(f"Bundled beam model not found: {bm_source}")
                return redirect(request.url)
        if spr_source == "upload":
            spr_table_path = save_single_file(spr_table, upload_folder)
        else:
            try:
                spr_table_path = _builtin_spr_path(secure_filename(spr_source))
            except Exception:
                flash(f"Bundled SPR table not found: {spr_source}")
                return redirect(request.url)

        if study_zip and study_zip.filename:
            study_dir = extract_zip(study_zip, upload_folder)
        else:
            try:
                study_dir = save_uploaded_directory(study_dir_files, upload_folder)
            except ValueError as e:
                flash(str(e))
                return redirect(request.url)

        # Udtræk strukturer fra RS-fil
        structures = get_structures(study_dir)
        if not structures:
            flash("No RS-file or structures found!")
            return redirect(request.url)
        # Render the combined setup page (structure inclusion + scorer selection)
        return render_template(
            "setup.html",
            structures=structures,
            study_dir=study_dir,
            beam_model_path=beam_model_path,
            spr_table_path=spr_table_path,
            scorer_defs=SCORER_DEFS,
        )
    return render_template(
        "upload.html",
        builtin_beam_models=_builtin_beam_models(),
        builtin_spr_tables=_builtin_spr_tables(),
    )


def run_conversion(params: ConversionParameters, selected_structures: List[str]) -> ConversionResult:
    """Filter RTSTRUCT and run dicomexport, returning discovered TOPAS files.

    Searches multiple directories for output to handle different output_base placements.
    """
    filtered_dir = filter_rtstruct_keep_rois(params.study_dir, selected_structures)
    study_to_use = filtered_dir
    output_base = params.output_base

    cmd_prefix = _dicomexport_cmd_prefix()
    cmd = cmd_prefix + ["-b", params.beam_model_path, "-s", params.spr_table_path]
    if params.field_nr is not None:
        cmd += ["-f", str(params.field_nr)]
    if params.nstat is not None:
        cmd += ["-N", str(params.nstat)]
    cmd += [study_to_use, output_base]
    env = os.environ.copy()
    try:
        proc = subprocess.run(cmd, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        out = (e.stdout or "").strip()
        err = (e.stderr or str(e)).strip()
        msg = "".join([part for part in (err, out) if part])
        raise RuntimeError(f"Error running dicomexport: {msg}") from e

    # dicomexport may write output files into the study dir, the filtered copy,
    # or the parent of output_base depending on the version.  Search all three
    # locations and deduplicate by filename to be robust across versions.
    output_stem = Path(output_base).name
    search_dirs = [Path(study_to_use), Path(params.study_dir), Path(output_base).parent]
    found_paths: dict[str, str] = {}  # basename → absolute path (first-seen wins)
    for d in search_dirs:
        if not d.exists():
            continue
        for f in os.listdir(d):
            if f.startswith(output_stem) and f.endswith(".txt") and f not in found_paths:
                found_paths[f] = str(d / f)
    if not found_paths:
        raise RuntimeError("No output files generated by dicomexport.")

    sorted_names = sorted(found_paths.keys())
    return ConversionResult(
        out_files=sorted_names,
        out_file_paths=[found_paths[n] for n in sorted_names],
        study_name=Path(params.study_dir).name,
        selected_structures=list(selected_structures),
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


@app.route("/convert", methods=["POST"])
def convert():
    study_dir = request.form["study_dir"]
    beam_model_path = request.form["beam_model_path"]
    spr_table_path = request.form["spr_table_path"]
    # Any structure with at least one scorer checked is included in the RTSTRUCT filter
    selected_structures = sorted({
        s
        for sc_def in SCORER_DEFS
        for s in request.form.getlist(f'score_{sc_def["id"]}')
        if s
    })
    nstat_val = request.form.get("nstat", "1000000")
    try:
        if nstat_val == "custom":
            nstat = int(request.form.get("nstat_custom", "").strip())
        else:
            nstat = int(nstat_val)
        if nstat < 1:
            raise ValueError
    except ValueError:
        flash("Invalid number of primaries — must be a positive integer.")
        return redirect(url_for("upload_files"))

    raw_basename = (request.form.get("output_basename") or "topas").strip()
    output_basename = secure_filename(raw_basename)
    if not output_basename or output_basename != raw_basename or "." in output_basename:
        flash("Invalid output basename — use letters, digits, underscores, and hyphens only.")
        return redirect(url_for("upload_files"))
    params = ConversionParameters(
        study_dir=study_dir,
        beam_model_path=beam_model_path,
        spr_table_path=spr_table_path,
        output_base=os.path.join(study_dir, output_basename),
        field_nr=None,
        nstat=nstat,
    )
    try:
        result = run_conversion(params, selected_structures)
    except RuntimeError as err:
        flash(str(err))
        return redirect(url_for("upload_files"))

    # Parse the scorer choices the user made on the setup page.
    # append_scorers() modifies each TOPAS file in-place: it injects the
    # requested out-of-field scorer blocks and optionally removes the
    # DoseToWater scorer that dicomexport always writes.
    scorer_config = scorer_config_from_form(request.form)
    if scorer_config.scorers or not scorer_config.keep_infield:
        for fpath in result.out_file_paths:
            try:
                append_scorers(fpath, scorer_config)
            except Exception as err:
                # Non-fatal: the original file is still usable; alert the user
                flash(f"Warning: scorer post-processing failed for {os.path.basename(fpath)}: {err}")

    return render_template(
        "convert_success.html",
        out_files=result.out_files,
        study_name=result.study_name,
        study_dir=params.study_dir,
        selected_structures=result.selected_structures,
    )


@app.route("/download/<study>/<filename>")
def download_file(study, filename):
    safe_study = secure_filename(study)
    safe_filename = secure_filename(filename)
    dir_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_study)
    # Ensure the resolved path is within the upload folder
    abs_dir_path = os.path.abspath(dir_path)
    abs_upload_folder = os.path.abspath(app.config["UPLOAD_FOLDER"])
    if not abs_dir_path.startswith(abs_upload_folder + os.sep):
        flash("Invalid study path.")
        return redirect(url_for("upload_files"))
    file_path = os.path.join(abs_dir_path, safe_filename)
    if not os.path.isfile(file_path):
        flash("File not found.")
        return redirect(url_for("upload_files"))
    return send_from_directory(abs_dir_path, safe_filename, as_attachment=True)


@app.route("/squeue")
def squeue():
    result = subprocess.run(["squeue"], capture_output=True, text=True)
    return result.stdout or result.stderr


@app.route("/submit", methods=["POST"])
def submit_job():
    study_dir = request.form["study_dir"]
    study_name = request.form["study_name"]
    out_files = request.form.getlist("out_files")

    # Validate study_dir is within UPLOAD_FOLDER
    abs_study_dir = os.path.abspath(study_dir)
    abs_upload_folder = os.path.abspath(app.config["UPLOAD_FOLDER"])
    if not abs_study_dir.startswith(abs_upload_folder + os.sep):
        flash("Invalid study path.")
        return redirect(url_for("upload_files"))

    # Create timestamped job working directory under JOBS_FOLDER
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = os.path.join(app.config["JOBS_FOLDER"], f"{secure_filename(study_name)}_{timestamp}")
    os.makedirs(job_dir, exist_ok=True)
    try:
        shutil.chown(job_dir, user="slurm", group="slurm")
    except LookupError:
        pass  # slurm user not present outside container

    # Find each TOPAS file (search study_dir recursively) and copy to job_dir
    job_ids = []
    errors = []
    for fname in out_files:
        safe_fname = secure_filename(fname)
        src = None
        for root, _dirs, files in os.walk(abs_study_dir):
            if safe_fname in files:
                candidate = os.path.join(root, safe_fname)
                # Don't pick up files already inside a job_ subdirectory
                if os.sep + "job_" not in candidate[len(abs_study_dir):]:
                    src = candidate
                    break
        if src is None:
            errors.append(f"File not found: {safe_fname}")
            continue
        shutil.copy2(src, os.path.join(job_dir, safe_fname))

        ncpu = os.cpu_count() or 1
        result = subprocess.run(
            [
                "runuser", "-u", "slurm", "--",
                "sbatch",
                "--export=ALL",
                f"--cpus-per-task={ncpu}",
                f"--chdir={job_dir}",
                f"--output={job_dir}/slurm-%j.out",
                "--wrap", f"{TOPAS_BIN} {safe_fname}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            job_id = result.stdout.strip().split()[-1]
            job_ids.append((safe_fname, job_id))
        else:
            errors.append(f"sbatch failed for {safe_fname}: {result.stderr.strip()}")

    post_process_job(job_dir)

    for fname, jid in job_ids:
        flash(f"Submitted {fname} → SLURM job {jid}")
    for e in errors:
        flash(f"Error: {e}")
    return redirect(url_for("list_jobs"))


@app.route("/about")
def about():
    def pkg_version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    def explicit_version(env_name, marker_name):
        env_value = (os.environ.get(env_name) or "").strip()
        if env_value:
            return env_value
        marker = Path("/etc/pregdos") / marker_name
        try:
            if marker.is_file():
                marker_value = marker.read_text(encoding="utf-8").strip()
                if marker_value:
                    return marker_value
        except OSError:
            pass
        return "unknown"

    versions = {
        "pregdos": pkg_version("pregdos"),
        "dicomexport": pkg_version("dicomexport"),
        "topas": explicit_version("TOPAS_VERSION", "TOPAS_VERSION"),
        "geant4": explicit_version("GEANT4_VERSION", "GEANT4_VERSION"),
    }
    return render_template("about.html", versions=versions)


@app.route("/jobs")
def list_jobs():
    jobs_folder = app.config["JOBS_FOLDER"]
    jobs = []
    if os.path.isdir(jobs_folder):
        for name in sorted(os.listdir(jobs_folder), reverse=True):
            job_path = os.path.join(jobs_folder, name)
            if os.path.isdir(job_path):
                file_count = len(os.listdir(job_path))
                jobs.append({"name": name, "file_count": file_count})
    return render_template("jobs.html", jobs=jobs)


@app.route("/jobs/<job_dir_name>")
def job_files(job_dir_name):
    jobs_folder = app.config["JOBS_FOLDER"]
    safe_name = secure_filename(job_dir_name)
    abs_job_path = os.path.abspath(os.path.join(jobs_folder, safe_name))
    abs_jobs_folder = os.path.abspath(jobs_folder)
    if not abs_job_path.startswith(abs_jobs_folder + os.sep):
        flash("Invalid job directory.")
        return redirect("/jobs")
    if not os.path.isdir(abs_job_path):
        flash("Job directory not found.")
        return redirect("/jobs")
    files = []
    for fname in sorted(os.listdir(abs_job_path)):
        fpath = os.path.join(abs_job_path, fname)
        if os.path.isfile(fpath):
            files.append({"name": fname, "size": os.path.getsize(fpath)})
    return render_template("job_files.html", job_dir_name=safe_name, files=files)


@app.route("/jobs/download/<job_dir_name>/<filename>")
def download_job_file(job_dir_name, filename):
    jobs_folder = app.config["JOBS_FOLDER"]
    safe_name = secure_filename(job_dir_name)
    safe_filename = secure_filename(filename)
    abs_job_path = os.path.abspath(os.path.join(jobs_folder, safe_name))
    abs_jobs_folder = os.path.abspath(jobs_folder)
    if not abs_job_path.startswith(abs_jobs_folder + os.sep):
        flash("Invalid job directory.")
        return redirect("/jobs")
    return send_from_directory(abs_job_path, safe_filename, as_attachment=True)


def main():
    app.run(debug=True, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
