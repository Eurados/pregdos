from flask import (
    Flask,
    request,
    render_template,
    send_from_directory,
    redirect,
    flash,
)
import pydicom
import glob

import zipfile
import os
from werkzeug.utils import secure_filename
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import copy
from typing import List

from .models import ConversionParameters, ConversionResult

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(tempfile.gettempdir(), "pregdos_uploads")
ALLOWED_EXTENSIONS = {"dcm", "csv", "txt"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = "pregdos_secret_key"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# HTML form moved to templates/upload.html

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
            if member.endswith('/'):
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
        out_path = os.path.join(study_dir, *parts) if parts else os.path.join(study_dir, secure_filename(Path(file.filename).name))
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
    # copy all files into tmpdir
    shutil.copytree(orig_study_dir, tmpdir, dirs_exist_ok=True)

    # find RTST in copy
    rs_files = glob.glob(os.path.join(tmpdir, "RS*.dcm"))
    if not rs_files:
        return tmpdir
    rs_path = rs_files[0]
    ds = pydicom.dcmread(rs_path)

    # map ROIName -> ROINumber
    name_to_number = {}
    if hasattr(ds, 'StructureSetROISequence'):
        for roi in ds.StructureSetROISequence:
            name = getattr(roi, 'ROIName', None)
            number = getattr(roi, 'ROINumber', None)
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
        if not hasattr(seq, '__iter__'):
            return seq
        out = []
        for item in seq:
            val = getattr(item, attr_name, None)
            if val in keep_numbers:
                out.append(item)
        return out

    # StructureSetROISequence: keep by ROINumber
    if hasattr(new_ds, 'StructureSetROISequence'):
        new_ds.StructureSetROISequence = [item for item in new_ds.StructureSetROISequence if getattr(
            item, 'ROINumber', None) in keep_numbers]

    # ROIContourSequence: keep by ReferencedROINumber
    if hasattr(new_ds, 'ROIContourSequence'):
        new_ds.ROIContourSequence = [item for item in new_ds.ROIContourSequence if getattr(
            item, 'ReferencedROINumber', None) in keep_numbers]

    # RTROIObservationsSequence: keep by ReferencedROINumber
    if hasattr(new_ds, 'RTROIObservationsSequence'):
        new_ds.RTROIObservationsSequence = [item for item in new_ds.RTROIObservationsSequence if getattr(
            item, 'ReferencedROINumber', None) in keep_numbers]

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


@app.route("/", methods=["GET", "POST"])
def upload_files():
    if request.method == "POST":
        study_zip = request.files.get("study_zip")
        study_dir_files = [f for f in (request.files.getlist("study_dir") or []) if f and f.filename]
        beam_model = request.files.get("beam_model")
        spr_table = request.files.get("spr_table")

        # Validate input
        if not (beam_model and beam_model.filename and spr_table and spr_table.filename):
            flash("Beam model and SPR table required.")
            return redirect(request.url)
        if not study_zip and not study_dir_files:
            flash("Provide either a ZIP or a folder.")
            return redirect(request.url)

        if (study_zip and study_zip.filename) and study_dir_files:
            flash("Please choose either ZIP or Folder, not both.")
            return redirect(request.url)

        upload_folder = app.config["UPLOAD_FOLDER"]

        beam_model_path = save_single_file(beam_model, upload_folder)
        spr_table_path = save_single_file(spr_table, upload_folder)

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
        # Render structure selection template
        return render_template(
            'select_structures.html',
            structures=structures,
            study_dir=study_dir,
            beam_model_path=beam_model_path,
            spr_table_path=spr_table_path,
        )
    return render_template('upload.html')

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

    search_dirs = {Path(study_to_use), Path(params.study_dir), Path(output_base).parent}
    found = []
    for d in search_dirs:
        if not d.exists():
            continue
        for f in os.listdir(d):
            if f.startswith("topas") and f.endswith(".txt"):
                found.append(f)
    # Deduplicate basenames
    out_files = []
    seen = set()
    for f in found:
        if f not in seen:
            seen.add(f)
            out_files.append(f)
    if not out_files:
        raise RuntimeError("No output files generated by dicomexport.")

    return ConversionResult(
        out_files=out_files,
        study_name=Path(study_to_use).name,
        selected_structures=list(selected_structures),
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


@app.route("/convert", methods=["POST"])
def convert():
    study_dir = request.form["study_dir"]
    beam_model_path = request.form["beam_model_path"]
    spr_table_path = request.form["spr_table_path"]
    selected_structures = request.form.getlist("structures")
    params = ConversionParameters(
        study_dir=study_dir,
        beam_model_path=beam_model_path,
        spr_table_path=spr_table_path,
        output_base=os.path.join(study_dir, "topas"),
        field_nr=None,
        nstat=None,
    )
    try:
        result = run_conversion(params, selected_structures)
    except RuntimeError as err:
        flash(str(err))
        return redirect("/")
    return render_template(
        'convert_success.html',
        out_files=result.out_files,
        study_name=result.study_name,
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
        return redirect("/")
    file_path = os.path.join(abs_dir_path, safe_filename)
    if not os.path.isfile(file_path):
        flash("File not found.")
        return redirect("/")
    return send_from_directory(abs_dir_path, safe_filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
