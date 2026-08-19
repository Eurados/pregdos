"""RTSTRUCT mask pre-pass and structure mass/volume metrics.

TOPAS structure-filtered 1x1x1 scorers filter the numerator, but their built-in
normalisation is not useful for absolute structure dose.  PregDos therefore runs a
cheap TOPAS pre-pass that writes a native-CT-grid mask for each scored RT structure,
then combines that mask with the CT Hounsfield units and the HU-to-material table
from the generated TOPAS input.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pydicom


MASK_PREPASS_FILE = "structure_mask_prepass.txt"
PREPASS_FILE = MASK_PREPASS_FILE
METRICS_FILE = "structure_metrics.json"
MEV_PER_G_TO_GY = 1.602176634e-10


class StructureMetricsError(Exception):
    """Structure metrics could not be generated from the pre-pass output."""


def _sanitize_name(name: str) -> str:
    result = re.sub(r"[^A-Za-z0-9]", "_", name).strip("_")
    return result or "unknown"


def _parameter_value(text: str, parameter: str) -> str | None:
    pattern = re.compile(rf"^\s*\w+:{re.escape(parameter)}\s*=\s*(?P<value>.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        pattern = re.compile(rf"^\s*{re.escape(parameter)}\s*=\s*(?P<value>.+?)\s*$", re.MULTILINE)
        match = pattern.search(text)
    return match.group("value").strip() if match else None


def _quoted_or_raw(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value.split()[0]


def _copy_parameters(source: str, names: Iterable[str]) -> list[str]:
    out: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for name in names:
            if re.match(rf"^(?:\w+:)?{re.escape(name)}\s*=", stripped):
                out.append(line)
                break
    return out


def write_prepass_input(run_dir: str | Path, reference_topas: str | Path, structures: Iterable[str]) -> str | None:
    """Write the TOPAS structure mask pre-pass input.

    Returns the pre-pass filename when at least one structure was requested, otherwise None.
    """
    names = sorted({s for s in structures if s})
    if not names:
        return None

    run_dir = Path(run_dir)
    source = Path(reference_topas).read_text()
    parameters = [
        "includeFile",
        "Rt/Plan/IsoCenterX",
        "Rt/Plan/IsoCenterY",
        "Rt/Plan/IsoCenterZ",
        "Ge/Patient/DicomOriginX",
        "Ge/Patient/DicomOriginY",
        "Ge/Patient/DicomOriginZ",
        "Ge/Patient/IgnoreInconsistentFrameOfReferenceUID",
        "Ge/World/Type",
        "Ge/World/Material",
        "Ge/World/HLX",
        "Ge/World/HLY",
        "Ge/World/HLZ",
        "Ge/World/Invisible",
        "Ge/Patient/Parent",
        "Ge/Patient/Type",
        "Ge/Patient/DicomDirectory",
        "Ge/Patient/DicomModalityTags",
        "Ge/Patient/CloneRTDoseGridFrom",
        "Ge/Patient/TransX",
        "Ge/Patient/TransY",
        "Ge/Patient/TransZ",
        "Ge/Patient/RotX",
        "Ge/Patient/RotY",
        "Ge/Patient/RotZ",
        "Ge/Patient/Color",
    ]

    lines = [
        "# PregDos RTSTRUCT mask pre-pass",
        f"# Generated from {Path(reference_topas).name}",
        "",
        *_copy_parameters(source, parameters),
        "",
        'i:Ts/NumberOfThreads = 1',
        'b:Ts/DumpParameters = "False"',
        'b:Ts/PauseBeforeQuit = "False"',
        "",
        's:Ge/PregDosMaskSourcePosition/Parent = "World"',
        's:Ge/PregDosMaskSourcePosition/Type = "Group"',
        "d:Ge/PregDosMaskSourcePosition/TransZ = -900 mm",
        "",
        's:So/PregDosMaskDummy/Type = "Beam"',
        's:So/PregDosMaskDummy/Component = "PregDosMaskSourcePosition"',
        's:So/PregDosMaskDummy/BeamParticle = "gamma"',
        "d:So/PregDosMaskDummy/BeamEnergy = 1 MeV",
        "u:So/PregDosMaskDummy/BeamEnergySpread = 0",
        's:So/PregDosMaskDummy/BeamPositionDistribution = "Flat"',
        's:So/PregDosMaskDummy/BeamPositionCutoffShape = "Rectangle"',
        "d:So/PregDosMaskDummy/BeamPositionCutoffX = 1 mm",
        "d:So/PregDosMaskDummy/BeamPositionCutoffY = 1 mm",
        's:So/PregDosMaskDummy/BeamAngularDistribution = "None"',
        "i:So/PregDosMaskDummy/NumberOfHistoriesInRun = 1",
        "",
    ]
    for structure in names:
        safe = _sanitize_name(structure)
        scorer = f"PregDosMask_{safe}"
        lines += [
            f's:Sc/{scorer}/Quantity = "StepCount"',
            f's:Sc/{scorer}/Component = "Patient"',
            f'b:Sc/{scorer}/SetBinToMinusOneIfNotInRTStructure = "True"',
            f'sv:Sc/{scorer}/OnlyIncludeIfInRTStructure = 1 "{structure}"',
            f's:Sc/{scorer}/OutputType = "binary"',
            f's:Sc/{scorer}/OutputFile = "structure_mask_{safe}"',
            f's:Sc/{scorer}/IfOutputFileAlreadyExists = "Overwrite"',
            f'sv:Sc/{scorer}/Report = 1 "Sum"',
            "",
        ]

    (run_dir / PREPASS_FILE).write_text("\n".join(lines).rstrip() + "\n")
    return PREPASS_FILE


def write_mask_prepass(reference_topas: str | Path, structures: Iterable[str]) -> str | None:
    """Compatibility wrapper used by the web route.

    ``reference_topas`` is one generated field input inside the run directory.  The pre-pass
    lands next to it and reuses its DICOM and HU-table paths.
    """
    reference = Path(reference_topas)
    return write_prepass_input(reference.parent, reference, structures)


@dataclass(slots=True)
class CTData:
    hu: np.ndarray
    voxel_volume_cm3: float
    dicom_directory: Path


def _load_ct(dicom_dir: Path) -> CTData:
    slices = []
    for path in sorted(dicom_dir.glob("*.dcm")):
        try:
            ds = pydicom.dcmread(path)
        except Exception:
            continue
        if getattr(ds, "Modality", None) == "CT":
            slices.append(ds)
    if not slices:
        raise StructureMetricsError(f"no CT slices found in {dicom_dir}")

    row_dir = np.asarray(slices[0].ImageOrientationPatient[:3], dtype=float)
    col_dir = np.asarray(slices[0].ImageOrientationPatient[3:], dtype=float)
    normal = np.cross(row_dir, col_dir)
    slices.sort(key=lambda ds: float(np.dot(np.asarray(ds.ImagePositionPatient, dtype=float), normal)))

    arrays = []
    for ds in slices:
        slope = float(getattr(ds, "RescaleSlope", 1.0))
        intercept = float(getattr(ds, "RescaleIntercept", 0.0))
        arrays.append(ds.pixel_array.astype(np.float64) * slope + intercept)
    hu = np.stack(arrays, axis=0)

    row_spacing = float(slices[0].PixelSpacing[0])
    col_spacing = float(slices[0].PixelSpacing[1])
    positions = [float(np.dot(np.asarray(ds.ImagePositionPatient, dtype=float), normal)) for ds in slices]
    if len(positions) > 1:
        slice_spacing = float(np.median(np.diff(positions)))
    else:
        slice_spacing = float(getattr(slices[0], "SliceThickness", 1.0))
    voxel_volume_cm3 = abs(row_spacing * col_spacing * slice_spacing) / 1000.0
    return CTData(hu=hu, voxel_volume_cm3=voxel_volume_cm3, dicom_directory=dicom_dir)


_PARAM_LINE_RE = re.compile(r"^\s*\w+:[^=\n]+=", re.MULTILINE)
_FLOAT_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _array_values(text: str, parameter: str) -> list[float]:
    match = re.search(rf"^\s*\w+:{re.escape(parameter)}\s*=\s*", text, re.MULTILINE)
    if not match:
        raise StructureMetricsError(f"{parameter} missing from HU table")
    next_param = _PARAM_LINE_RE.search(text, match.end())
    raw = text[match.end() : next_param.start() if next_param else len(text)]
    raw = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
    values = [float(x) for x in _FLOAT_RE.findall(raw)]
    if not values:
        raise StructureMetricsError(f"{parameter} has no numeric values")
    count = int(values[0])
    payload = values[1:]
    if len(payload) < count:
        raise StructureMetricsError(f"{parameter} expected {count} values, found {len(payload)}")
    return payload[:count]


def _density_from_hu(hu: np.ndarray, spr_table: Path) -> np.ndarray:
    text = spr_table.read_text()
    if '"Schneider"' not in text:
        raise StructureMetricsError(f"unsupported HU material converter in {spr_table}")

    boundaries = np.asarray(_array_values(text, "Ge/Patient/SchneiderHounsfieldUnitSections"), dtype=float)
    offsets = np.asarray(_array_values(text, "Ge/Patient/SchneiderDensityOffset"), dtype=float)
    factors = np.asarray(_array_values(text, "Ge/Patient/SchneiderDensityFactor"), dtype=float)
    factor_offsets = np.asarray(_array_values(text, "Ge/Patient/SchneiderDensityFactorOffset"), dtype=float)
    corrections = np.asarray(_array_values(text, "Ge/Patient/DensityCorrection"), dtype=float)
    if not (len(boundaries) == len(offsets) + 1 == len(factors) + 1 == len(factor_offsets) + 1):
        raise StructureMetricsError(f"inconsistent Schneider density sections in {spr_table}")

    hu_min = boundaries[0]
    hu_max = boundaries[-1] - 1
    hu_clipped = np.clip(hu, hu_min, hu_max)
    section = np.searchsorted(boundaries[1:], hu_clipped, side="right")
    section = np.clip(section, 0, len(offsets) - 1)

    density = offsets[section] + factors[section] * (factor_offsets[section] + hu_clipped)

    correction_origin = hu_min if int(round(boundaries[-1] - boundaries[0])) == len(corrections) else -1000.0
    correction_max = correction_origin + len(corrections) - 1
    corr_index = np.rint(
        np.clip(hu_clipped, correction_origin, correction_max) - correction_origin
    ).astype(int)
    return density * corrections[corr_index]


def _resolve_topas_path(run_dir: Path, value: str | None) -> Path:
    if not value:
        raise StructureMetricsError("required path parameter is missing")
    path = Path(_quoted_or_raw(value))
    return path if path.is_absolute() else (run_dir / path).resolve()


def _prepass_structures(run_dir: Path) -> list[tuple[str, str]]:
    prepass = run_dir / PREPASS_FILE
    if not prepass.is_file():
        return []
    text = prepass.read_text()
    scorers: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r'^\s*\w+:Sc/(?P<scorer>[^/]+)/(?P<param>OnlyIncludeIfInRTStructure|OutputFile)\s*=\s*(?P<value>.+?)\s*$',
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        scorer = match.group("scorer")
        param = match.group("param")
        value = match.group("value")
        entry = scorers.setdefault(scorer, {})
        if param == "OnlyIncludeIfInRTStructure":
            name_match = re.search(r'\d+\s*"(?P<name>[^"]+)"', value)
            if name_match:
                entry["name"] = name_match.group("name")
        elif param == "OutputFile":
            output = _quoted_or_raw(value)
            if output.startswith("structure_mask_"):
                entry["safe"] = output.removeprefix("structure_mask_")

    found = [
        (entry["name"], entry["safe"])
        for entry in scorers.values()
        if "name" in entry and "safe" in entry
    ]
    if found:
        return found
    for match in re.finditer(r'OnlyIncludeIfInRTStructure\s*=\s*\d+\s*"(?P<name>[^"]+)"', text):
        name = match.group("name")
        found.append((name, _sanitize_name(name)))
    return found


def compute_metrics(run_dir: str | Path) -> dict:
    """Compute and write ``structure_metrics.json`` from pre-pass binary masks."""
    run_dir = Path(run_dir)
    prepass = run_dir / PREPASS_FILE
    if not prepass.is_file():
        raise StructureMetricsError(f"{PREPASS_FILE} not found")
    text = prepass.read_text()
    dicom_dir = _resolve_topas_path(run_dir, _parameter_value(text, "Ge/Patient/DicomDirectory"))
    spr_table = _resolve_topas_path(run_dir, _parameter_value(text, "includeFile"))
    ct = _load_ct(dicom_dir)
    densities = _density_from_hu(ct.hu, spr_table)

    patient_voxels = int(ct.hu.size)
    patient_volume = patient_voxels * ct.voxel_volume_cm3
    patient_mass = float(densities.sum() * ct.voxel_volume_cm3)

    payload = {
        "source": "TOPAS SetBinToMinusOneIfNotInRTStructure pre-pass",
        "prepass_file": PREPASS_FILE,
        "dicom_directory": str(dicom_dir),
        "spr_table": str(spr_table),
        "ct_shape_zyx": list(ct.hu.shape),
        "voxel_volume_cm3": ct.voxel_volume_cm3,
        "patient": {
            "voxel_count": patient_voxels,
            "volume_cm3": patient_volume,
            "mass_g": patient_mass,
            "average_density_g_cm3": patient_mass / patient_volume if patient_volume else math.nan,
        },
        "structures": {},
    }

    for structure, safe in _prepass_structures(run_dir):
        mask_path = run_dir / f"structure_mask_{safe}.bin"
        if not mask_path.is_file() or mask_path.stat().st_size == 0:
            continue
        mask_raw = np.fromfile(mask_path, dtype="<f8")
        if mask_raw.size != ct.hu.size:
            raise StructureMetricsError(f"{mask_path.name} has {mask_raw.size} bins, expected {ct.hu.size}")
        mask = mask_raw.reshape(ct.hu.shape) >= 0
        voxels = int(mask.sum())
        volume = voxels * ct.voxel_volume_cm3
        mass = float(densities[mask].sum() * ct.voxel_volume_cm3) if voxels else 0.0
        payload["structures"][structure] = {
            "mask_file": mask_path.name,
            "voxel_count": voxels,
            "volume_cm3": volume,
            "mass_g": mass,
            "average_density_g_cm3": mass / volume if volume else math.nan,
            "patient_to_structure_mass_ratio": patient_mass / mass if mass else math.nan,
            "patient_to_structure_volume_ratio": patient_volume / volume if volume else math.nan,
        }

    (run_dir / METRICS_FILE).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _discard_masks(run_dir)
    return payload


def _discard_masks(run_dir: Path) -> None:
    """Delete the pre-pass masks now that their numbers are in ``structure_metrics.json``.

    TOPAS writes one mask per structure as ``double`` per CT voxel -- 8 bytes to carry one
    bit.  On a 512x512x658 CT that is 1.4 GB *per structure*, and nothing reads it again:
    the field runs re-derive structure membership themselves (their inputs never mention
    these files), and :func:`compute_metrics` has just consumed the only copy anyone needs.

    Deleting them here rather than at the end of the run keeps the peak footprint down while
    the fields are still to come.  A rerun regenerates them: :func:`webserver._clear_run_outputs`
    removes ``structure_metrics.json`` alongside any masks, so the pre-pass runs again.

    Called only after the JSON is safely written -- if the computation raised, the masks stay
    put for inspection.
    """
    for _, safe in _prepass_structures(run_dir):
        for suffix in (".bin", ".binheader"):
            (run_dir / f"structure_mask_{safe}{suffix}").unlink(missing_ok=True)


def load_metrics(run_dir: str | Path) -> dict | None:
    path = Path(run_dir) / METRICS_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def ensure_metrics(run_dir: str | Path) -> tuple[dict | None, list[str]]:
    """Return cached metrics, or compute them if the pre-pass has completed."""
    run_dir = Path(run_dir)
    if metrics := load_metrics(run_dir):
        return metrics, []
    prepass_exit = run_dir / "structure_mask_prepass.exit_code"
    if not prepass_exit.is_file():
        return None, []
    try:
        code = int(prepass_exit.read_text().strip())
    except (OSError, ValueError):
        return None, ["structure mask pre-pass exit code is unreadable"]
    if code != 0:
        return None, ["structure mask pre-pass failed; absolute structure normalisation is unavailable"]
    try:
        return compute_metrics(run_dir), []
    except StructureMetricsError as exc:
        return None, [str(exc)]


def structure_metric(metrics: dict | None, structure: str) -> dict | None:
    """Return the cached metric record for one structure, if available."""
    if not metrics or not structure:
        return None
    structures = metrics.get("structures")
    if not isinstance(structures, dict):
        return None
    value = structures.get(structure)
    return value if isinstance(value, dict) else None


def energy_deposit_to_gy(
    metrics: dict | None,
    structure: str,
    unit: str,
    total: float | None,
    sd: float | None,
) -> tuple[float | None, float | None] | None:
    """Convert scaled TOPAS EnergyDeposit to absorbed dose in Gy using structure mass."""
    if total is None or unit.strip().lower() != "mev":
        return None
    metric = structure_metric(metrics, structure)
    if not metric:
        return None
    try:
        mass_g = float(metric["mass_g"])
    except (KeyError, TypeError, ValueError):
        return None
    if mass_g <= 0:
        return None
    factor = MEV_PER_G_TO_GY / mass_g
    return total * factor, None if sd is None else sd * factor


def fluence_volume_correction_factor(metrics: dict | None, structure: str) -> float | None:
    """Correction for structure-filtered fluence scorers attached to the whole patient."""
    metric = structure_metric(metrics, structure)
    if not metric or not metrics:
        return None
    try:
        patient_volume = float(metrics["patient"]["volume_cm3"])
        structure_volume = float(metric["volume_cm3"])
    except (KeyError, TypeError, ValueError):
        return None
    if patient_volume <= 0 or structure_volume <= 0:
        return None
    return patient_volume / structure_volume


# NOTE: a "mass_correction_factor" (M_patient / M_structure) was intentionally removed.
# A single-bin structure scorer on ``Patient`` is normalized by TOPAS to the whole patient
# box: EnergyDeposit (energy) is divided by nothing and needs ÷ structure mass
# (energy_deposit_to_gy); every intensive dose/fluence quantity (DoseToWater, DoseToMedium,
# AmbientDoseEquivalent) is already divided by the patient volume and needs the volume ratio
# V_patient / V_structure (fluence_volume_correction_factor). The mass ratio is never the
# right correction for a dose quantity, so it is not offered.
