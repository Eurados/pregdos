import logging
import pydicom
from pathlib import Path
from typing import List

from pregdos.model_ct import CTModel, Image

logger = logging.getLogger(__name__)


def get_ct_files_sorted_by_instance_number(directory: Path) -> List[Path]:
    """
    Return a list of CT DICOM file paths in the directory,
    sorted by the DICOM 'InstanceNumber' tag.

    Reason: we cannot rely on the file names to be sorted correctly,
    e.g. when the files are copied from a PACS system or running numbering as 1 instead of 001.
    """
    files = list(directory.glob("CT*.dcm"))
    if not files:
        raise FileNotFoundError(f"No CT DICOM files matching 'CT*.dcm' found in {directory}")

    def get_instance_number(file: Path) -> int:
        ds = pydicom.dcmread(file, stop_before_pixels=True)
        if not hasattr(ds, 'InstanceNumber'):
            raise AttributeError(f"File {file} is missing 'InstanceNumber' DICOM tag.")
        return int(ds.InstanceNumber)

    files.sort(key=get_instance_number)
    return files


def load_ct_series(mydir: Path) -> CTModel:
    if not mydir.is_dir():
        raise ValueError(f"{mydir} is not a directory")

    ct_files = get_ct_files_sorted_by_instance_number(mydir)

    ct_model = CTModel()

    for file in ct_files:
        ds = pydicom.dcmread(file, stop_before_pixels=False)
        logger.debug(f"Loading CT slice: {file.name}")

        img = Image(
            sop_class_uid=str(ds.SOPClassUID),
            sop_instance_uid=str(ds.SOPInstanceUID),
            modality=ds.Modality,
            series_description=ds.SeriesDescription,
            pixel_spacing=tuple(map(float, ds.PixelSpacing)),
            slice_location=float(ds.SliceLocation),
            image_orientation=tuple(map(float, ds.ImageOrientationPatient)),
            image_position_patient=tuple(map(float, ds.ImagePositionPatient)),
            instance_number=int(ds.InstanceNumber),
            rows=int(ds.Rows),
            columns=int(ds.Columns),
            patient_name=ds.PatientName,
            patient_id=ds.PatientID,
            patient_position=ds.PatientPosition,
        )
        ct_model.images.append(img)

    # Sort images by z-position if needed:
    ct_model.images.sort(key=lambda img: img.image_position_patient[2])

    return ct_model
