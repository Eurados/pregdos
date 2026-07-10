"""Inspect and normalise an uploaded DICOM study before it reaches dicomexport.

Real-world exports are anarchic: the CT may sit in a ``CT/`` subdirectory, the RTDOSE at the
top level, and a stray ``DICOMDIR`` alongside.  Two downstream tools disagree about how to
look for files, and the mismatch is what makes such a layout fail:

* **dicomexport** finds the RTDOSE with a *recursive* glob and writes that file's parent
  directory into the generated TOPAS input as ``s:Ge/Patient/DicomDirectory``.
* **TOPAS** (``TsDicomPatient``) then scans that one directory **non-recursively** for CT
  slices.

So a study with ``Brain/CT/*.dcm`` and ``Brain/RD*.dcm`` yields ``DicomDirectory = .../Brain``
and TOPAS reports *"did not contain any files of the desired modalities"* -- minutes after
submission, in a log the user has to go hunting for (issue #52).

This module removes the dependency on the uploader's folder shape, in two steps:

1. :func:`scan` reads every file's DICOM header, and :func:`validate` rejects a study that
   is incomplete or holds more than one patient/CT series.  Validation runs **first**, on
   the original tree, so the error can name the real problem ("your CT is in ``CT/``").
2. :func:`flatten` then moves every DICOM file into the top of ``dicom/``, so
   ``DicomDirectory`` always resolves to a single directory holding every modality.

Flattening is what makes the multi-series check load-bearing rather than pedantic: two CT
series in separate folders would otherwise merge silently into one impossible patient.
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import pydicom

# Only these header fields are read, so a 125 MB RTDOSE costs no more than its header.
_TAGS = ["Modality", "PatientID", "StudyInstanceUID", "SeriesInstanceUID"]

CT = "CT"
RTSTRUCT = "RTSTRUCT"
RTPLAN = "RTPLAN"
RTDOSE = "RTDOSE"

# Modalities dicomexport needs.  Anything else in the upload is not our business.
REQUIRED = (CT, RTSTRUCT, RTPLAN, RTDOSE)

# Modalities of which exactly one file may exist.  dicomexport picks the first match of a
# glob for each of these, and glob order is not sorted -- so more than one is an ambiguity,
# not a convenience.
SINGLETON = (RTSTRUCT, RTPLAN)


@dataclass(slots=True)
class DicomFile:
    path: Path
    modality: str
    patient_id: str = ""
    study_uid: str = ""
    series_uid: str = ""


@dataclass(slots=True)
class Intake:
    """What an uploaded DICOM tree actually contains."""

    root: Path
    files: List[DicomFile] = field(default_factory=list)
    ignored: List[Path] = field(default_factory=list)
    """Files that cannot serve as input: unreadable by pydicom, or DICOM carrying no
    ``Modality`` (a ``DICOMDIR`` is the usual example -- it parses, but names no modality).

    :func:`flatten` **deletes** these, so the distinction matters: anything landing here is
    thrown away, not merely skipped."""

    def by_modality(self, modality: str) -> List[DicomFile]:
        return [f for f in self.files if f.modality == modality]

    @property
    def modalities(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for f in self.files:
            counts[f.modality] += 1
        return dict(counts)

    def _distinct(self, attr: str, modality: Optional[str] = None) -> Set[str]:
        source = self.by_modality(modality) if modality else self.files
        return {getattr(f, attr) for f in source if getattr(f, attr)}

    @property
    def patient_ids(self) -> Set[str]:
        return self._distinct("patient_id")

    @property
    def study_uids(self) -> Set[str]:
        return self._distinct("study_uid")

    @property
    def ct_series_uids(self) -> Set[str]:
        return self._distinct("series_uid", CT)


def scan(dicom_root: str | Path) -> Intake:
    """Read the header of every file under ``dicom_root``.

    Never raises on a bad file: anything pydicom cannot parse, and anything parseable that
    names no ``Modality``, is recorded in :attr:`Intake.ignored` -- so a stray ``DICOMDIR`` or
    a thumbnail does not abort an upload.
    """
    root = Path(dicom_root)
    intake = Intake(root=root)

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True, specific_tags=_TAGS)
            modality = str(getattr(ds, "Modality", "") or "").strip()
        except Exception:  # noqa: BLE001 - an unreadable file is data, not an error
            intake.ignored.append(path)
            continue

        if not modality:
            intake.ignored.append(path)
            continue

        intake.files.append(DicomFile(
            path=path,
            modality=modality,
            patient_id=str(getattr(ds, "PatientID", "") or "").strip(),
            study_uid=str(getattr(ds, "StudyInstanceUID", "") or "").strip(),
            series_uid=str(getattr(ds, "SeriesInstanceUID", "") or "").strip(),
        ))

    return intake


def validate(intake: Intake) -> List[str]:
    """Return the reasons this study cannot be converted.  Empty list means it is usable.

    Checked against the *original* tree, before flattening, so the messages can describe
    what the user actually uploaded.
    """
    errors: List[str] = []
    counts = intake.modalities

    if not intake.files:
        return ["No DICOM files found in the upload."]

    for modality in REQUIRED:
        if not counts.get(modality):
            errors.append(f"No {modality} files found. A study needs CT, RTSTRUCT, RTPLAN and RTDOSE.")

    for modality in SINGLETON:
        if counts.get(modality, 0) > 1:
            errors.append(
                f"Found {counts[modality]} {modality} files; exactly one is required "
                f"(the conversion would otherwise pick one of them arbitrarily)."
            )

    # Two patients, or two studies, in one upload would be flattened into one impossible
    # patient geometry.
    if len(intake.patient_ids) > 1:
        errors.append(f"Upload contains more than one patient: {', '.join(sorted(intake.patient_ids))}.")
    if len(intake.study_uids) > 1:
        errors.append(f"Upload contains {len(intake.study_uids)} different studies; provide exactly one.")

    # Likewise two CT series: flattening would interleave their slices.
    if len(intake.ct_series_uids) > 1:
        errors.append(
            f"Upload contains {len(intake.ct_series_uids)} CT series; provide exactly one. "
            "Flattening them into a single directory would merge them into one patient."
        )

    if counts.get(CT, 0) == 1:
        errors.append("Only one CT slice found; a full CT series is required.")

    return errors


def warnings(intake: Intake) -> List[str]:
    """Non-fatal observations worth showing the user."""
    notes: List[str] = []

    n_dose = intake.modalities.get(RTDOSE, 0)
    if n_dose > 1:
        # dicomexport clones its RTDoseGrid from glob(...)[0], and glob order is not sorted.
        notes.append(
            f"Found {n_dose} RTDOSE files. The in-field dose grid will be cloned from one of "
            "them, chosen arbitrarily; this affects only the optional in-field scorer."
        )
    if intake.ignored:
        names = ", ".join(p.name for p in intake.ignored[:3])
        more = f" (and {len(intake.ignored) - 3} more)" if len(intake.ignored) > 3 else ""
        notes.append(f"Discarded {len(intake.ignored)} unusable file(s): {names}{more}.")
    return notes


def flatten(intake: Intake) -> int:
    """Move every DICOM file to the top of ``intake.root`` and discard the rest.

    TOPAS reads ``DicomDirectory`` non-recursively, so every modality must sit side by side.
    Unusable files (see :attr:`Intake.ignored`) are removed rather than left behind:
    ``dicom/`` is the directory TOPAS scans, and it should contain nothing else.

    Returns the number of files moved.  Names that collide across subdirectories are
    disambiguated with their source directory, so no slice is silently overwritten.

    The discards happen **before** the moves.  Otherwise a non-DICOM file at the root could
    share a name with a slice moved up from a subdirectory: the move would overwrite it, and
    the discard would then delete the slice that had just taken its place.  Reserving names
    against the real directory contents (rather than against the DICOM files alone) closes
    the same hole from the other side.
    """
    root = intake.root

    for path in intake.ignored:
        path.unlink(missing_ok=True)
    intake.ignored = []

    # Reserve every name that exists on disk, not merely the ones we intend to keep.
    taken: Set[str] = {p.name for p in root.iterdir() if p.is_file()}

    moved = 0
    for f in intake.files:
        if f.path.parent == root:
            continue
        name = _unique_name(f.path, root, taken)
        taken.add(name)
        target = root / name
        if target.exists():
            raise RuntimeError(f"Refusing to overwrite {target} while flattening {root}")
        shutil.move(str(f.path), str(target))
        f.path = target
        moved += 1

    _prune_empty_dirs(root)
    return moved


def _unique_name(path: Path, root: Path, taken: Set[str]) -> str:
    """A collision-free name for ``path`` at the top of ``root``."""
    if path.name not in taken:
        return path.name

    # Prefer a name that says where the file came from, e.g. `CT_slice001.dcm`.
    relative = path.relative_to(root).parent.parts
    if relative:
        candidate = f"{'_'.join(relative)}_{path.name}"
        if candidate not in taken:
            return candidate

    stem, suffix = path.stem, path.suffix
    for n in range(2, 100000):
        candidate = f"{stem}_{n}{suffix}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"Cannot find a free name for {path.name}")


def _prune_empty_dirs(root: Path) -> None:
    """Remove the now-empty subdirectories left behind by the flatten.

    Reverse order visits children before parents, so a nested chain of empty directories
    collapses entirely.  A directory that still holds something is left alone: ``scan`` only
    records regular files, so a FIFO or a dangling symlink is neither moved nor discarded and
    keeps its parent alive.  That is harmless -- TOPAS reads ``dicom/`` non-recursively.
    """
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass  # not empty
