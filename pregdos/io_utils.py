import os
from pathlib import Path
import zipfile

from werkzeug.utils import secure_filename


def save_single_file(upload, folder: str) -> str:
    path = os.path.join(folder, secure_filename(upload.filename))
    upload.save(path)
    return path


def save_uploaded_directory(files, base_folder: str) -> str:
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
            os.path.join(study_dir, *parts)
            if parts
            else os.path.join(study_dir, secure_filename(Path(file.filename).name))
        )
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        file.save(out_path)
    return study_dir


def extract_zip(study_zip, folder: str) -> str:
    zip_path = save_single_file(study_zip, folder)
    study_dir = os.path.join(folder, Path(study_zip.filename).stem)
    os.makedirs(study_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(study_dir)
    return study_dir

