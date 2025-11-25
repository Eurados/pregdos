from flask import (
    Flask,
    request,
    render_template,
    send_from_directory,
    redirect,
    flash,
)
from pathlib import Path
import os
import subprocess

from .io_utils import save_single_file, save_uploaded_directory, extract_zip
from .rtstruct_utils import get_structures
from .conversion import run_conversion, run_topas_for_result
from .models import ConversionParameters

UPLOAD_FOLDER = "/tmp/pregdos_uploads"
ALLOWED_EXTENSIONS = {"dcm", "csv", "txt"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = "pregdos_secret_key"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route("/", methods=["GET", "POST"])
def upload_files():
    if request.method == "POST":
        study_zip = request.files.get("study_zip")
        study_dir_files = [f for f in (request.files.getlist("study_dir") or []) if f and f.filename]
        beam_model = request.files.get("beam_model")
        spr_table = request.files.get("spr_table")

        # Validate input
        if not (beam_model and spr_table):
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
            flash("Ingen RS-fil eller strukturer fundet!")
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
    try:
        run_topas_for_result(result, study_dir)
    except subprocess.CalledProcessError as e:
        flash(f"TOPAS run failed: {e}")
        return redirect("/")
    return render_template(
        'convert_success.html',
        out_files=result.out_files,
        study_name=result.study_name,
        selected_structures=result.selected_structures,
    )


@app.route("/download/<study>/<filename>")
def download_file(study, filename):
    dir_path = os.path.join(app.config["UPLOAD_FOLDER"], study)
    return send_from_directory(dir_path, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)