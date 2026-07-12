from flask import (
    Flask,
    Response,
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

import csv
import datetime
import io
import math
import zipfile
import os
import secrets
from werkzeug.utils import secure_filename
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile

from . import dicom_intake, executor, results, structure_metrics, studies, versions
from .models import ConversionParameters, ConversionResult
from .studies import StudyError
from .topas_scorer import SCORER_DEFS, append_scorers, scorer_config_from_form


# The studies root: one directory per uploaded study.  Still called UPLOAD_FOLDER for
# backwards compatibility with existing deployments and the Docker entrypoint.
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(tempfile.gettempdir(), "pregdos_uploads")

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = os.environ.get("PREGDOS_SECRET_KEY") or secrets.token_urlsafe(32)

# Templates render doses with a shared SI prefix (e.g. "3.8 mSv") via this helper.
app.jinja_env.globals["fmt_dose"] = results.humanize_dose


class UploadRejected(Exception):
    """The uploaded DICOM study cannot be converted.  Carries one message per problem."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = problems


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


@app.context_processor
def inject_layout_context():
    """Values every page needs: the version shown in the nav, and the funding logos.

    setuptools-scm produces versions like ``0.3.0.post33+g1a87f860.d20260709``.  The local
    part after ``+`` identifies the exact commit, which is valuable in a bug report but too
    long for a nav bar, so the short form is shown and the full one hangs off the tooltip.
    """
    try:
        version = importlib.metadata.version("pregdos")
    except importlib.metadata.PackageNotFoundError:
        version = "dev"
    return {
        "pregdos_version": version,
        "pregdos_version_short": version.split("+", 1)[0],
        "funding_logos": _funding_logos(),
    }


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

            # Check the study *as uploaded*, so an error can name the real layout problem,
            # then normalise it: TOPAS reads DicomDirectory non-recursively, so every
            # modality has to sit in one directory (#52).
            intake = dicom_intake.scan(dicom_dir)
            problems = dicom_intake.validate(intake)
            if problems:
                raise UploadRejected(problems)
            notes = dicom_intake.warnings(intake)
            dicom_intake.flatten(intake)

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
        except UploadRejected as e:
            shutil.rmtree(study_path, ignore_errors=True)
            for problem in e.problems:
                flash(problem)
            return redirect(request.url)
        except Exception as e:
            shutil.rmtree(study_path, ignore_errors=True)
            flash(str(e) if str(e) else "Upload failed.")
            return redirect(request.url)

        for note in notes:
            flash(note)

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

    prepass = None
    if selected_structures:
        try:
            prepass = structure_metrics.write_mask_prepass(run_dir / result.out_files[0], selected_structures)
        except Exception as err:
            shutil.rmtree(run_dir, ignore_errors=True)
            flash(f"Structure mask pre-pass setup failed: {err}")
            return redirect(url_for("upload_files"))

    return render_template(
        "convert_success.html",
        out_files=result.out_files,
        prepass_file=prepass,
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

    # Refuse to launch into a broken toolchain: an unsupported OpenTOPAS would emit NaN doses
    # (#49) and a stale TOPAS_G4_DATA_DIR would abort every field seconds in.  Cheaper to stop
    # here than to discover it hours later in a log.
    blocker = versions.submit_blocker()
    if blocker:
        flash(f"Cannot submit — {blocker}")
        return redirect(url_for("run_detail", study=study_name, run_id=run_id))

    # Validate every requested file up front, so a typo cannot start a partial run.
    topas_files = []
    missing = []
    prepass = structure_metrics.MASK_PREPASS_FILE
    if (run_dir / prepass).is_file():
        topas_files.append(prepass)
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
            return versions.UNKNOWN

    # TOPAS and Geant4 are interrogated at runtime (env/marker first, then the binary
    # itself), so the page reports what will actually run, not what was configured.
    env = versions.summary()
    env["pregdos"] = pkg_version("pregdos")
    env["dicomexport"] = pkg_version("dicomexport")
    return render_template("about.html", versions=env)


# Funding acknowledgement logos, shown on the About page when present.  Absent files are
# simply not rendered, so a source checkout without them shows text rather than a broken image.
#
# `plate`: the logo is transparent with dark lettering, so it needs a light backing in dark
# mode.  The EU flag carries its own blue field and must not be plated.
_FUNDING_LOGOS = (
    {"file": "pianoforte-logo.png", "alt": "PIANOFORTE Partnership",
     "href": "https://pianoforte-partnership.eu/", "plate": True},
    {"file": "eu-flag.png", "alt": "Funded by the European Union",
     "href": None, "plate": False},
)


def _funding_logos() -> list[dict]:
    static_dir = Path(app.static_folder or "")
    return [logo for logo in _FUNDING_LOGOS if (static_dir / "img" / logo["file"]).is_file()]


NOT_SUBMITTED = "not submitted"


def _format_duration(seconds: float) -> str:
    """A rough human duration: "~2h 15m", "~45m", "~30s"."""
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"~{h}h {m}m"
    if m:
        return f"~{m}m"
    return f"~{s}s"


def _completion_clock(finish: datetime.datetime) -> dict:
    """Local wall-clock finish, split for a two-line box: ``{"time": "14:30", "date": "11 Jul"}``.

    Day-first date, European style.
    """
    return {"time": finish.strftime("%H:%M"), "date": f"{finish.day} {finish:%b}"}


def _run_status(run_dir: Path) -> str:
    """Status of one run, read entirely from files on disk (see :mod:`pregdos.executor`)."""
    if executor.read_run_metadata(run_dir) is None:
        return NOT_SUBMITTED
    return executor.run_status(run_dir)


def _resolve_run(study, run_id):
    """Resolve a run directory from URL parameters, or return None."""
    try:
        run_dir = studies.run_path(studies_root(), study, run_id)
    except StudyError:
        return None
    return run_dir if run_dir.is_dir() else None


@app.route("/jobs")
def list_jobs():
    """Backwards-compatible alias for the results page."""
    return redirect(url_for("list_studies"))


@app.route("/studies")
def list_studies():
    """One tile per study, each listing its conversion runs and their status."""
    root = studies_root()
    tiles = []
    for study in studies.list_studies(root):
        runs = []
        for run_id in studies.list_runs(root, study):
            run_dir = studies.run_path(root, study, run_id)
            status = _run_status(run_dir)
            percent = eta = None
            # Only a running job needs its logs parsed for the pie and completion clock.
            if status == executor.RUNNING:
                prog = executor.run_progress(run_dir)
                total = sum(p.histories_total for p in prog)
                if total > 0:
                    percent = round(100 * sum(p.histories_done for p in prog) / total)
                info = executor.read_run_metadata(run_dir)
                finish = executor.estimate_completion_time(prog, info.submitted if info else None)
                eta = _completion_clock(finish) if finish else None
            runs.append({
                "run_id": run_id,
                "status": status,
                "percent": percent,
                "eta": eta,
                "file_count": sum(1 for p in run_dir.iterdir() if p.is_file()),
            })
        active = any(r["status"] in (executor.RUNNING, executor.QUEUED) for r in runs)
        tiles.append({"name": study, "runs": runs, "active": active})
    # Keep the page live while any run is in flight, like the detail page.
    auto_refresh = any(t["active"] for t in tiles)
    return render_template("studies.html", tiles=tiles, auto_refresh=auto_refresh)


@app.route("/studies/<study>/<run_id>")
def run_detail(study, run_id):
    """Scorer results for one run, plus its raw files.

    The CSVs are parsed at render time rather than at submit time: when /submit runs, the
    job has not produced anything yet.  Parsing a handful of single-voxel CSVs per page view
    costs nothing.
    """
    run_dir = _resolve_run(study, run_id)
    if run_dir is None:
        flash("Run directory not found.")
        return redirect(url_for("list_studies"))

    rows, warnings, plan_fractions = _result_rows(run_dir, study)
    groups = _group_rows(rows)
    for w in warnings:
        flash(f"Could not read scorer output: {w}")

    files = [{"name": p.name, "size": p.stat().st_size} for p in sorted(run_dir.iterdir()) if p.is_file()]
    info = executor.read_run_metadata(run_dir)
    status = _run_status(run_dir)
    field_progress = executor.run_progress(run_dir)
    etr = None
    if status == executor.RUNNING and info is not None:
        seconds = executor.estimate_remaining_seconds(field_progress, info.submitted)
        etr = _format_duration(seconds) if seconds is not None else None
    progress = [
        {
            "field": p.topas_file,
            "status": p.status,
            "percent": round(100 * p.fraction),
            "histories_done": p.histories_done,
            "histories_total": p.histories_total,
            "runs_started": p.runs_started,
            "total_runs": p.total_runs,
        }
        for p in field_progress
    ]
    return render_template(
        "run_detail.html",
        study=study,
        run_id=run_id,
        status=status,
        # While work is in flight the page refreshes itself so the bars advance.
        auto_refresh=status in (executor.RUNNING, executor.QUEUED),
        progress=progress,
        etr=etr,
        groups=groups,
        plan_fractions=plan_fractions,
        files=files,
        backend=info.backend if info else None,
        submitted=info.submitted if info else None,
        logs=[f["name"] for f in files if f["name"].endswith(".log")],
    )


def _group_rows(rows: list) -> list:
    """Group scorer rows by scorer, and total each group over its fields.

    A clinician reads one quantity at a time -- "how much neutron dose did the brainstem
    get, from all fields together" -- so the scorer is the outer key and the field the inner.

    The per-field values are each already scaled to that field's own particle budget, so the
    plan total is their plain sum.  Their uncertainties, however, come from independent Monte
    Carlo runs and therefore add **in quadrature**, not linearly.

    A group containing any unusable row (a NaN Sum, or a multi-bin grid) gets no total: a
    partial sum over fields would understate the dose while looking authoritative.  Nor is a
    group totalled when two rows share a field number -- ``IfOutputFileAlreadyExists =
    "Increment"`` writes a second CSV for a re-run of the *same* field, and adding those
    together would double-count it.
    """
    groups: dict = {}
    for row in rows:
        key = (row["scorer"], row["structure"], row["quantity"], row["unit"])
        groups.setdefault(key, []).append(row)

    out = []
    for (scorer, structure, quantity, unit), members in groups.items():
        members.sort(key=lambda r: (r["field"] is None, r["field"]))
        summable = [r for r in members if r["problem"] is None and r["sum"] is not None]
        complete = len(summable) == len(members)

        fields = [r["field"] for r in members]
        distinct_fields = None not in fields and len(set(fields)) == len(fields)

        total_sum = total_sd = None
        if complete and distinct_fields and len(members) > 1:
            total_sum = sum(r["sum"] for r in summable)
            sds = [r["sd"] for r in summable if r["sd"] is not None]
            total_sd = math.sqrt(sum(sd * sd for sd in sds)) if len(sds) == len(summable) else None

        out.append({
            "scorer": scorer, "structure": structure, "quantity": quantity, "unit": unit,
            "rows": members, "total_sum": total_sum, "total_sd": total_sd,
            "n_fields": len(members),
        })

    out.sort(key=lambda g: (g["scorer"], g["structure"]))
    return out


def _result_rows(run_dir: Path, study: str):
    """Parsed, plan-scaled scorer rows for one run, ready for the results table."""
    parsed, warnings = results.collect_results(run_dir)
    metrics, metric_warnings = structure_metrics.ensure_metrics(run_dir)
    warnings.extend(metric_warnings)

    # Field names come from the study's RTPLAN.  A field is identified by its DICOM
    # BeamNumber, which need not match its position in the plan -- so show the name a
    # clinician would recognise, not just an index.
    plan_fractions = None
    try:
        rtplan = studies.find_rtplan(studies_root(), study)
        names = results.beam_names(rtplan)
        plan_fractions = results.planned_fractions(rtplan)
    except StudyError:
        names = {}
    fraction_multiplier = plan_fractions or 1

    rows = []
    for r in parsed:
        scaling = results.scaling_for(r, run_dir)
        total, sd = r.scaled(scaling)
        metric = None
        mass_normalized = False
        volume_normalized = False
        quantity = r.quantity
        unit = r.unit
        problem = r.problem
        if r.is_single_bin:
            metric = structure_metrics.structure_metric(metrics, r.structure)
            if r.quantity == "EnergyDeposit":
                converted = structure_metrics.energy_deposit_to_gy(metrics, r.structure, r.unit, total, sd)
                if converted is not None:
                    total, sd = converted
                    quantity = "DoseToMedium"
                    unit = "Gy"
                    mass_normalized = True
                elif problem is None:
                    problem = "structure mass metrics are missing; cannot convert EnergyDeposit to Gy"
            elif r.quantity == "AmbientDoseEquivalent" and r.structure:
                correction = (
                    structure_metrics.fluence_volume_correction_factor(metrics, r.structure)
                    if r.component == "Patient"
                    else None
                )
                if correction is not None:
                    if total is not None:
                        total *= correction
                    if sd is not None:
                        sd *= correction
                    volume_normalized = True
                elif problem is None:
                    problem = "structure volume metrics or scorer component are missing; cannot correct fluence denominator"
            elif r.quantity == "DoseToWater" and r.structure:
                # DoseToWater is an intensive dose (Gy) that TOPAS divides by the whole
                # patient-box volume for a single-bin scorer, exactly like the H*(10) fluence
                # scorer. Rescale that denominator to the structure volume (V_patient/V_struct);
                # only EnergyDeposit (energy, no volume division) uses the structure mass.
                correction = (
                    structure_metrics.fluence_volume_correction_factor(metrics, r.structure)
                    if r.component == "Patient"
                    else None
                )
                if correction is not None:
                    if total is not None:
                        total *= correction
                    if sd is not None:
                        sd *= correction
                    volume_normalized = True
                elif problem is None:
                    problem = (
                        "structure volume metrics or scorer component are missing; "
                        "cannot correct DoseToWater denominator"
                    )
            elif r.quantity == "DoseToMedium" and r.component == "Patient" and r.structure and problem is None:
                problem = ("structure DoseToMedium from TOPAS is not accepted; "
                           "rerun with PregDos EnergyDeposit structure scoring")
        if fraction_multiplier != 1:
            if total is not None:
                total *= fraction_multiplier
            if sd is not None:
                sd *= fraction_multiplier
        number = r.field_number
        rows.append({
            "field": number,
            "field_name": names.get(number, "") if number is not None else "",
            "scorer": r.scorer,
            "structure": r.structure or "—",
            "quantity": quantity,
            "unit": unit,
            "sum": total,
            "sd": sd,
            "raw_sum": r.raw_sum,
            "problem": problem,
            "scale": scaling.factor * fraction_multiplier if scaling else None,
            "plan_fractions": plan_fractions,
            "structure_volume_cm3": metric.get("volume_cm3") if metric else None,
            "structure_mass_g": metric.get("mass_g") if metric else None,
            "structure_average_density_g_cm3": metric.get("average_density_g_cm3") if metric else None,
            "structure_mass_normalized": mass_normalized,
            "structure_volume_normalized": volume_normalized,
            "csv_name": r.csv_name,
        })
    return rows, warnings, plan_fractions


@app.route("/studies/<study>/<run_id>/report.csv")
def download_report(study, run_id):
    """Aggregate every scorer in the run into one plan-scaled CSV report."""
    run_dir = _resolve_run(study, run_id)
    if run_dir is None:
        flash("Run directory not found.")
        return redirect(url_for("list_studies"))

    rows, _, plan_fractions = _result_rows(run_dir, study)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["# PregDos report", f"study={study}", f"run={run_id}"])
    writer.writerow(["# Doses are PHYSICAL absorbed dose (Gy) / equivalent dose (Sv); no RBE is "
                     "applied. Eclipse proton RTDOSE is Gy(RBE) = physical dose-to-water x 1.1."])
    if plan_fractions:
        writer.writerow([f"# Planned fractions: {plan_fractions}; reported values are total course dose."])
    else:
        writer.writerow(["# Planned fractions: unavailable; reported values are per generated TOPAS plan scale."])
    writer.writerow(["# Doses are scaled from the simulated histories to the per-fraction plan scale (and to total course dose when planned fractions are available). Structure EnergyDeposit rows are "
                     "mass-normalized (energy / structure mass); structure DoseToWater and "
                     "fluence rows are volume-normalized (patient box rescaled to the structure "
                     "volume); see issue #50."])
    writer.writerow(["# dose_uncertainty is the 1-sigma Monte-Carlo statistical error "
                     "(sqrt(N)*SD/Sum applied to the scaled dose, N = simulated histories)."])
    writer.writerow(["# field=ALL rows total a scorer over its fields; their uncertainties add in quadrature."])
    writer.writerow(["field", "field_name", "scorer", "structure", "quantity", "unit",
                     "dose", "dose_uncertainty", "scale_factor", "mass_normalized", "volume_normalized",
                     "structure_volume_cm3", "structure_mass_g", "structure_average_density_g_cm3", "note"])
    for group in _group_rows(rows):
        for r in group["rows"]:
            writer.writerow([
                "" if r["field"] is None else r["field"], r["field_name"],
                r["scorer"], r["structure"], r["quantity"], r["unit"],
                "" if r["sum"] is None else repr(r["sum"]),
                "" if r["sd"] is None else repr(r["sd"]),
                "" if r["scale"] is None else repr(r["scale"]),
                "yes" if r["structure_mass_normalized"] else "",
                "yes" if r["structure_volume_normalized"] else "",
                "" if r["structure_volume_cm3"] is None else repr(r["structure_volume_cm3"]),
                "" if r["structure_mass_g"] is None else repr(r["structure_mass_g"]),
                "" if r["structure_average_density_g_cm3"] is None else repr(r["structure_average_density_g_cm3"]),
                r["problem"] or "",
            ])
        if group["total_sum"] is not None:
            writer.writerow([
                "ALL", "", group["scorer"], group["structure"], group["quantity"], group["unit"],
                repr(group["total_sum"]),
                "" if group["total_sd"] is None else repr(group["total_sd"]),
                "", "", "", "", "", "", f"sum over {group['n_fields']} fields",
            ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{secure_filename(study)}_{run_id}_report.csv"'},
    )


@app.route("/studies/<study>/delete", methods=["POST"])
def delete_study(study):
    """Remove a study and everything under it.

    An unfinished run is cancelled first: TOPAS jobs take hours, and one left running would
    keep writing into a directory that has just been deleted.
    """
    root = studies_root()
    try:
        study_dir = studies.study_path(root, study)
    except StudyError:
        flash("Invalid study path.")
        return redirect(url_for("list_studies"))
    if not study_dir.is_dir():
        flash("Study not found.")
        return redirect(url_for("list_studies"))

    cancelled = 0
    for run_id in studies.list_runs(root, study):
        run_dir = studies.run_path(root, study, run_id)
        if _run_status(run_dir) in (executor.RUNNING, executor.QUEUED):
            executor.cancel_run(run_dir)
            cancelled += 1

    shutil.rmtree(study_dir, ignore_errors=True)
    if cancelled:
        flash(f"Cancelled {cancelled} unfinished run(s).")
    flash(f"Deleted study {study}.")
    return redirect(url_for("list_studies"))


@app.route("/studies/download/<study>/<run_id>/<filename>")
def download_job_file(study, run_id, filename):
    run_dir = _resolve_run(study, run_id)
    if run_dir is None:
        flash("Run directory not found.")
        return redirect(url_for("list_studies"))
    return send_from_directory(str(run_dir), secure_filename(filename), as_attachment=True)


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def main():
    # Debug is OFF by default: the Werkzeug debugger is an interactive console, and the app
    # binds all interfaces, so debug=True on a shared network is remote code execution.
    # Opt in explicitly with PREGDOS_DEBUG=1 for local development only.
    app.run(debug=_env_flag("PREGDOS_DEBUG"), host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
