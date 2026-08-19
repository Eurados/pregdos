import zipfile

import numpy as np
import pydicom
import pydicom.uid  # pydicom re-exports this at runtime, but only an explicit import declares it
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


def test_ensure_dose_export_is_idempotent(tmp_path, mocker):
    """Built once on first request, reused after -- the page must not rebuild 11M-voxel grids."""
    (tmp_path / "topas_field1.dcm").write_bytes(b"cube")
    def fake_postprocess(d):
        path = tmp_path / rtdose.PLAN_DOSE_NAME
        path.write_bytes(b"x")
        return [path]

    post = mocker.patch("pregdos.rtdose.postprocess", side_effect=fake_postprocess)

    rtdose.ensure_dose_export(tmp_path)
    assert (tmp_path / rtdose.PLAN_DOSE_NAME).is_file()
    post.assert_called_once()

    rtdose.ensure_dose_export(tmp_path)      # already there
    post.assert_called_once()                # still once: not rebuilt


def test_ensure_dose_export_without_a_cube_does_nothing(tmp_path):
    assert rtdose.ensure_dose_export(tmp_path) == ([], [])


def test_ensure_dose_export_reports_failure_instead_of_raising(tmp_path, mocker):
    (tmp_path / "topas_field1.dcm").write_bytes(b"cube")
    mocker.patch("pregdos.rtdose.postprocess",
                 side_effect=rtdose.RTDoseError("no clinical RTDOSE"))

    paths, warnings = rtdose.ensure_dose_export(tmp_path)

    assert paths == [] and warnings == ["no clinical RTDOSE"]


def _plan_with_beams(path, beams):
    sop_class = pydicom.uid.RTIonPlanStorage
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.Modality = "RTPLAN"
    ds.IonBeamSequence = []
    for number in beams:
        beam = Dataset()
        if number is None:
            beam.add_new(0x300A00C0, "IS", None)   # BeamNumber present but empty
        else:
            beam.BeamNumber = number
        ds.IonBeamSequence.append(beam)
    group = Dataset()
    group.FractionGroupNumber = 1
    group.NumberOfFractionsPlanned = 1
    ds.FractionGroupSequence = [group]
    ds.save_as(path, enforce_file_format=True)
    return ds


def _study_with_cube(tmp_path, beams, field):
    """A study whose plan carries ``beams`` and whose run holds one cube for ``field``."""
    study = tmp_path / "study"
    dicom = study / "dicom"
    run = study / "run001"
    dicom.mkdir(parents=True)
    run.mkdir()

    plan = _plan_with_beams(dicom / "RN.plan.dcm", beams)
    template = _template()
    ref_plan = Dataset()
    ref_plan.ReferencedSOPClassUID = plan.SOPClassUID
    ref_plan.ReferencedSOPInstanceUID = plan.SOPInstanceUID
    template.ReferencedRTPlanSequence = [ref_plan]
    template.save_as(dicom / "RD.dcm", enforce_file_format=True)

    cube = _template()
    cube.DoseGridScaling = "1"
    cube.PixelData = np.ones((1, 2, 2), dtype="<u4").tobytes()
    cube.save_as(run / f"topas_field{field}.dcm", enforce_file_format=True)
    (run / f"topas_field{field:02d}.txt").write_text(
        "# PARTICLE_SCALING: 1\n# REQUESTED_HISTORIES: 1\n")
    return run


def test_postprocess_survives_a_beam_whose_number_is_empty(tmp_path):
    """A present-but-empty BeamNumber reads back as None: `hasattr` sees it, `int()` cannot."""
    run = _study_with_cube(tmp_path, [3, None], field=3)

    rtdose.postprocess(run)          # must not raise TypeError

    out = pydicom.dcmread(run / "rtdose_field03.dcm", stop_before_pixels=True)
    ref = out.ReferencedRTPlanSequence[0].ReferencedFractionGroupSequence[0].ReferencedBeamSequence[0]
    assert ref.ReferencedBeamNumber == 3


def test_postprocess_omits_the_beam_reference_when_the_plan_has_no_such_beam(tmp_path):
    """Better no beam reference than one pointing at a beam that is not in the plan."""
    run = _study_with_cube(tmp_path, [4, 5], field=3)

    rtdose.postprocess(run)

    out = pydicom.dcmread(run / "rtdose_field03.dcm", stop_before_pixels=True)
    assert "ReferencedFractionGroupSequence" not in out.ReferencedRTPlanSequence[0]


def test_postprocess_stamps_beam_number_not_ordinal_position(tmp_path):
    """Field 3 is the *third* beam number, not the third beam in the sequence (issue #78)."""
    run = _study_with_cube(tmp_path, [4, 5, 3], field=3)

    rtdose.postprocess(run)

    out = pydicom.dcmread(run / "rtdose_field03.dcm", stop_before_pixels=True)
    ref = out.ReferencedRTPlanSequence[0].ReferencedFractionGroupSequence[0].ReferencedBeamSequence[0]
    assert ref.ReferencedBeamNumber == 3
