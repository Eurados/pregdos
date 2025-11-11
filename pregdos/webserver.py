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

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(tempfile.gettempdir(), "pregdos_uploads")
ALLOWED_EXTENSIONS = {"dcm", "csv", "txt"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = "pregdos_secret_key"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# HTML form moved to templates/upload.html


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
        study_zip = request.files["study_zip"]
        beam_model = request.files["beam_model"]
        spr_table = request.files["spr_table"]
        if not (study_zip and beam_model and spr_table):
            flash("All files are required!")
            return redirect(request.url)
        study_zip_path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(study_zip.filename))
        beam_model_path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(beam_model.filename))
        spr_table_path = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(spr_table.filename))
        study_zip.save(study_zip_path)
        beam_model.save(beam_model_path)
        spr_table.save(spr_table_path)

        study_dir = os.path.join(app.config["UPLOAD_FOLDER"], Path(study_zip.filename).stem)
        os.makedirs(study_dir, exist_ok=True)
        with zipfile.ZipFile(study_zip_path, "r") as zip_ref:
            zip_ref.extractall(study_dir)

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


@app.route("/convert", methods=["POST"])
def convert():
    study_dir = request.form["study_dir"]
    beam_model_path = request.form["beam_model_path"]
    spr_table_path = request.form["spr_table_path"]
    selected_structures = request.form.getlist("structures")
    filtered_dir = filter_rtstruct_keep_rois(study_dir, selected_structures)
    study_to_use = filtered_dir
    output_base = os.path.join(study_to_use, "topas")
    cmd_prefix = _dicomexport_cmd_prefix()
    cmd = cmd_prefix + [
        "-b",
        beam_model_path,
        "-s",
        spr_table_path,
        study_to_use,
        output_base,
    ]
    env = os.environ.copy()
    try:
        subprocess.run(cmd, check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        out = (e.stdout or "").strip()
        err = (e.stderr or str(e)).strip()
        msg = "".join([part for part in (err, out) if part])
        flash(f"Error running conversion: {msg}")
        return redirect("/")
    out_files = [
        f for f in os.listdir(study_to_use)
        if f.startswith("topas") and f.endswith(".txt")
    ]
    if not out_files:
        flash("No output files generated.")
        return redirect("/")
    return render_template(
        'convert_success.html',
        out_files=out_files,
        study_name=Path(study_to_use).name,
        selected_structures=selected_structures,
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
