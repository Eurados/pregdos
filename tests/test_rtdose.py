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


def test_plan_derive_can_preserve_eclipse_identity():
    template = _template()
    template.SeriesInstanceUID = "1.2.3.4"
    original_sop = template.SOPInstanceUID
    original_media_sop = template.file_meta.MediaStorageSOPInstanceUID

    ds = rtdose._derive(
        template,
        series_uid=generate_uid(),
        summation="PLAN",
        what="plan",
        plan=Dataset(),
        preserve_identity=True,
    )

    assert ds.SOPInstanceUID == original_sop
    assert ds.file_meta.MediaStorageSOPInstanceUID == original_media_sop
    assert ds.SeriesInstanceUID == "1.2.3.4"
    assert "DoseComment" not in ds


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
    plan.SeriesInstanceUID = generate_uid()
    plan.RTPlanLabel = "Brain_fin2"
    plan.ApprovalStatus = "APPROVED"
    plan.ReviewDate = "20240227"
    plan.ReviewTime = "120000"
    plan.ReviewerName = "Reviewer^Clinical"
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


# --- The Eclipse import bundle: an RTPLAN copy and every dose referencing it (#125) -----------
#
# Eclipse's DICOM Import refuses the clinical RTPLAN ("the plan already exists"), so the bundle
# carries a copy under a new identity, and the doses reference the copy.  The loose files keep
# referencing the clinical plan, for Import Dosegrid, which takes them as they are.

def _bundle(run):
    with zipfile.ZipFile(run / rtdose.IMPORT_BUNDLE_NAME) as zf:
        return {name: pydicom.dcmread(zf.open(name)) for name in zf.namelist()}


def test_bundle_holds_a_plan_copy_and_every_dose_referencing_it(tmp_path, monkeypatch):
    clinical = [_clinical(beam=1), _clinical(beam=2)]
    run = _study(tmp_path, monkeypatch, clinical)
    loose_fields, loose_plan = _exported(run)
    bundle = _bundle(run)

    assert sorted(bundle) == sorted([rtdose.PLAN_COPY_NAME, "rtdose_field01.dcm",
                                     "rtdose_field02.dcm", rtdose.PLAN_DOSE_NAME])
    copy_of_plan = bundle[rtdose.PLAN_COPY_NAME]
    assert copy_of_plan.SOPInstanceUID != PLAN_UID
    assert copy_of_plan.file_meta.MediaStorageSOPInstanceUID == copy_of_plan.SOPInstanceUID
    assert [int(b.BeamNumber) for b in copy_of_plan.IonBeamSequence] == [1, 2]

    doses = [bundle[name] for name in sorted(bundle) if name.startswith("rtdose_")]
    assert {ds.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID for ds in doses} == {
        copy_of_plan.SOPInstanceUID}
    assert len({ds.SeriesInstanceUID for ds in doses}) == 1
    assert rtdose._beam_references(bundle["rtdose_field01.dcm"]) == (1,)
    assert rtdose._beam_references(bundle["rtdose_field02.dcm"]) == (2,)
    assert rtdose._beam_references(bundle[rtdose.PLAN_DOSE_NAME]) == ()
    assert bundle[rtdose.PLAN_DOSE_NAME].DoseSummationType == "PLAN"

    # New objects, not the loose ones again: Eclipse would take them for the same doses.
    loose = [*loose_fields.values(), loose_plan]
    assert not {ds.SOPInstanceUID for ds in doses} & {ds.SOPInstanceUID for ds in loose}
    assert {ds.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID for ds in loose} == {PLAN_UID}
    np.testing.assert_array_equal(bundle[rtdose.PLAN_DOSE_NAME].pixel_array, loose_plan.pixel_array)


def test_plan_copy_is_a_new_unapproved_plan_with_its_own_id(tmp_path, monkeypatch):
    run = _study(tmp_path, monkeypatch, [_clinical(beam=1), _clinical(beam=2)])
    rtdose.postprocess(run)
    clinical_plan = pydicom.dcmread(tmp_path / "dicom" / "RN.plan.dcm")
    copy_of_plan = _bundle(run)[rtdose.PLAN_COPY_NAME]

    assert copy_of_plan.RTPlanLabel == "Brain_fin2_MC"
    assert copy_of_plan.SeriesInstanceUID != clinical_plan.SeriesInstanceUID
    assert copy_of_plan.ApprovalStatus == "UNAPPROVED"
    for keyword in ("ReviewDate", "ReviewTime", "ReviewerName"):
        assert keyword not in copy_of_plan


def test_plan_copy_id_stays_within_sixteen_characters():
    plan = Dataset()
    plan.file_meta = FileMetaDataset()
    plan.RTPlanLabel = "SixteenCharsLong"

    assert rtdose.plan_copy(plan).RTPlanLabel == "SixteenCharsL_MC"


def test_bundle_plan_dose_is_new_even_where_the_loose_one_keeps_eclipses_identity(tmp_path, monkeypatch):
    clinical = [_clinical()]
    run = _study(tmp_path, monkeypatch, clinical)
    _, loose_plan = _exported(run)
    bundled_plan = _bundle(run)[rtdose.PLAN_DOSE_NAME]

    assert loose_plan.SOPInstanceUID == clinical[0].SOPInstanceUID
    assert bundled_plan.SOPInstanceUID != clinical[0].SOPInstanceUID
    assert "PregDos" in bundled_plan.DoseComment


def test_an_export_from_before_the_full_bundle_is_built_again(tmp_path, monkeypatch):
    """A run exported before #125 has the plan dose and the old two-file bundle; asking for
    the export again must give it the full bundle, and not leave the old one behind."""
    run = _study(tmp_path, monkeypatch, [_clinical(beam=1), _clinical(beam=2)])
    (run / rtdose.PLAN_DOSE_NAME).write_bytes(b"old plan dose")
    (run / "rtdose_plan_eclipse_import.zip").write_bytes(b"PK old bundle")

    paths, warnings = rtdose.ensure_dose_export(run)

    assert warnings == []
    assert run / rtdose.IMPORT_BUNDLE_NAME in paths
    assert not (run / "rtdose_plan_eclipse_import.zip").exists()
    assert rtdose.PLAN_COPY_NAME in _bundle(run)


def test_a_failed_export_leaves_no_bundle(tmp_path, monkeypatch):
    """The bundle's presence is what says the export finished, so a half-written one must not
    remain under its name."""
    run = _study(tmp_path, monkeypatch, [_clinical(beam=1), _clinical(beam=2)])

    def broken(*args, **kwargs):
        raise rtdose.RTDoseError("cube unreadable")
    monkeypatch.setattr(rtdose, "_write_doses", broken)

    with pytest.raises(rtdose.RTDoseError):
        rtdose.postprocess(run)
    assert not (run / rtdose.IMPORT_BUNDLE_NAME).exists()
    assert not list(run.glob("*.partial"))


def test_import_identity_names_what_the_import_creates(tmp_path, monkeypatch):
    run = _study(tmp_path, monkeypatch, [_clinical(beam=1), _clinical(beam=2)])
    assert rtdose.import_identity(run) is None          # built on first download only

    rtdose.postprocess(run)
    bundle = _bundle(run)

    assert rtdose.import_identity(run) == {
        "plan_label": "Brain_fin2_MC",
        "plan_uid": str(bundle[rtdose.PLAN_COPY_NAME].SOPInstanceUID),
        "series_uid": str(bundle[rtdose.PLAN_DOSE_NAME].SeriesInstanceUID),
    }


def test_import_identity_survives_a_bundle_it_cannot_read(tmp_path):
    """Called on every render of a finished run, so a truncated export has to degrade to
    "nothing to show" rather than take the results page down."""
    (tmp_path / rtdose.IMPORT_BUNDLE_NAME).write_bytes(b"not a zip at all")

    assert rtdose.import_identity(tmp_path) is None


def test_every_dose_shares_one_series_named_for_all_and_says_which_it_is(tmp_path, monkeypatch):
    """A TPS shows a series by one description; it named the series after field 1 (#125)."""
    run = _study(tmp_path, monkeypatch, [_clinical(beam=1), _clinical(beam=2)])
    fields, plan = _exported(run)
    bundle = _bundle(run)

    for doses in ([fields[1], fields[2], plan],
                  [bundle["rtdose_field01.dcm"], bundle["rtdose_field02.dcm"], bundle[rtdose.PLAN_DOSE_NAME]]):
        assert {ds.SeriesDescription for ds in doses} == {rtdose.DOSE_SERIES_DESCRIPTION}
        assert [ds.DoseComment.split(":")[0] for ds in doses] == [
            "PregDos/TOPAS field 1", "PregDos/TOPAS field 2", "PregDos/TOPAS plan"]


def test_dose_comment_fits_dicom_lo():
    assert len(rtdose._dose_comment("field 99")) <= 64
