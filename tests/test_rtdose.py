import pytest
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


def test_postprocess_refuses_a_run_that_is_short_a_cube(tmp_path):
    """Summing what happens to be there would yield a plausible-looking dose that is not the
    whole course, which is worse than no export at all."""
    for n in (1, 2, 3):
        (tmp_path / f"topas_field{n:02d}.txt").write_text("input")
    (tmp_path / "topas_field02.dcm").write_bytes(b"cube")

    with pytest.raises(rtdose.RTDoseError, match=r"field\(s\) 1, 3 produced no dose cube"):
        rtdose.postprocess(tmp_path)


def test_ensure_dose_export_turns_the_refusal_into_a_warning(tmp_path):
    """The web layer must get a message, not a traceback."""
    (tmp_path / "topas_field01.txt").write_text("input")
    (tmp_path / "topas_field02.txt").write_text("input")
    (tmp_path / "topas_field01.dcm").write_bytes(b"cube")

    paths, warnings = rtdose.ensure_dose_export(tmp_path)

    assert paths == []
    assert len(warnings) == 1 and "produced no dose cube" in warnings[0]


def test_unpadded_cubes_from_dicomexport_before_2_still_match_their_fields(tmp_path):
    """dicomexport < 2.0 wrote topas_field2.dcm for topas_field02.txt, and runs made with it are
    still on disk.  The number is parsed, so both spellings key on the same field (#129)."""
    for n in (1, 2):
        (tmp_path / f"topas_field{n:02d}.txt").write_text("input")
        (tmp_path / f"topas_field{n}.dcm").write_bytes(b"cube")

    assert [n for n, _ in rtdose.field_cubes(tmp_path)] == [1, 2]
    assert rtdose.missing_field_cubes(tmp_path) == []


def test_zero_padded_cubes_are_matched_to_their_fields(tmp_path):
    """dicomexport >= 2.0 names the cube like its input: topas_field02.txt -> topas_field02.dcm."""
    for n in (1, 2, 10):
        (tmp_path / f"topas_field{n:02d}.txt").write_text("input")
        (tmp_path / f"topas_field{n:02d}.dcm").write_bytes(b"cube")

    assert [n for n, _ in rtdose.field_cubes(tmp_path)] == [1, 2, 10]
    assert rtdose.missing_field_cubes(tmp_path) == []


def test_missing_field_cubes_names_the_fields_that_did_not(tmp_path):
    """The inputs say which fields the run has; the cubes say which ones produced dose."""
    for n in (1, 2, 3):
        (tmp_path / f"topas_field{n:02d}.txt").write_text("input")
    (tmp_path / "topas_field02.dcm").write_bytes(b"cube")

    assert rtdose.missing_field_cubes(tmp_path) == [1, 3]


def test_missing_field_cubes_ignores_the_mask_prepass_input(tmp_path):
    """structure_mask_prepass.txt is a TOPAS input but not a field, and has no cube of its own."""
    (tmp_path / "topas_field01.txt").write_text("input")
    (tmp_path / "topas_field01.dcm").write_bytes(b"cube")
    (tmp_path / "structure_mask_prepass.txt").write_text("prepass")

    assert rtdose.missing_field_cubes(tmp_path) == []


def test_plan_dose_identity_is_none_until_the_export_has_been_built(tmp_path):
    """The export is on-demand, so an unexported run simply has no identity to show yet."""
    assert rtdose.plan_dose_identity(tmp_path) is None


def test_plan_dose_identity_reads_the_uids_off_the_written_file(tmp_path):
    """Read back, not predicted: the plan dose preserves the clinical RTDOSE's identity, so
    only the file itself can say which UIDs the imported object will carry."""
    ds = _template()
    ds.SeriesInstanceUID = "1.2.3.4"
    ds.save_as(tmp_path / rtdose.PLAN_DOSE_NAME, enforce_file_format=True)

    assert rtdose.plan_dose_identity(tmp_path) == {
        "series_uid": "1.2.3.4",
        "sop_uid": str(ds.SOPInstanceUID),
    }


def test_plan_dose_identity_survives_a_file_it_cannot_read(tmp_path):
    """Called on every render of a finished run, so a truncated export has to degrade to
    "nothing to show" rather than take the results page down."""
    (tmp_path / rtdose.PLAN_DOSE_NAME).write_bytes(b"not dicom at all")

    assert rtdose.plan_dose_identity(tmp_path) is None


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
    (tmp_path / "topas_field01.dcm").write_bytes(b"cube")
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
    (tmp_path / "topas_field01.dcm").write_bytes(b"cube")
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
    cube.save_as(run / f"topas_field{field:02d}.dcm", enforce_file_format=True)
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


def test_a_32_bit_cube_is_read_with_its_scaling_and_keeps_its_low_dose(tmp_path):
    """#127: at 16 bits TOPAS writes every voxel below ~1/65535 of the peak as zero, which is
    the out-of-field region.  dicomexport >= 2.0 asks for 32 bits.  A cube holding such a voxel
    must be read as pixel_array * DoseGridScaling and carried into the export.

    The template's scaling is set fine enough to hold it: the export keeps the clinical
    template's DoseGridScaling whenever the dose fits (_encode), so its resolution is the
    template's, and a coarse template rounds the low dose away again.  See the PR for #127.
    """
    run = _study_with_cube(tmp_path, [3], field=3)
    template_path = run.parent / "dicom" / "RD.dcm"
    template = pydicom.dcmread(template_path)
    template.DoseGridScaling = "1e-12"
    template.save_as(template_path, enforce_file_format=True)
    cube_path = run / "topas_field03.dcm"
    cube = pydicom.dcmread(cube_path)
    cube.DoseGridScaling = "1e-09"
    stored = np.array([[[1000, 4_000_000_000], [2_000_000_000, 3_000_000_000]]], dtype="<u4")
    cube.PixelData = stored.tobytes()
    cube.save_as(cube_path, enforce_file_format=True)
    assert stored.min() / stored.max() < 1 / 65535          # zero in any 16-bit cube

    rtdose.postprocess(run)

    out = pydicom.dcmread(run / "rtdose_field03.dcm")
    dose = out.pixel_array.astype(np.float64) * float(out.DoseGridScaling)
    assert dose.min() > 0
    assert dose.min() / dose.max() == pytest.approx(stored.min() / stored.max(), rel=1e-3)


# --- Choosing the clinical template for each exported dose (#125) -----------------------------
#
# An Eclipse export holds either a plan dose or one dose per field.  Each output is built from
# the matching one, and a plan dose built from a field dose must neither reference that beam
# nor carry that dose's identity, or Eclipse would take it for that field.

PLAN_UID = "1.2.826.0.1.3680043.8.498.1"


def _clinical(beam=None, instance=1, plan_uid=PLAN_UID, position=(0.0, 0.0, 0.0)) -> Dataset:
    """An Eclipse RTDOSE: a field dose for ``beam``, or the plan dose if ``beam`` is None."""
    ds = _template()
    ds.SeriesInstanceUID = generate_uid()
    ds.InstanceNumber = instance
    ds.ImagePositionPatient = list(position)
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.PixelSpacing = [1, 1]
    ds.GridFrameOffsetVector = [0]
    ref_plan = Dataset()
    ref_plan.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.481.8"
    ref_plan.ReferencedSOPInstanceUID = plan_uid
    group = Dataset()
    group.ReferencedFractionGroupNumber = 1
    if beam is not None:
        ds.DoseSummationType = "BEAM"
        ref_beam = Dataset()
        ref_beam.ReferencedBeamNumber = beam
        group.ReferencedBeamSequence = [ref_beam]
    ref_plan.ReferencedFractionGroupSequence = [group]
    ds.ReferencedRTPlanSequence = [ref_plan]
    return ds


def _study(tmp_path, monkeypatch, clinical, beams=(1, 2)):
    """A study with an RTPLAN, the given clinical RTDOSEs, and a finished run of ``beams``."""
    dicom, run = tmp_path / "dicom", tmp_path / "run"
    dicom.mkdir()
    run.mkdir()
    plan = Dataset()
    plan.file_meta = FileMetaDataset()
    plan.file_meta.MediaStorageSOPClassUID = pydicom.uid.RTIonPlanStorage
    plan.file_meta.MediaStorageSOPInstanceUID = pydicom.uid.UID(PLAN_UID)
    plan.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    plan.SOPClassUID = plan.file_meta.MediaStorageSOPClassUID
    plan.SOPInstanceUID = PLAN_UID
    group = Dataset()
    group.FractionGroupNumber = 1
    group.NumberOfFractionsPlanned = 1
    plan.FractionGroupSequence = [group]
    beam_items = []
    for number in beams:
        beam = Dataset()
        beam.BeamNumber = number
        beam.BeamName = f"F{number}"
        beam_items.append(beam)
    plan.IonBeamSequence = beam_items
    plan.save_as(dicom / "RN.plan.dcm", enforce_file_format=True)
    for i, ds in enumerate(clinical):
        ds.save_as(dicom / f"RD.{i}.dcm", enforce_file_format=True)
    for number in beams:
        (run / f"topas_field{number:02d}.txt").write_text("input")
        cube = _clinical()
        cube.PixelData = np.full((1, 2, 2), number, dtype="<u4").tobytes()
        cube.save_as(run / f"topas_field{number:02d}.dcm", enforce_file_format=True)
    monkeypatch.setattr(rtdose, "_plan_scale", lambda run_dir, field_number: 1.0)
    return run


def _exported(run):
    rtdose.postprocess(run)
    fields = {int(p.stem[-2:]): pydicom.dcmread(p) for p in sorted(run.glob("rtdose_field*.dcm"))}
    return fields, pydicom.dcmread(run / rtdose.PLAN_DOSE_NAME)


def test_field_doses_only_plan_dose_neither_references_a_beam_nor_borrows_an_identity(tmp_path, monkeypatch):
    """The bug #125 exposed: the plan dose was built from Eclipse's field-1 dose and kept both
    its beam reference and its SOP Instance UID."""
    clinical = [_clinical(beam=1, instance=11), _clinical(beam=2, instance=12)]
    fields, plan = _exported(_study(tmp_path, monkeypatch, clinical))

    assert plan.DoseSummationType == "PLAN"
    assert rtdose._beam_references(plan) == ()
    assert plan.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID == PLAN_UID
    assert plan.SOPInstanceUID not in {ds.SOPInstanceUID for ds in clinical}
    assert plan.SeriesInstanceUID not in {ds.SeriesInstanceUID for ds in clinical}
    assert plan.SeriesInstanceUID == fields[1].SeriesInstanceUID
    assert "PregDos" in plan.DoseComment
    # The sum of both fields, to within half the template's dose step.
    np.testing.assert_allclose(plan.pixel_array * float(plan.DoseGridScaling), 3 * 3e-05 * 1.1, atol=1.5e-05)


def test_each_field_dose_is_built_from_eclipses_dose_for_that_beam(tmp_path, monkeypatch):
    clinical = [_clinical(beam=2, instance=12), _clinical(beam=1, instance=11)]
    fields, _ = _exported(_study(tmp_path, monkeypatch, clinical))

    for number in (1, 2):
        assert fields[number].InstanceNumber == 10 + number
        assert rtdose._beam_references(fields[number]) == (number,)
        assert fields[number].DoseSummationType == "BEAM"


def test_plan_dose_study_keeps_eclipses_plan_dose_identity(tmp_path, monkeypatch):
    """Unchanged from before #125: built from Eclipse's own plan dose, the plan dose keeps it."""
    clinical = [_clinical(instance=10)]
    fields, plan = _exported(_study(tmp_path, monkeypatch, clinical))

    assert plan.SOPInstanceUID == clinical[0].SOPInstanceUID
    assert plan.SeriesInstanceUID == clinical[0].SeriesInstanceUID
    assert rtdose._beam_references(plan) == ()
    for number in (1, 2):
        assert rtdose._beam_references(fields[number]) == (number,)
        assert fields[number].SOPInstanceUID != clinical[0].SOPInstanceUID


def test_with_both_the_plan_dose_comes_from_the_plan_dose_and_fields_from_field_doses(tmp_path, monkeypatch):
    clinical = [_clinical(beam=1, instance=11), _clinical(instance=10), _clinical(beam=2, instance=12)]
    fields, plan = _exported(_study(tmp_path, monkeypatch, clinical))

    assert plan.InstanceNumber == 10 and plan.SOPInstanceUID == clinical[1].SOPInstanceUID
    assert [fields[n].InstanceNumber for n in (1, 2)] == [11, 12]


def test_doses_of_another_plan_are_not_templates(tmp_path, monkeypatch):
    clinical = [_clinical(beam=1, instance=99, plan_uid="1.2.3.999"), _clinical(beam=1, instance=11)]
    fields, plan = _exported(_study(tmp_path, monkeypatch, clinical, beams=(1,)))

    assert fields[1].InstanceNumber == 11
    assert plan.InstanceNumber == 11


def test_a_dose_on_another_grid_is_not_a_template(tmp_path, monkeypatch):
    clinical = [_clinical(beam=1, instance=11, position=(5.0, 0.0, 0.0)), _clinical(beam=2, instance=12)]
    fields, plan = _exported(_study(tmp_path, monkeypatch, clinical))

    assert fields[1].InstanceNumber == 12            # borrowed, but still beam 1
    assert rtdose._beam_references(fields[1]) == (1,)
    assert plan.InstanceNumber == 12


def test_no_dose_on_the_cubes_grid_is_refused(tmp_path, monkeypatch):
    clinical = [_clinical(beam=1, position=(5.0, 0.0, 0.0))]
    run = _study(tmp_path, monkeypatch, clinical, beams=(1,))

    with pytest.raises(rtdose.RTDoseError, match="dose grid of field 1"):
        rtdose.postprocess(run)


def test_grids_match_despite_topas_shorter_positions():
    """TOPAS writes -149.707031 where Eclipse wrote -149.7070313: the same grid."""
    eclipse = _clinical(position=(-149.7070313, -261.7929688, -139.0))
    topas = _clinical(position=(-149.707031, -261.793, -139.0))

    assert rtdose._same_grid(eclipse, topas)
