from dataclasses import dataclass


@dataclass
class CTModel:
    """
    A model for CT data in a proton treatment plan.

    Attributes:
        patient_id: Unique identifier for the patient.
        patient_name: Full name of the patient.
        patient_initials: Initials of the patient.
        patient_firstname: First name of the patient.
        plan_label: Label for the treatment plan.
        beam_name: Name of the beam used in the treatment.
        cmu: Total amount of MUs in this field.
        pld_csetweight: Weighting factor for the PLD.
        n_layers: Number of layers in the treatment plan.
    """
    patient_id: str = ""
    patient_name: str = ""
    patient_initials: str = ""
    patient_firstname: str = ""
    patient_position: str = ""
    pixel_spacing: float = 0.0
    slice_thickness: float = 0.0
    image_orientation: str = ""
    image_position: str = ""
    rows: int = 0
    columns: int = 0
    sop_instance_uid: str = ""

    def __repr__(self):
        """Return a string representation of the CT model."""
        pass
