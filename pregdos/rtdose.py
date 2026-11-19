"""Turn the raw TOPAS dose cubes into RTDOSE files a TPS will import (issue #62).

The in-field scorer dicomexport writes is a valid enough RTDOSE for TOPAS, but not for a
clinical TPS: Type-1 attributes are missing (Eclipse rejects the file on ``DoseSummationType``
(3004,000A), then on ``DoseType`` (3004,0004)), and the dose is raw *simulation* dose rather than
what the plan delivers.

**Bit depth is decided before PregDos sees the cube, and nothing here can repair it.**  TOPAS
scales each cube to its own peak, so at 16 bits it writes every voxel below about 1/65535 of the
field's peak as zero: on a grid whose peak is the target dose, that is the out-of-field (fetal)
region.  The zeros are in the file, and re-encoding them at any depth keeps them zeros.
dicomexport >= 2.0, the minimum PregDos accepts, has TOPAS write 32 bits per pixel instead
(#127), which keeps that region.  The cube is read as ``pixel_array * DoseGridScaling``
whatever its depth, and written at the depth of the clinical template, re-scaled if the dose
would not fit (:func:`_encode`).

Rather than repair the TOPAS cube tag by tag, **each output is built from the study's own
clinical RTDOSE as a template**, with only the dose grid replaced: a field dose from Eclipse's
dose for that beam, the plan dose from Eclipse's plan dose, each when the study has one (#125).  Everything a TPS is fussy
about -- geometry, ``FrameOfReferenceUID``, patient/study identity, and above all the
``ReferencedRTPlanSequence`` -- is then byte-for-byte what the TPS itself wrote, so it cannot
disagree with us about it.  Only the identity of the new object (SOP/Series UID) and the dose
itself are ours.

For a run with N fields this writes **N+1** files: one ``BEAM`` dose per field, plus the summed
``PLAN`` dose -- the latter being what clinical staff actually review for QA.

**These files carry Gy(RBE), not physical dose.**  A clinical proton RTDOSE holds
``Gy(RBE) = physical x 1.1`` while still declaring ``DoseType = PHYSICAL`` -- the long-standing
TPS convention.  Exporting true physical dose would display ~9% below the clinical plan purely
from the convention mismatch, which is a trap for visual QA.  So the export follows its
destination: the constant RBE is applied here and said so in ``DoseComment``.  Everything
PregDos shows in its own web UI stays **physical**.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, generate_uid

from . import results

# The in-field cube is ``topas_field<nn>.dcm``, named like the input it came from
# (``topas_field<nn>.txt``).  dicomexport before 2.0 wrote the cube unpadded
# (``topas_field<n>.dcm``), and runs made with it still exist on disk.  Both regexes parse the
# number, so either spelling is keyed on the same field number.
_CUBE_RE = re.compile(r"^topas_field(\d+)\.dcm$")
# The generated inputs, which say which fields the run *has*.
_INPUT_RE = re.compile(r"^topas_field(\d+)\.txt$")

FIELD_DOSE_PREFIX = "rtdose_field"
PLAN_DOSE_NAME = "rtdose_plan.dcm"
IMPORT_BUNDLE_NAME = "rtdose_eclipse_import.zip"
#: The bundle's name before #125, when it held the original RTPLAN and the plan dose only.
_OLD_BUNDLE_NAME = "rtdose_plan_eclipse_import.zip"
#: The RTPLAN copy inside the bundle, which every dose in it references.
PLAN_COPY_NAME = "rtplan_pregdos.dcm"
#: Appended to the copy's Plan ID (``RTPlanLabel``, at most 16 characters), so Eclipse does not
#: take it for the clinical plan.
PLAN_LABEL_SUFFIX = "_MC"
#: The SeriesDescription of every dose PregDos makes.  One series holds the field doses and
#: the plan dose, and a TPS shows the series by one description, so it names none of them;
#: each dose says which it is in its DoseComment (:func:`_dose_comment`).
DOSE_SERIES_DESCRIPTION = "PregDos TOPAS doses"


def _dose_comment(what: str) -> str:
    """``PregDos/TOPAS field 1: Gy(RBE)=physical x1.1, whole course``, within LO's 64 characters."""
    return f"PregDos/TOPAS {what}: Gy(RBE)=physical x1.1, whole course"[:64]

#: Constant proton RBE.  Applied *only* on the way out to a TPS, so the cube overlays the
#: clinical plan (which is Gy(RBE)).  PregDos itself never reports RBE-weighted dose.
PROTON_RBE = 1.1

_UINT32_MAX = 2**32 - 1


class RTDoseError(Exception):
    """The TOPAS dose cubes could not be turned into importable RTDOSE files."""


def _study_dicom(run_dir: Path) -> Path:
    return run_dir.parent / "dicom"


def _rtplan_path(run_dir: Path) -> Optional[Path]:
    """The study's RTPLAN, which the run's TOPAS inputs were generated from."""
    for pattern in ("RN*.dcm", "RP*.dcm"):
        found = sorted(_study_dicom(run_dir).glob(pattern))
        if found:
            return found[0]
    return None


def _beam_references(ds: Dataset) -> Tuple[int, ...]:
    """The beam numbers an RTDOSE references: one for a field dose, none for a plan dose."""
    beams = []
    for ref_plan in getattr(ds, "ReferencedRTPlanSequence", None) or []:
        for group in getattr(ref_plan, "ReferencedFractionGroupSequence", None) or []:
            for beam in getattr(group, "ReferencedBeamSequence", None) or []:
                if getattr(beam, "ReferencedBeamNumber", None) is not None:
                    beams.append(int(beam.ReferencedBeamNumber))
    return tuple(beams)


def _referenced_plan_uid(ds: Dataset) -> Optional[str]:
    refs = getattr(ds, "ReferencedRTPlanSequence", None) or []
    uid = getattr(refs[0], "ReferencedSOPInstanceUID", None) if refs else None
    return str(uid) if uid else None


def _clinical_rtdoses(run_dir: Path, plan_uid: Optional[str]) -> List[Dataset]:
    """The study's clinical RTDOSEs, the templates for everything we write, pixels included.

    An Eclipse export holds a plan dose or one dose per field, rarely both (#125).  Doses
    referencing another plan are left out, unless none references this one.
    """
    doses: List[Dataset] = [pydicom.dcmread(path) for path in sorted(_study_dicom(run_dir).glob("RD*.dcm"))]
    this_plan = [ds for ds in doses if plan_uid is not None and _referenced_plan_uid(ds) == plan_uid]
    return this_plan or doses


_GEOMETRY = ("Rows", "Columns", "NumberOfFrames", "ImagePositionPatient", "ImageOrientationPatient",
             "PixelSpacing", "GridFrameOffsetVector")


def _same_grid(a: Dataset, b: Dataset) -> bool:
    """Whether two RTDOSEs lay their voxels out alike, to 0.01 mm.

    TOPAS writes positions with fewer digits than Eclipse, hence the tolerance.
    """
    for keyword in _GEOMETRY:
        x, y = getattr(a, keyword, None), getattr(b, keyword, None)
        if x is None or y is None:
            if x is not y:
                return False
            continue
        x, y = np.atleast_1d(np.asarray(x, dtype=float)), np.atleast_1d(np.asarray(y, dtype=float))
        if x.shape != y.shape or not np.allclose(x, y, rtol=0, atol=0.01):
            return False
    return True


def _field_template(doses: List[Dataset], field_number: int, cube: Dataset) -> Dataset:
    """Eclipse's dose for this beam; else its plan dose; else any dose on the cube's grid.

    :func:`_derive` rewrites the beam reference, so a borrowed template still says which field.
    """
    on_grid = [ds for ds in doses if _same_grid(ds, cube)]
    for wanted in ((field_number,), ()):
        for ds in on_grid:
            if _beam_references(ds) == wanted:
                return ds
    if on_grid:
        return on_grid[0]
    raise RTDoseError(
        f"no clinical RTDOSE has the dose grid of field {field_number}'s TOPAS cube; "
        f"the TOPAS scorer must clone it")


def _plan_template(doses: List[Dataset], cube: Dataset) -> Tuple[Dataset, bool]:
    """``(template, is_plan_dose)``: Eclipse's plan dose if the study has one on the cube's
    grid, else a field dose, whose beam reference :func:`_derive` then drops."""
    on_grid = [ds for ds in doses if _same_grid(ds, cube)]
    for ds in on_grid:
        if not _beam_references(ds):
            return ds, True
    if on_grid:
        return on_grid[0], False
    raise RTDoseError("no clinical RTDOSE has the dose grid of the TOPAS cubes; "
                      "the TOPAS scorer must clone it")


def field_cubes(run_dir: str | Path) -> list[tuple[int, Path]]:
    """``(field_number, cube_path)`` for every TOPAS dose cube in the run, in field order."""
    run_dir = Path(run_dir)
    out = []
    for path in run_dir.glob("topas_field*.dcm"):
        match = _CUBE_RE.match(path.name)
        if match:
            out.append((int(match.group(1)), path))
    return sorted(out)


def missing_field_cubes(run_dir: str | Path) -> list[int]:
    """Field numbers whose TOPAS input exists but whose dose cube does not.

    A run reports COMPLETED once every field has *exited cleanly*, which is not the same as
    every field having written its cube: the file can be removed afterwards, or a write can
    fail without a non-zero exit.  :func:`postprocess` then sums whichever cubes it finds and
    the PLAN dose silently omits those fields -- while still describing itself as the whole
    course, which is what makes it dangerous rather than merely incomplete.

    The generated inputs are the authority for which fields the run has; :func:`_plan_scale`
    already treats ``topas_field<nn>.txt`` that way, so this introduces no second convention.
    """
    run_dir = Path(run_dir)
    expected = {
        int(match.group(1))
        for path in run_dir.glob("topas_field*.txt")
        if (match := _INPUT_RE.match(path.name))
    }
    return sorted(expected - {number for number, _ in field_cubes(run_dir)})


def _plan_scale(run_dir: Path, field_number: int) -> float:
    """Factor converting a field's scored dose into the dose the plan delivers per fraction."""
    topas_input = run_dir / f"topas_field{field_number:02d}.txt"
    scaling = results.parse_plan_scaling(topas_input)
    if scaling is None:
        raise RTDoseError(f"no plan scaling for field {field_number} ({topas_input.name})")
    return scaling.factor


def _max_stored_value(ds: Dataset) -> int:
    if int(ds.PixelRepresentation) != 0:
        raise RTDoseError("signed RTDOSE pixel data is not supported")
    return 2 ** int(ds.BitsStored) - 1


def _pixel_dtype(ds: Dataset) -> np.dtype:
    bits = int(ds.BitsAllocated)
    if bits == 16:
        return np.dtype("<u2")
    if bits == 32:
        return np.dtype("<u4")
    raise RTDoseError(f"unsupported RTDOSE pixel depth: {bits} bits")


def _format_ds(value: float) -> str:
    """Return a DICOM DS string, whose value representation is limited to 16 chars."""
    if value == 0:
        return "0"
    for precision in range(10, 1, -1):
        text = f"{value:.{precision}g}"
        if len(text) <= 16 and float(text) >= value:
            return text
    text = f"{value:.8e}"
    if float(text) < value:
        text = f"{np.nextafter(value, np.inf):.8e}"
    if len(text) > 16:
        raise RTDoseError(f"cannot encode DoseGridScaling {value} as a DICOM DS value")
    return text


def _encode(ds: Dataset, dose_gy: np.ndarray) -> None:
    """Replace the dose grid while preserving the template pixel format when possible."""
    peak = float(dose_gy.max())
    max_stored = _max_stored_value(ds)
    dtype = _pixel_dtype(ds)

    scaling = float(ds.DoseGridScaling)
    if peak > 0 and np.rint(peak / scaling) > max_stored:
        # Keep the template bit depth, but choose the smallest valid scaling that fits.
        scaling = float(_format_ds((peak / max_stored) * (1.0 + 1e-8)))
        ds.DoseGridScaling = _format_ds(scaling)

    grid = (
        np.rint(dose_gy / scaling).clip(0, max_stored).astype(dtype)
        if peak > 0
        else np.zeros(dose_gy.shape, dtype)
    )
    ds.PixelData = grid.tobytes()
    ds["PixelData"].VR = "OW" if int(ds.BitsAllocated) > 8 else "OB"


def _derive(template: Dataset, series_uid: str, summation: str, what: str,
            plan: Dataset, beam_number: Optional[int] = None, preserve_identity: bool = False) -> Dataset:
    """Copy the clinical RTDOSE and re-identify it as *our* dose, keeping its plan reference.

    The ``ReferencedRTPlanSequence`` is inherited, so the dose points at exactly the plan the
    TPS already associates with the clinical dose.  Only its beam reference is ours: a BEAM
    dose names its own beam, whichever dose it was built from, and any other dose names none.
    A PLAN dose built from Eclipse's field-1 dose would otherwise still claim to be beam 1.

    ``what`` ("field 1", "plan") goes into the DoseComment.  A dose that keeps Eclipse's
    identity keeps its description and comment too.
    """
    ds = copy.deepcopy(template)
    ds.DoseUnits = "GY"
    # "PHYSICAL" is what a proton TPS writes even when the values are Gy(RBE); we follow that
    # convention so the cube lines up with the clinical plan rather than reading 10% low.
    ds.DoseType = "PHYSICAL"
    ds.DoseSummationType = summation

    if beam_number is not None:
        ref_plan = ds.ReferencedRTPlanSequence[0]
        ref_beam = Dataset()
        ref_beam.ReferencedBeamNumber = beam_number
        ref_group = Dataset()
        ref_group.ReferencedFractionGroupNumber = plan.FractionGroupSequence[0].FractionGroupNumber
        ref_group.ReferencedBeamSequence = [ref_beam]
        ref_plan.ReferencedFractionGroupSequence = [ref_group]
    else:
        for ref_plan in getattr(ds, "ReferencedRTPlanSequence", None) or []:
            for group in getattr(ref_plan, "ReferencedFractionGroupSequence", None) or []:
                if "ReferencedBeamSequence" in group:
                    del group.ReferencedBeamSequence

    if not preserve_identity:
        # Field-dose objects need their own identity.  A plan dose built from Eclipse's own
        # plan dose keeps that RD's identity, because Eclipse is strict about reconnecting
        # cloned doses; one built from a field dose must not, or it would pose as that field.
        ds.SOPInstanceUID = generate_uid()
        ds.SeriesInstanceUID = series_uid
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    transfer_syntax = ds.file_meta.get("TransferSyntaxUID", ExplicitVRLittleEndian)
    ds.is_little_endian = True
    ds.is_implicit_VR = transfer_syntax == ImplicitVRLittleEndian
    if not preserve_identity:
        ds.SeriesDescription = DOSE_SERIES_DESCRIPTION
        ds.DoseComment = _dose_comment(what)
    return ds


def plan_copy(plan: Dataset) -> Dataset:
    """The RTPLAN as a new plan, for Eclipse's DICOM Import of the bundle (#125).

    Eclipse refuses the clinical RTPLAN itself: "the plan already exists".  The copy gets new
    SOP Instance and Series UIDs and a Plan ID ending in :data:`PLAN_LABEL_SUFFIX`, in case
    Eclipse matches on the ID rather than the UID.  It is set UNAPPROVED, so a Monte Carlo
    recalculation never shows up as an approved plan; the review attributes, which only an
    approved or reviewed plan carries, go with it.  Beams, fraction groups and references to
    the CT and structure set are kept, so the doses attach to the same beams.
    """
    ds = copy.deepcopy(plan)
    ds.SOPInstanceUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    ds.SeriesInstanceUID = generate_uid()
    label = str(getattr(plan, "RTPlanLabel", "") or "PLAN")
    ds.RTPlanLabel = label[:16 - len(PLAN_LABEL_SUFFIX)] + PLAN_LABEL_SUFFIX
    ds.SeriesDescription = "PregDos TOPAS plan copy"
    ds.ApprovalStatus = "UNAPPROVED"
    for keyword in ("ReviewDate", "ReviewTime", "ReviewerName"):
        if keyword in ds:
            delattr(ds, keyword)
    now = datetime.now()
    ds.InstanceCreationDate = now.strftime("%Y%m%d")
    ds.InstanceCreationTime = now.strftime("%H%M%S")
    return ds


def _for_plan_copy(ds: Dataset, copy_of_plan: Dataset, series_uid: str, what: str) -> Dataset:
    """Turn an exported dose into the bundle's: a new object referencing the plan copy.

    Mutates ``ds`` -- it has been written as a loose file already -- rather than copying a
    cube of tens of MB.
    """
    ds.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID = copy_of_plan.SOPInstanceUID
    ds.SOPInstanceUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    ds.SeriesInstanceUID = series_uid
    ds.SeriesDescription = DOSE_SERIES_DESCRIPTION
    ds.DoseComment = _dose_comment(what)
    return ds


def _add_to_bundle(zf: zipfile.ZipFile, name: str, ds: Dataset) -> None:
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    zf.writestr(name, buffer.getvalue())


def import_identity(run_dir: str | Path) -> Optional[dict]:
    """What the bundle's import creates in the TPS, or None if the bundle is not built.

    ``{"plan_label", "plan_uid", "series_uid"}``: the plan copy's Plan ID and SOP Instance UID,
    and the Series UID its doses share -- what someone needs to find them again after import,
    so the results page shows them next to the download.  Read back from the bundle, since
    the UIDs are minted when it is built.

    Never raises and never builds anything.  It is called at render time, where the export may
    simply not exist yet -- it is generated on first download -- and where a truncated or
    unreadable file has to read as "no identity to show" rather than as a 500.
    """
    path = Path(run_dir) / IMPORT_BUNDLE_NAME
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open(PLAN_COPY_NAME) as f:
                plan = pydicom.dcmread(f)
            with zf.open(PLAN_DOSE_NAME) as f:
                dose = pydicom.dcmread(f, stop_before_pixels=True)
        return {"plan_label": str(plan.RTPlanLabel), "plan_uid": str(plan.SOPInstanceUID),
                "series_uid": str(dose.SeriesInstanceUID)}
    except Exception:  # noqa: BLE001 - absent, truncated or not DICOM all mean the same here
        return None


def exported_files(run_dir: str | Path) -> List[Path]:
    """The importable RTDOSE files already generated for this run, in import order."""
    run_dir = Path(run_dir)
    out = sorted(run_dir.glob(f"{FIELD_DOSE_PREFIX}*.dcm"))
    for name in (PLAN_DOSE_NAME, IMPORT_BUNDLE_NAME):
        if (run_dir / name).is_file():
            out.append(run_dir / name)
    return out


def ensure_dose_export(run_dir: str | Path) -> Tuple[List[Path], List[str]]:
    """Generate the importable RTDOSE files once, returning ``(paths, warnings)``.

    Idempotent -- the files are built on first request and reused after -- and it never raises:
    a study without a clinical RTDOSE to build from, or an unreadable cube, must degrade to a
    message on the page rather than a 500.

    **Only call this on a finished run.**  ``postprocess`` sums whichever cubes it finds, so on
    a half-finished run it would happily write a PLAN dose that silently omits the fields that
    have not run yet -- which is exactly the sort of plausible-looking wrong dose this project
    exists to avoid.
    """
    run_dir = Path(run_dir)

    # The bundle is written last, under a temporary name, so its presence means the whole
    # export finished.  Anything less -- an interrupted export, or one from before #125 with
    # the old bundle -- is built again.
    if (run_dir / PLAN_DOSE_NAME).is_file() and (run_dir / IMPORT_BUNDLE_NAME).is_file():
        return exported_files(run_dir), []

    if not field_cubes(run_dir):
        return [], []
    try:
        return postprocess(run_dir), []
    except Exception as exc:  # noqa: BLE001 - a bad DICOM must not take down the results page
        return [], [str(exc)]


def postprocess(run_dir: str | Path) -> List[Path]:
    """Write importable per-field (BEAM) and summed (PLAN) RTDOSE files for a finished run.

    Returns the paths written, with the plan dose and Eclipse import bundle last.  The raw
    TOPAS cubes are left untouched.

    Two sets are written.  The loose ``rtdose_*.dcm`` files reference the clinical RTPLAN, for
    Eclipse's Import Dosegrid, which takes them as they are.  The bundle, for its DICOM
    Import, holds a copy of the RTPLAN under a new identity (:func:`plan_copy`) and the same
    doses as new objects referencing it, each field dose its own beam (#125).
    """
    run_dir = Path(run_dir)
    cubes = field_cubes(run_dir)
    if not cubes:
        return []

    # Refuse rather than sum what happens to be there.  A PLAN dose short of a field is not a
    # smaller dose, it is a wrong one that describes itself as the whole course -- and it looks
    # entirely plausible in a TPS.  Callers turn this into a message; see ensure_dose_export.
    if missing := missing_field_cubes(run_dir):
        fields = ", ".join(str(number) for number in missing)
        raise RTDoseError(
            f"field(s) {fields} produced no dose cube, so the plan dose would be the sum of the "
            f"remaining fields only while still describing itself as the whole course. Rerun the "
            f"missing field(s) before exporting."
        )

    plan_path = _rtplan_path(run_dir)
    if plan_path is None:
        raise RTDoseError("study needs both an RTPLAN and a clinical RTDOSE to build from")
    plan = pydicom.dcmread(plan_path, stop_before_pixels=True)
    templates = _clinical_rtdoses(run_dir, str(getattr(plan, "SOPInstanceUID", "")) or None)
    if not templates:
        raise RTDoseError("study needs both an RTPLAN and a clinical RTDOSE to build from")

    fractions = results.planned_fractions(plan_path) or 1
    # dicomexport >= 1.5.0 names each cube after the DICOM BeamNumber, so a field number is
    # only usable as a beam reference if the plan really has a beam with that number.  An
    # element that is present but empty reads back as None, which `hasattr` would wave through
    # and `int()` would then choke on -- the same guard `results.beam_names` already applies.
    plan_beams = getattr(plan, "IonBeamSequence", None) or getattr(plan, "BeamSequence", None) or []
    beam_numbers = {
        int(b.BeamNumber)
        for b in plan_beams
        if getattr(b, "BeamNumber", None) is not None
    }

    series_uid = generate_uid()
    written: List[Path] = []

    copy_of_plan = plan_copy(pydicom.dcmread(plan_path))
    bundle_series_uid = generate_uid()
    bundle_path = run_dir / IMPORT_BUNDLE_NAME
    partial_bundle = run_dir / (IMPORT_BUNDLE_NAME + ".partial")
    bundle = zipfile.ZipFile(partial_bundle, "w", compression=zipfile.ZIP_DEFLATED)
    try:
        _add_to_bundle(bundle, PLAN_COPY_NAME, copy_of_plan)
        written += _write_doses(run_dir, cubes, templates, plan, beam_numbers, fractions,
                                series_uid, copy_of_plan, bundle_series_uid, bundle)
        bundle.close()
        partial_bundle.replace(bundle_path)
    finally:
        bundle.close()
        partial_bundle.unlink(missing_ok=True)
    (run_dir / _OLD_BUNDLE_NAME).unlink(missing_ok=True)
    written.append(bundle_path)
    return written


def _write_doses(run_dir: Path, cubes: List[Tuple[int, Path]], templates: List[Dataset],
                 plan: Dataset, beam_numbers: set, fractions: int, series_uid: str,
                 copy_of_plan: Dataset, bundle_series_uid: str, bundle: zipfile.ZipFile) -> List[Path]:
    """Write each field dose and the plan dose, loose and into the bundle; the loose paths."""
    written: List[Path] = []
    total: Optional[np.ndarray] = None
    cube: Optional[Dataset] = None

    for field_number, cube_path in cubes:
        cube = pydicom.dcmread(cube_path)
        # Raw simulated dose -> the Gy(RBE) the plan delivers over the whole course, which is
        # what the TPS holds and expects to compare against.
        dose = cube.pixel_array.astype(np.float64) * float(cube.DoseGridScaling)
        dose *= _plan_scale(run_dir, field_number) * fractions * PROTON_RBE

        template = _field_template(templates, field_number, cube)
        if dose.shape != template.pixel_array.shape:
            raise RTDoseError(
                f"field {field_number} grid {dose.shape} does not match the clinical RTDOSE "
                f"grid {template.pixel_array.shape}; the TOPAS scorer must clone it"
            )

        total = dose.copy() if total is None else total + dose
        beam_number = field_number if field_number in beam_numbers else None
        ds = _derive(template, series_uid, "BEAM", f"field {field_number}", plan,
                     beam_number=beam_number)
        _encode(ds, dose)
        out = run_dir / f"{FIELD_DOSE_PREFIX}{field_number:02d}.dcm"
        ds.save_as(out, enforce_file_format=True)
        written.append(out)
        _add_to_bundle(bundle, out.name,
                       _for_plan_copy(ds, copy_of_plan, bundle_series_uid, f"field {field_number}"))

    # The summed cube: what clinical staff actually review, rather than field-by-field.
    assert total is not None and cube is not None
    template, is_plan_dose = _plan_template(templates, cube)
    ds = _derive(template, series_uid, "PLAN", "plan", plan, preserve_identity=is_plan_dose)
    _encode(ds, total)
    plan_out = run_dir / PLAN_DOSE_NAME
    ds.save_as(plan_out, enforce_file_format=True)
    written.append(plan_out)
    _add_to_bundle(bundle, plan_out.name, _for_plan_copy(ds, copy_of_plan, bundle_series_uid, "plan"))
    return written
