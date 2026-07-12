import zipfile

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ImplicitVRLittleEndian, RTDoseStorage, generate_uid

from pregdos import rtdose


def _template() -> Dataset:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = RTDoseStorage
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    ds.SOPClassUID = RTDoseStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.Modality = "RTDOSE"
    ds.DoseUnits = "GY"
    ds.DoseType = "PHYSICAL"
    ds.DoseSummationType = "PLAN"
    ds.DoseGridScaling = "3e-05"
    ds.BitsAllocated = 32
    ds.BitsStored = 32
    ds.HighBit = 31
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = 2
    ds.Columns = 2
    ds.NumberOfFrames = "1"
    ds.PixelData = np.zeros((1, 2, 2), dtype="<u4").tobytes()
    ds.is_little_endian = True
    ds.is_implicit_VR = True
    return ds


def test_encode_preserves_eclipse_pixel_contract_when_dose_fits(tmp_path):
    ds = _template()

    rtdose._encode(ds, np.array([[[0.0, 1.0], [10.0, 70.0]]], dtype=np.float64))

    assert ds.DoseGridScaling == "3e-05"
    assert ds.BitsAllocated == 32
    assert ds.BitsStored == 32
    assert ds.HighBit == 31
    assert ds.pixel_array.dtype == np.uint32
    assert ds.pixel_array.max() == round(70.0 / 3e-05)

    out = tmp_path / "dose.dcm"
    ds.save_as(out, enforce_file_format=True)
    reread = pydicom.dcmread(out)
    assert reread.file_meta.TransferSyntaxUID == ImplicitVRLittleEndian
    assert reread.is_implicit_VR


def test_encode_writes_valid_ds_scaling_when_template_scaling_would_overflow():
    ds = _template()
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelData = np.zeros((1, 2, 2), dtype="<u2").tobytes()

    rtdose._encode(ds, np.array([[[0.0, 1.0], [10.0, 70.0]]], dtype=np.float64))

    assert len(str(ds.DoseGridScaling)) <= 16
    assert ds.BitsAllocated == 16
    assert ds.BitsStored == 16
    assert ds.HighBit == 15
    assert ds.pixel_array.dtype == np.uint16
    assert ds.pixel_array.max() <= 2**16 - 1


def test_plan_derive_can_preserve_eclipse_identity():
    template = _template()
    template.SeriesInstanceUID = "1.2.3.4"
    original_sop = template.SOPInstanceUID
    original_media_sop = template.file_meta.MediaStorageSOPInstanceUID

    ds = rtdose._derive(
        template,
        series_uid=generate_uid(),
        summation="PLAN",
        description=None,
        plan=Dataset(),
        preserve_identity=True,
    )

    assert ds.SOPInstanceUID == original_sop
    assert ds.file_meta.MediaStorageSOPInstanceUID == original_media_sop
    assert ds.SeriesInstanceUID == "1.2.3.4"
    assert "DoseComment" not in ds


def test_write_plan_import_bundle_contains_total_dose_and_referenced_plan(tmp_path):
    plan = tmp_path / "RN.1.2.3.dcm"
    dose = tmp_path / rtdose.PLAN_DOSE_NAME
    plan.write_bytes(b"plan")
    dose.write_bytes(b"dose")

    bundle = rtdose._write_plan_import_bundle(tmp_path, plan, dose)

    assert bundle.name == rtdose.PLAN_IMPORT_BUNDLE_NAME
    with zipfile.ZipFile(bundle) as zf:
        assert sorted(zf.namelist()) == ["RN.1.2.3.dcm", rtdose.PLAN_DOSE_NAME]
        assert zf.read("RN.1.2.3.dcm") == b"plan"
        assert zf.read(rtdose.PLAN_DOSE_NAME) == b"dose"
