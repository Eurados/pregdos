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

import zipfile
import os
from werkzeug.utils import secure_filename
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile

from . import executor, studies
from .models import ConversionParameters, ConversionResult
from .studies import StudyError
from .topas_scorer import SCORER_DEFS, append_scorers, scorer_config_from_form


# The studies root: one directory per uploaded study.  Still called UPLOAD_FOLDER for
# backwards compatibility with existing deployments and the Docker entrypoint.
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(tempfile.gettempdir(), "pregdos_uploads")
ALLOWED_EXTENSIONS = {"dcm", "csv", "txt"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = os.environ.get("PREGDOS_SECRET_KEY", "pregdos_secret_key")


def studies_root() -> str:
    """The configured studies root.  Read through app.config so tests can override it."""
    return app.config["UPLOAD_FOLDER"]


def ensure_studies_root() -> str | None:
    """Return an error string if the studies root can't be used, else None."""
    return studies.ensure_root(studies_root())


# ---------------------------------------------------------------------------
# Bundled beam models and SPR tables
# ---------------------------------------------------------------------------

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


def _builtin_beam_models() -> list[dict]:
    """Return metadata for beam model CSVs bundled with the package."""
    bm_dir = importlib.resources.files("pregdos") / "data" / "beam_models"
    models = []
    for entry in bm_dir.iterdir():
        if entry.name.endswith(".csv"):
            models.append({"name": entry.name, "label": entry.name})
    models.sort(key=lambda m: m["name"], reverse=True)
    return models


def _copy_builtin(kind: str, filename: str, dest_dir: Path) -> str:
    """Copy a bundled beam model / SPR table into a study dir.  Return its basename."""
    safe = secure_filename(filename)
    src = importlib.resources.files("pregdos") / "data" / kind / safe
    if not src.is_file():
        raise FileNotFoundError(f"Unknown built-in {kind} file: {filename}")
    (dest_dir / safe).write_bytes(src.read_bytes())
    return safe


# ---------------------------------------------------------------------------
# Upload handling
# ---------------------------------------------------------------------------

def save_single_file(upload, folder) -> str:
    """Save one uploaded file into ``folder``.  Return its basename."""
    name = secure_filename(upload.filename)
    upload.save(os.path.join(folder, name))
    return name


def extract_zip(study_zip, dest_dir):
    """Extract an uploaded ZIP into ``dest_dir``, rejecting entries that escape it.

    The ZIP itself is written to a scratch file next to ``dest_dir`` and removed again;
    only its contents are kept, so no stray archive is left inside the study.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    scratch = dest.parent / (secure_filename(study_zip.filename) + ".part")
    study_zip.save(str(scratch))
    try:
        with zipfile.ZipFile(scratch, "r") as zf:
            for member in zf.namelist():
                member_path = os.path.abspath(os.path.join(dest, member))
                if not member_path.startswith(os.path.abspath(dest) + os.sep):
                    raise Exception(f"Unsafe zip entry detected: {member}")
                if member.endswith("/"):
                    os.makedirs(member_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(member_path), exist_ok=True)
                    with zf.open(member) as source, open(member_path, "wb") as target:
                        shutil.copyfileobj(source, target)
    finally:
        scratch.unlink(missing_ok=True)
    return str(dest)


def save_uploaded_directory(files, dest_dir):
    """Save a browser directory upload into ``dest_dir``, preserving relative structure."""
    if not files:
        raise ValueError("Empty directory upload")
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for file in files:
        parts = [secure_filename(p) for p in file.filename.split("/") if p]
        if not parts:
            continue
        out_path = dest.joinpath(*parts)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        file.save(str(out_path))
    return str(dest)


def _upload_study_name(study_zip, study_dir_files) -> str:
    """Derive a human-readable study name from whichever upload form was used.

    Study names are shown in the UI and appear in URLs, so we keep the name the user
    already recognises (the ZIP stem or the dropped folder's name) rather than a UID.
    """
    if study_zip and study_zip.filename:
        return Path(study_zip.filename).stem
    if study_dir_files:
        return study_dir_files[0].filename.split("/")[0]
    raise ValueError("No study upload provided")


def get_structures(root, study_name):
    """ROI names in the study's RTSTRUCT, or [] if there is none."""
    rs_path = studies.find_rtstruct(root, study_name)
    if rs_path is None:
        return []
    ds = pydicom.dcmread(str(rs_path))
    return [roi.ROIName for roi in ds.StructureSetROISequence]


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


@app.context_processor
def inject_pregdos_version():
    try:
        version = importlib.metadata.version("pregdos")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    return {"pregdos_version": version}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/upload", methods=["GET", "POST"])
def upload_files():
    folder_err = ensure_studies_root()
    if folder_err:
        flash(folder_err)
        return render_template("upload.html",
                               builtin_beam_models=_builtin_beam_models(),
                               builtin_spr_tables=_builtin_spr_tables()), 500

    if request.method == "POST":
        study_zip = request.files.get("study_zip")
        study_dir_files = [f for f in (request.files.getlist("study_dir") or []) if f and f.filename]

        # Beam model / SPR table: either a bundled file or an upload
        bm_source = request.form.get("beam_model_source", "upload")
        beam_model = request.files.get("beam_model")
        spr_source = request.form.get("spr_table_source", "upload")
        spr_table = request.files.get("spr_table")

        # Validate input before creating anything on disk
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

        root = studies_root()
        try:
            study_name, study_path = studies.create_study(root, _upload_study_name(study_zip, study_dir_files))
        except (StudyError, ValueError) as e:
            flash(str(e))
            return redirect(request.url)

        # Everything below writes into the new study dir.  If any step fails we remove it
        # again, so a failed upload never leaves a half-populated study behind.
        try:
            dicom_dir = study_path / studies.DICOM_SUBDIR
            if study_zip and study_zip.filename:
                extract_zip(study_zip, dicom_dir)
            else:
                save_uploaded_directory(study_dir_files, dicom_dir)

            # Copy beam model and SPR table into the study so it is self-contained:
            # deleting the study removes every input it depends on, and the generated
            # TOPAS file can reference the SPR table by a relative path.
            if bm_source == "upload":
                beam_model_name = save_single_file(beam_model, study_path)
            else:
                beam_model_name = _copy_builtin("beam_models", bm_source, study_path)
            if spr_source == "upload":
                spr_table_name = save_single_file(spr_table, study_path)
            else:
                spr_table_name = _copy_builtin("spr_tables", spr_source, study_path)

            structures = get_structures(root, study_name)
            if not structures:
                raise ValueError("No RS-file or structures found!")
        except Exception as e:
            shutil.rmtree(study_path, ignore_errors=True)
            flash(str(e) if str(e) else "Upload failed.")
            return redirect(request.url)

        # Render the combined setup page (structure inclusion + scorer selection)
        return render_template(
            "setup.html",
            structures=structures,
            study_name=study_name,
            beam_model_name=beam_model_name,
            spr_table_name=spr_table_name,
            scorer_defs=SCORER_DEFS,
        )
    return render_template(
        "upload.html",
        builtin_beam_models=_builtin_beam_models(),
        builtin_spr_tables=_builtin_spr_tables(),
    )


def run_conversion(params: ConversionParameters, selected_structures: list) -> ConversionResult:
    """Run dicomexport inside the run directory and collect the TOPAS files it wrote.

    dicomexport is invoked with ``cwd=params.run_dir`` and relative arguments, so the
    ``DicomDirectory`` and ``includeFile`` paths it bakes into the generated TOPAS input
    are relative and already correct for a TOPAS run started from that same directory.
    Nothing needs to be rewritten afterwards, and the study directory stays movable.

    Because the run directory was created empty moments ago, its ``*_field*.txt`` files
    are exactly this conversion's output -- no cross-directory search, no deduplication,
    and no way for a previous run's files to leak in (issue #41).
    """
    cmd = _dicomexport_cmd_prefix() + ["-b", params.beam_model_rel, "-s", params.spr_table_rel]
    if params.field_nr is not None:
        cmd += ["-f", str(params.field_nr)]
    if params.nstat is not None:
        cmd += ["-N", str(params.nstat)]
    cmd += [params.dicom_rel, params.output_basename]

    try:
        proc = subprocess.run(
            cmd, check=True, cwd=params.run_dir, env=os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except subprocess.CalledProcessError as e:
        out = (e.stdout or "").strip()
        err = (e.stderr or str(e)).strip()
        msg = "".join([part for part in (err, out) if part])
        raise RuntimeError(f"Error running dicomexport: {msg}") from e

    out_files = sorted(p.name for p in Path(params.run_dir).glob(f"{params.output_basename}_field*.txt"))
    if not out_files:
        raise RuntimeError("No output files generated by dicomexport.")

    return ConversionResult(
        out_files=out_files,
        study_name=params.study_name,
        run_id=Path(params.run_dir).name,
        selected_structures=list(selected_structures),
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


@app.route("/convert", methods=["POST"])
def convert():
    root = studies_root()
    study_name = request.form["study_name"]
    beam_model_name = secure_filename(request.form["beam_model_name"])
    spr_table_name = secure_filename(request.form["spr_table_name"])

    # Any structure with at least one scorer checked is scored.  The matrix selection is
    # the selection mechanism: each checked cell becomes a scorer block carrying
    # OnlyIncludeIfInRTStructure, so TOPAS computes only the structures chosen here.
    selected_structures = sorted({
        s
        for sc_def in SCORER_DEFS
        for s in request.form.getlist(f'score_{sc_def["id"]}')
        if s
    })

    nstat_val = request.form.get("nstat", "1000000")
    try:
        nstat = int(request.form.get("nstat_custom", "").strip()) if nstat_val == "custom" else int(nstat_val)
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

    try:
        study_path = studies.study_path(root, study_name)
        run_id, run_dir = studies.create_run(root, study_name)
    except StudyError as e:
        flash(str(e))
        return redirect(url_for("upload_files"))

    params = ConversionParameters(
        study_name=study_name,
        run_dir=str(run_dir),
        dicom_rel=studies.relative_to_run(studies.dicom_path(root, study_name), run_dir),
        beam_model_rel=studies.relative_to_run(study_path / beam_model_name, run_dir),
        spr_table_rel=studies.relative_to_run(study_path / spr_table_name, run_dir),
        output_basename=output_basename,
        field_nr=None,
        nstat=nstat,
    )

    try:
        result = run_conversion(params, selected_structures)
    except RuntimeError as err:
        # A failed conversion leaves nothing behind: the run dir is empty or partial.
        shutil.rmtree(run_dir, ignore_errors=True)
        flash(str(err))
        return redirect(url_for("upload_files"))

    # Inject the requested out-of-field scorer blocks, and optionally drop the in-field
    # DoseToWater scorer that dicomexport always writes.
    scorer_config = scorer_config_from_form(request.form)
    if scorer_config.scorers or not scorer_config.keep_infield:
        failures = []
        for fname in result.out_files:
            try:
                append_scorers(str(run_dir / fname), scorer_config)
            except Exception as err:
                failures.append((fname, err))
        if failures:
            # The user asked for scorers that could not be written.  Do not present the
            # conversion as successful: surface a visible error naming each affected file
            # and send the user back to try again (#36).
            shutil.rmtree(run_dir, ignore_errors=True)
            for name, err in failures:
                flash(f"Scorer post-processing failed for {name}: {err}")
            return redirect(url_for("upload_files"))

    return render_template(
        "convert_success.html",
        out_files=result.out_files,
        study_name=result.study_name,
        run_id=result.run_id,
        selected_structures=result.selected_structures,
    )


@app.route("/download/<study>/<run_id>/<filename>")
def download_file(study, run_id, filename):
    """Download one generated TOPAS input file from a run directory."""
    try:
        run_dir = studies.run_path(studies_root(), study, run_id)
    except StudyError:
        flash("Invalid study path.")
        return redirect(url_for("upload_files"))
    safe_filename = secure_filename(filename)
    if not (run_dir / safe_filename).is_file():
        flash("File not found.")
        return redirect(url_for("upload_files"))
    return send_from_directory(str(run_dir), safe_filename, as_attachment=True)


@app.route("/squeue")
def squeue():
    result = subprocess.run(["squeue"], capture_output=True, text=True)
    return result.stdout or result.stderr


@app.route("/submit", methods=["POST"])
def submit_job():
    """Execute each generated TOPAS file, in place, in the run directory.

    The files are *not* copied anywhere.  Their ``DicomDirectory`` and ``includeFile``
    entries are relative to the run directory they were generated in, so TOPAS must run
    with that directory as its working directory (see :mod:`pregdos.executor`).
    """
    root = studies_root()
    study_name = request.form["study_name"]
    run_id = request.form["run_id"]
    out_files = request.form.getlist("out_files")

    try:
        run_dir = studies.run_path(root, study_name, run_id)
    except StudyError:
        flash("Invalid study path.")
        return redirect(url_for("upload_files"))
    if not run_dir.is_dir():
        flash("Run directory not found.")
        return redirect(url_for("upload_files"))

    # Validate every requested file up front, so a typo cannot start a partial run.
    topas_files = []
    missing = []
    for fname in out_files:
        safe_fname = secure_filename(fname)
        if (run_dir / safe_fname).is_file():
            topas_files.append(safe_fname)
        else:
            missing.append(safe_fname)
    if missing or not topas_files:
        for name in missing:
            flash(f"Error: File not found: {name}")
        if not topas_files:
            flash("Error: Nothing to submit.")
        return redirect(url_for("upload_files"))

    # Under SLURM the job runs as the `slurm` user, which must own the directory it writes
    # logs and scorer CSVs into.  Harmless (and skipped) elsewhere.
    if executor.select_backend() == executor.SLURM:
        try:
            shutil.chown(run_dir, user="slurm", group="slurm")
        except (LookupError, PermissionError, OSError):
            pass  # slurm user not present outside the container

    info = executor.submit_run(run_dir, topas_files)

    if info.backend == executor.SLURM:
        for job in info.fields:
            flash(f"Submitted {job.topas_file} → SLURM job {job.ident}")
    elif info.fields:
        flash(
            f"Running {len(info.fields)} field(s) locally in the background "
            f"(no SLURM found; pid {info.fields[0].ident}). Progress appears in the run directory."
        )
    for e in info.errors:
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
    """List every conversion run across all studies.

    Superseded by the per-study results view in #32; kept simple here so the app stays
    coherent after runs moved from a separate jobs tree into the study directories.
    """
    root = studies_root()
    jobs = []
    for study in studies.list_studies(root):
        for run_id in studies.list_runs(root, study):
            run_dir = studies.run_path(root, study, run_id)
            submitted = executor.read_run_metadata(run_dir) is not None
            jobs.append({
                "study": study,
                "run_id": run_id,
                "file_count": sum(1 for p in run_dir.iterdir() if p.is_file()),
                # Status is read from the exit-code sentinels on disk, so it stays correct
                # across a webserver restart.  A run that was never submitted has none.
                "status": executor.run_status(run_dir) if submitted else "not submitted",
            })
    jobs.sort(key=lambda j: (j["run_id"], j["study"]), reverse=True)
    return render_template("jobs.html", jobs=jobs)


@app.route("/jobs/<study>/<run_id>")
def job_files(study, run_id):
    try:
        run_dir = studies.run_path(studies_root(), study, run_id)
    except StudyError:
        flash("Invalid job directory.")
        return redirect(url_for("list_jobs"))
    if not run_dir.is_dir():
        flash("Job directory not found.")
        return redirect(url_for("list_jobs"))
    files = [
        {"name": p.name, "size": p.stat().st_size}
        for p in sorted(run_dir.iterdir()) if p.is_file()
    ]
    return render_template("job_files.html", study=study, run_id=run_id, files=files)


@app.route("/jobs/download/<study>/<run_id>/<filename>")
def download_job_file(study, run_id, filename):
    try:
        run_dir = studies.run_path(studies_root(), study, run_id)
    except StudyError:
        flash("Invalid job directory.")
        return redirect(url_for("list_jobs"))
    return send_from_directory(str(run_dir), secure_filename(filename), as_attachment=True)


def main():
    app.run(debug=True, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
