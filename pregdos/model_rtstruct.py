from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class RTStructROI:
    """
    A model for a single ROI (Region of Interest) in an RT Structure Set.
    """
    roi_name: str
    roi_number: int
    rgb_color: Tuple[int, int, int]  # RGB color

    # Contour data can be added here if needed, but they are not needed for the pipeline.
    # Contour data are in patient coordinates.


@dataclass
class RTStruct:
    """
    A model representing an RT Structure Set for proton therapy planning.

    Attributes:
        rois: List of ROIs in the structure set.
        patient_id: Patient ID.
        patient_name: Patient name.
        patient_initials: Patient initials.
        patient_firstname: Patient first name.
    """

    modality: str = "RTSTRUCT"  # Default modality for RT Structure Set

    rois: List[RTStructROI] = field(default_factory=list)

    patient_id: str = ""
    patient_name: str = ""
    patient_initials: str = ""
    patient_firstname: str = ""
    frame_of_reference_uid: str = ""

    @property
    def n_rois(self) -> int:
        """Return the number of ROIs in the structure set."""
        return len(self.rois)
