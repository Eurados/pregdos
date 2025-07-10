from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class Image:
    """
    A single image in a CT scan.

    Attributes:
        pixel_data: The pixel data of the image.
        instance_number: The instance number of the image.
        sop_instance_uid: The SOP Instance UID of the image.
        sop_class_uid: The SOP Class UID of the image.
    """
    modality: str = "CT"

    pixel_data: bytes = b""  # pixel data are not needed for the pipeline, but a placeholder is just provided for future use

    sop_class_uid: str = ""
    sop_instance_uid: str = ""
    modality: str = ""
    series_description: str = ""

    pixel_spacing: Tuple[float, float] = (0.0, 0.0)
    slice_thickness: float = 0.0
    slice_location: float = 0.0
    image_orientation: Tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0)
    image_position_patient: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    instance_number: int = 0

    patient_id: str = ""
    patient_name: str = ""
    patient_initials: str = ""
    patient_firstname: str = ""
    patient_position: str = ""
    rows: int = 0
    columns: int = 0


@dataclass
class CTModel:
    """
    A model for CT data in a proton treatment plan.

    Attributes:
        images: List of images in the CT model.
"""
    images: List[Image] = field(default_factory=list)
    spr_to_material_path: Optional[Path] = None  # in fact mandatory as long we depend on the dose cube

    # In dicom format, the following data are per image, and could in princple be different
    # from image to image, but we assume they are the same for all images in this model
    # therefore we store them in the CTModel class as well.
    # Topas is not able to handle varying data per image, so it is legit here, but
    # but should this change in the future, we can still adapt the code.
    @property
    def patient_id(self) -> str:
        return self.images[0].patient_id if self.images else ""

    @property
    def patient_name(self) -> str:
        return self.images[0].patient_name if self.images else ""

    @property
    def patient_position(self) -> str:
        return self.images[0].patient_position if self.images else ""

    @property
    def rows(self) -> int:
        return self.images[0].rows if self.images else 0

    @property
    def columns(self) -> int:
        return self.images[0].columns if self.images else 0

    @property
    def n_slices(self) -> int:
        """Number of slices in the CT model."""
        return len(self.images)

    @property
    def slice_thickness(self) -> float:
        """Slice thickness of the CT model."""
        if len(self.images) > 1:
            return self.images[1].slice_location - self.images[0].slice_location
        else:
            return 0.0

    def __repr__(self):
        return (
            f"<CTModel patient_id='{self.patient_id}', "
            f"name='{self.patient_name}', "
            f"position='{self.patient_position}', "
            f"slices={len(self.images)}>"
        )
