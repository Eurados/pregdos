from flask import (
    Flask,
    request,
    render_template_string,
    send_from_directory,
    redirect,
    url_for,
    flash,
)

import os
from werkzeug.utils import secure_filename
from pathlib import Path
import subprocess
import sys

UPLOAD_FOLDER = "/tmp/pregdos_uploads"
ALLOWED_EXTENSIONS = {"dcm", "csv", "txt"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = "pregdos_secret_key"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

HTML_FORM = """
<!doctype html>
<title>PregDos DICOM to TOPAS Converter</title>
<h1>Upload DICOM Study Folder, Beam Model, and SPR Table</h1>
<form method=post enctype=multipart/form-data>
  DICOM Study (zip): <input type=file name=study_zip required><br>
  Beam Model CSV: <input type=file name=beam_model required><br>
  SPR-to-Material Table: <input type=file name=spr_table required><br>
  <input type=submit value=Upload>
</form>
{% with messages = get_flashed_messages() %}
  {% if messages %}
    <ul>
    {% for message in messages %}
      <li>{{ message }}</li>
    {% endfor %}
    </ul>
  {% endif %}
{% endwith %}
"""


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
        study_zip_path = os.path.join(
            app.config["UPLOAD_FOLDER"], secure_filename(study_zip.filename)
        )
        beam_model_path = os.path.join(
            app.config["UPLOAD_FOLDER"], secure_filename(beam_model.filename)
        )
        spr_table_path = os.path.join(
            app.config["UPLOAD_FOLDER"], secure_filename(spr_table.filename)
        )
        study_zip.save(study_zip_path)
        beam_model.save(beam_model_path)
        spr_table.save(spr_table_path)
        import zipfile

        study_dir = os.path.join(
            app.config["UPLOAD_FOLDER"], Path(study_zip.filename).stem
        )
        os.makedirs(study_dir, exist_ok=True)
        with zipfile.ZipFile(study_zip_path, "r") as zip_ref:
            zip_ref.extractall(study_dir)
        output_base = os.path.join(study_dir, "topas")
        cmd = [
            sys.executable,
            "-m",
            "dicomexport.main",
            "-b",
            beam_model_path,
            "-s",
            spr_table_path,
            study_dir,
            output_base,
        ]
        try:
            subprocess.run(cmd, check=True)
        except Exception as e:
            flash(f"Error running conversion: {e}")
            return redirect(request.url)

        out_files = [
            f
            for f in os.listdir(study_dir)
            if f.startswith("topas") and f.endswith(".txt")
        ]
        if not out_files:
            flash("No output files generated.")
            return redirect(request.url)
        links = "".join(
            f'<li><a href="/download/{Path(study_dir).name}/{f}">{f}</a></li>'
            for f in out_files
        )
        return f"<h2>Conversion complete. Download your files:</h2><ul>{links}</ul>"
    return render_template_string(HTML_FORM)


@app.route("/download/<study>/<filename>")
def download_file(study, filename):
    dir_path = os.path.join(app.config["UPLOAD_FOLDER"], study)
    return send_from_directory(dir_path, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
