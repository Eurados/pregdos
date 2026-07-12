"""Turn the raw TOPAS dose cubes into RTDOSE files a TPS will import (issue #62).

The in-field scorer dicomexport writes is a valid enough RTDOSE for TOPAS, but not for a
clinical TPS: Type-1 attributes are missing (Eclipse rejects the file on ``DoseSummationType``
(3004,000A), then on ``DoseType`` (3004,0004)), the dose is raw *simulation* dose rather than
what the plan delivers, and it is 16-bit -- which, on a grid scaled to a target dose of tens of
Gy, rounds every out-of-field (fetal) dose to zero.

Rather than repair the TOPAS cube tag by tag, **each output is built from the study's own
clinical RTDOSE as a template**, with only the dose grid replaced.  Everything a TPS is fussy
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
import re
import zipfile
from pathlib import Path
from typing import List, Optional

import numpy as np
import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, generate_uid

from . import results

# TOPAS writes the in-field cube as ``topas_field<n>.dcm`` (no zero padding), while the input
# it came from is ``topas_field<nn>.txt`` (zero padded).  Both are keyed on the field number.
_CUBE_RE = re.compile(r"^topas_field(\d+)\.dcm$")

FIELD_DOSE_PREFIX = "rtdose_field"
PLAN_DOSE_NAME = "rtdose_plan.dcm"
PLAN_IMPORT_BUNDLE_NAME = "rtdose_plan_eclipse_import.zip"

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


def _clinical_rtdose(run_dir: Path) -> Optional[Path]:
    """The study's clinical RTDOSE, used as the template for everything we write."""
    found = sorted(_study_dicom(run_dir).glob("RD*.dcm"))
    return found[0] if found else None


def field_cubes(run_dir: str | Path) -> list[tuple[int, Path]]:
    """``(field_number, cube_path)`` for every TOPAS dose cube in the run, in field order."""
    run_dir = Path(run_dir)
    out = []
    for path in run_dir.glob("topas_field*.dcm"):
        match = _CUBE_RE.match(path.name)
        if match:
            out.append((int(match.group(1)), path))
    return sorted(out)


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


def _derive(template: Dataset, series_uid: str, summation: str, description: Optional[str],
            plan: Dataset, beam_number: Optional[str] = None, preserve_identity: bool = False) -> Dataset:
    """Copy the clinical RTDOSE and re-identify it as *our* dose, keeping its plan reference.

    The ``ReferencedRTPlanSequence`` is inherited verbatim, so the dose points at exactly the
    plan the TPS already associates with the clinical dose.  A BEAM dose additionally has to say
    *which* beam, which the (plan-level) clinical reference does not carry.
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

    if not preserve_identity:
        # Field-dose objects need their own identity; the plan dose intentionally keeps the
        # Eclipse RD identity because Eclipse is strict about reconnecting cloned doses.
        ds.SOPInstanceUID = generate_uid()
        ds.SeriesInstanceUID = series_uid
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    transfer_syntax = ds.file_meta.get("TransferSyntaxUID", ExplicitVRLittleEndian)
    ds.is_little_endian = True
    ds.is_implicit_VR = transfer_syntax == ImplicitVRLittleEndian
    if description is not None:
        ds.SeriesDescription = description
    if not preserve_identity:
        ds.DoseComment = "PregDos/TOPAS: Gy(RBE)=physical x1.1, whole course"  # LO: max 64 chars
    return ds


def _write_plan_import_bundle(run_dir: Path, plan_path: Path, dose_path: Path) -> Path:
    """Bundle the generated PLAN dose with the RTPLAN Eclipse needs to connect it."""
    out = run_dir / PLAN_IMPORT_BUNDLE_NAME
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(plan_path, arcname=plan_path.name)
        zf.write(dose_path, arcname=dose_path.name)
    return out


def postprocess(run_dir: str | Path) -> List[Path]:
    """Write importable per-field (BEAM) and summed (PLAN) RTDOSE files for a finished run.

    Returns the paths written, with the plan dose and Eclipse import bundle last.  The raw
    TOPAS cubes are left untouched.
    """
    run_dir = Path(run_dir)
    cubes = field_cubes(run_dir)
    if not cubes:
        return []

    plan_path = _rtplan_path(run_dir)
    template_path = _clinical_rtdose(run_dir)
    if plan_path is None or template_path is None:
        raise RTDoseError("study needs both an RTPLAN and a clinical RTDOSE to build from")
    plan = pydicom.dcmread(plan_path, stop_before_pixels=True)
    template = pydicom.dcmread(template_path)

    fractions = results.planned_fractions(plan_path) or 1
    beams = [b.BeamNumber for b in getattr(plan, "IonBeamSequence", getattr(plan, "BeamSequence", []))]

    series_uid = generate_uid()
    written: List[Path] = []
    total: Optional[np.ndarray] = None

    for field_number, cube_path in cubes:
        cube = pydicom.dcmread(cube_path)
        # Raw simulated dose -> the Gy(RBE) the plan delivers over the whole course, which is
        # what the TPS holds and expects to compare against.
        dose = cube.pixel_array.astype(np.float64) * float(cube.DoseGridScaling)
        dose *= _plan_scale(run_dir, field_number) * fractions * PROTON_RBE

        if dose.shape != template.pixel_array.shape:
            raise RTDoseError(
                f"field {field_number} grid {dose.shape} does not match the clinical RTDOSE "
                f"grid {template.pixel_array.shape}; the TOPAS scorer must clone it"
            )

        total = dose.copy() if total is None else total + dose
        beam_number = beams[field_number - 1] if 0 < field_number <= len(beams) else None
        ds = _derive(template, series_uid, "BEAM", f"PregDos TOPAS field {field_number}",
                     plan, beam_number=beam_number)
        _encode(ds, dose)
        out = run_dir / f"{FIELD_DOSE_PREFIX}{field_number:02d}.dcm"
        ds.save_as(out, enforce_file_format=True)
        written.append(out)

    # The summed cube: what clinical staff actually review, rather than field-by-field.
    assert total is not None
    ds = _derive(template, series_uid, "PLAN", None, plan, preserve_identity=True)
    _encode(ds, total)
    plan_out = run_dir / PLAN_DOSE_NAME
    ds.save_as(plan_out, enforce_file_format=True)
    written.append(plan_out)
    written.append(_write_plan_import_bundle(run_dir, plan_path, plan_out))
    return written
