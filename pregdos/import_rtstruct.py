import pydicom
import logging
from pathlib import Path
from typing import List

from pregdos.model_rtstruct import RTStruct, RTStructROI


logger = logging.getLogger(__name__)


def import_rtstruct(rtstruct_path: Path) -> RTStruct:
    """
    Import an RT Structure Set (RTSTRUCT) from a DICOM file and return a RTStruct.
    Path may also be a directory containing an RS*.dcm files.

    """

    if rtstruct_path.is_dir():
        # Search for the first RS*.dcm file in the directory
        rs_files = sorted(rtstruct_path.glob("RS*.dcm"))
        if not rs_files:
            raise FileNotFoundError(f"No RS*.dcm file found in directory: {rtstruct_path}")
        rtstruct_file = rs_files[0]
        logger.info(f"Using RTSTRUCT file: {rtstruct_file.name}")
    else:
        if not rtstruct_path.exists():
            raise FileNotFoundError(f"RTSTRUCT file not found: {rtstruct_path}")
        rtstruct_file = rtstruct_path

    ds = pydicom.dcmread(rtstruct_file)

    # Check if the file is an RTSTRUCT
    if ds.Modality != "RTSTRUCT":
        raise ValueError(f"File {rtstruct_file} is not a valid RTSTRUCT file (Modality: {ds.Modality})")

    # --- Basic patient info ---
    patient_id = ds.get("PatientID", "")
    patient_name = ds.get("PatientName", "")
    patient_firstname = ds.get("PatientFirstName", "")
    patient_initials = ds.get("PatientInitials", "")

    # --- Frame of Reference UID ---
    frame_of_reference_uid = getattr(ds, "FrameOfReferenceUID", None)
    if not frame_of_reference_uid:
        raise ValueError("RTSTRUCT missing FrameOfReferenceUID, required for geometry consistency checks.")

    # --- Parse ROIs ---
    rois: List[RTStructROI] = []

    # Build ROI name/number map from StructureSetROISequence
    roi_name_map = {}
    for roi in ds.StructureSetROISequence:
        roi_number = int(roi.ROINumber)
        roi_name = str(roi.ROIName)
        roi_name_map[roi_number] = roi_name

    # Parse colors if available
    color_map = {}
    if hasattr(ds, "ROIContourSequence"):
        for roi_contour in ds.ROIContourSequence:
            roi_number = int(roi_contour.ReferencedROINumber)
            if hasattr(roi_contour, "ROIDisplayColor"):
                color_map[roi_number] = tuple(int(c) for c in roi_contour.ROIDisplayColor)

    # Assemble ROIs
    for roi_number, roi_name in roi_name_map.items():
        color = color_map.get(roi_number, (255, 255, 255))  # Default white
        rois.append(RTStructROI(
            roi_name=roi_name,
            roi_number=roi_number,
            rgb_color=color
        ))

    logger.info(f"Imported RTSTRUCT: {rtstruct_path.name} with {len(rois)} ROIs")

    return RTStruct(
        rois=rois,
        frame_of_reference_uid=frame_of_reference_uid,
        patient_id=patient_id,
        patient_name=str(patient_name),
        patient_initials=patient_initials,
        patient_firstname=patient_firstname
    )
