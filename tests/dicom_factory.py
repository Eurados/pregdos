"""Build minimal, well-formed DICOM studies for tests.

Real DICOM is the only honest input for the intake code: pydicom rejects a headerless
dataset, and so should we.
"""

from pathlib import Path

import pydicom
import pydicom.uid  # pydicom re-exports this at runtime, but only an explicit import declares it
from pydicom.dataset import Dataset, FileMetaDataset

SERIES_NUM = {"CT": 1, "RTSTRUCT": 2, "RTPLAN": 3, "RTDOSE": 4}

SOP = {
    "CT": pydicom.uid.CTImageStorage,
    "RTSTRUCT": pydicom.uid.RTStructureSetStorage,
    "RTPLAN": pydicom.uid.RTIonPlanStorage,
    "RTDOSE": pydicom.uid.RTDoseStorage,
}

STUDY_UID = "1.2.826.0.1.1"


def write(path: Path, modality, patient="PAT1", study=STUDY_UID, series=None, rois=(), fractions=None,
          declared=None):
    """Write one DICOM file of the given modality.

    ``declared`` gives an RTSTRUCT's overrides as Eclipse writes them, by ROI name: a
    ``MaterialID`` string, or a list of ``(ROIPhysicalProperty, value)`` pairs.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sop = SOP[modality]

    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = sop
    ds.file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds.SOPClassUID = sop
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.Modality = modality
    ds.PatientID = patient
    ds.StudyInstanceUID = study
    ds.SeriesInstanceUID = series or f"{study}.{SERIES_NUM[modality]}"

    if modality == "RTSTRUCT" and rois:
        seq = []
        for i, name in enumerate(rois, start=1):
            roi = Dataset()
            roi.ROINumber = i
            roi.ROIName = name
            seq.append(roi)
        ds.StructureSetROISequence = seq
        observations = []
        for i, name in enumerate(rois, start=1):
            obs = Dataset()
            obs.ObservationNumber = i
            obs.ReferencedROINumber = i
            obs.RTROIInterpretedType = "CONTROL" if name in (declared or {}) else "ORGAN"
            what = (declared or {}).get(name)
            if isinstance(what, str):
                obs.MaterialID = what
            elif what:
                props = []
                for prop, value in what:
                    item = Dataset()
                    item.ROIPhysicalProperty = prop
                    item.ROIPhysicalPropertyValue = value
                    props.append(item)
                obs.ROIPhysicalPropertiesSequence = props
            observations.append(obs)
        ds.RTROIObservationsSequence = observations

    if modality == "RTPLAN" and fractions is not None:
        group = Dataset()
        group.FractionGroupNumber = 1
        group.NumberOfFractionsPlanned = int(fractions)
        ds.FractionGroupSequence = [group]

    ds.save_as(str(path), enforce_file_format=True)
    return path


def flat_study(root: Path, n_ct=3, rois=("CTV", "Fetus"), declared=None):
    """CT, RS, RN, RD all side by side -- the layout TOPAS needs."""
    root = Path(root)
    for i in range(n_ct):
        write(root / f"CT.{i}.dcm", "CT")
    write(root / "RS.dcm", "RTSTRUCT", rois=rois, declared=declared)
    write(root / "RN.dcm", "RTPLAN")
    write(root / "RD.dcm", "RTDOSE")
    return root


def brain_layout(root: Path, n_ct=3, rois=("CTV", "Fetus")):
    """The real ~/Downloads/Brain shape: CT nested one level, the rest at the top."""
    root = Path(root)
    for i in range(n_ct):
        write(root / "CT" / f"CT.Image {i}.dcm", "CT")
    write(root / "RS.dcm", "RTSTRUCT", rois=rois)
    write(root / "RN.dcm", "RTPLAN")
    write(root / "RD.Pole 1.dcm", "RTDOSE")
    return root
