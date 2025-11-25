import copy
import glob
import os
import shutil
import tempfile
from pathlib import Path
from typing import List

import pydicom


def get_structures(study_dir: str) -> List[str]:
    rs_files = glob.glob(os.path.join(study_dir, "**", "RS*.dcm"), recursive=True)
    if not rs_files:
        return []
    ds = pydicom.dcmread(rs_files[0])
    return [roi.ROIName for roi in ds.StructureSetROISequence]


def filter_rtstruct_keep_rois(orig_study_dir: str, selected_rois: List[str]) -> str:
    """Copy orig_study_dir to a temp dir and rewrite the RTSTRUCT to keep only selected_rois.

    Returns the path to the filtered study dir (a copy).
    """
    parent = Path(orig_study_dir).parent
    tmpdir = tempfile.mkdtemp(
        prefix=Path(orig_study_dir).name + "_filtered_", dir=str(parent)
    )
    shutil.copytree(orig_study_dir, tmpdir, dirs_exist_ok=True)

    rs_files = glob.glob(os.path.join(tmpdir, "RS*.dcm"))
    if not rs_files:
        return tmpdir
    rs_path = rs_files[0]
    ds = pydicom.dcmread(rs_path)

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

    if not keep_numbers:
        return tmpdir

    new_ds = copy.deepcopy(ds)

    if hasattr(new_ds, "StructureSetROISequence"):
        new_ds.StructureSetROISequence = [
            item
            for item in new_ds.StructureSetROISequence
            if getattr(item, "ROINumber", None) in keep_numbers
        ]

    if hasattr(new_ds, "ROIContourSequence"):
        new_ds.ROIContourSequence = [
            item
            for item in new_ds.ROIContourSequence
            if getattr(item, "ReferencedROINumber", None) in keep_numbers
        ]

    if hasattr(new_ds, "RTROIObservationsSequence"):
        new_ds.RTROIObservationsSequence = [
            item
            for item in new_ds.RTROIObservationsSequence
            if getattr(item, "ReferencedROINumber", None) in keep_numbers
        ]

    try:
        new_ds.save_as(rs_path)
    except Exception:
        return tmpdir

    return tmpdir
