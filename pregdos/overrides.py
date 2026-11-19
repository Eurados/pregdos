"""Material overrides: give an RTSTRUCT structure a material in place of the CT conversion (#126).

A site defines its materials once, as TOPAS ``Ma/<name>/...`` lines, in ``materials.txt`` of its
dicomexport configuration directory.  The setup page offers those, plus a few materials TOPAS
already knows, for any structure of the study's RTSTRUCT.  dicomexport then pastes
``materials.txt`` into each field (``-S``, after the CT table) and writes the
``Ge/Patient/MaterialByRTStruct*`` lines from one ``--material-override "ROI=MATERIAL"`` per
choice, refusing a structure TOPAS would build wrongly.

Order matters: where structures overlap, TOPAS uses the first one given, so implants go before
the body.  The form's row order is the order passed.

Eclipse marks the structures it overrides in the RTSTRUCT, and the setup page starts with a row
for each (:func:`initial_rows`).  Eclipse writes a material name (``MaterialID``) or only a
value such as a relative electron density (``ROIPhysicalPropertiesSequence``).  Neither is a
TOPAS material, so a row starts at "No override" unless the site maps that Eclipse material
name to one (``[overrides.eclipse_materials]``).  The user picks or changes the material.
"""

from __future__ import annotations

import difflib
import importlib.resources
import re
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .textio import read_text_lenient

#: Materials TOPAS knows without a definition, offered after the site's own.  A short list on
#: purpose: an unknown name is caught only by TOPAS, on the compute node, so the page offers
#: names known to work rather than a free-text field.  A site needing another material defines
#: it in materials.txt.
BUILTIN_MATERIALS: Tuple[str, ...] = ("Vacuum", "Steel", "G4_AIR", "G4_WATER", "G4_Ti")

#: The material select's value for "Other Geant4 material...": the name is then in the row's
#: text field, ``override_material_other``.
OTHER = "__other__"


def _geant4_materials() -> Tuple[str, ...]:
    text = (importlib.resources.files("pregdos") / "data" / "geant4_materials.txt").read_text(encoding="utf-8")
    return tuple(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))


#: Every Geant4 NIST material TOPAS knows by name (G4_WATER, G4_BONE_COMPACT_ICRU, ...).  Too
#: many for a dropdown, so the page offers them by typing, under "Other Geant4 material".
GEANT4_MATERIALS: Tuple[str, ...] = _geant4_materials()

# `sv:Ma/ImplantSteel/Components = ...` -> "ImplantSteel"
_MATERIAL_RE = re.compile(r"^\s*\w+:Ma/(?P<name>[^/\s]+)/", re.MULTILINE)

Override = Tuple[str, str]
"""``(structure, material)``, in the order TOPAS should apply them."""


@dataclass(frozen=True)
class Declared:
    """What an RTSTRUCT says about a structure's material: Eclipse's override."""

    material_id: Optional[str]
    properties: Tuple[Tuple[str, str], ...]

    def __str__(self) -> str:
        """``MaterialID=S45C_Steel``, ``REL_ELEC_DENSITY=0.001``: as dicomexport's warning has it."""
        parts = [f"MaterialID={self.material_id}"] if self.material_id else []
        parts += [f"{name}={value}" for name, value in self.properties]
        return ", ".join(parts)


@dataclass(frozen=True)
class Row:
    """One override row of the setup page."""

    roi: str = ""
    material: str = ""
    note: str = ""


def declared_overrides(ds) -> Dict[str, Declared]:
    """Eclipse's overrides in an RTSTRUCT dataset, by structure name, in RTSTRUCT order.

    The ROI's ``MaterialID`` (300A,00E1) and each item of its ``ROIPhysicalPropertiesSequence``
    (3006,00B0), as dicomexport reads them (``import_rtstruct._declared_overrides``).  Eclipse
    writes these only for a structure it overrides.
    """
    names = {int(roi.ROINumber): str(roi.ROIName)
             for roi in getattr(ds, "StructureSetROISequence", None) or []
             if getattr(roi, "ROINumber", None) is not None}
    declared: Dict[str, Declared] = {}
    for obs in getattr(ds, "RTROIObservationsSequence", None) or []:
        number = getattr(obs, "ReferencedROINumber", None)
        if number is None or int(number) not in names:
            continue
        material_id = str(getattr(obs, "MaterialID", "") or "").strip() or None
        properties = tuple(
            (str(getattr(prop, "ROIPhysicalProperty", "") or "ROIPhysicalProperty"),
             str(getattr(prop, "ROIPhysicalPropertyValue", "")))
            for prop in getattr(obs, "ROIPhysicalPropertiesSequence", None) or [])
        if material_id or properties:
            declared[names[int(number)]] = Declared(material_id, properties)
    order = list(names.values())
    return dict(sorted(declared.items(), key=lambda item: order.index(item[0])))


def preselected(declared: Declared, mapping: Mapping[str, str], offered: Iterable[str]) -> Tuple[str, str]:
    """``(material, note)`` for a row Eclipse declared: the site's mapping, if any and offered.

    Never a guess: a value-only override, or a material name the site has not mapped, starts
    at "No override".
    """
    note = f"Eclipse: {declared}"
    target = mapping.get(declared.material_id or "")
    if not target:
        return "", note
    if target not in set(offered):
        return "", (f"{note}. The site maps {declared.material_id} to {target}, which is not "
                    f"offered here, so nothing is preselected.")
    return target, f"{note}, preselected by the site"


def initial_rows(declared: Mapping[str, Declared], mapping: Mapping[str, str],
                 offered: Iterable[str]) -> List[Row]:
    """The rows a fresh setup page starts with: one per structure Eclipse overrides."""
    offered = list(offered)
    rows = []
    for roi, what in declared.items():
        material, note = preselected(what, mapping, offered)
        rows.append(Row(roi, material, note))
    return rows


def site_materials(materials_file: Optional[str | Path]) -> List[str]:
    """Material names ``materials.txt`` defines, in file order.  [] if absent or unreadable."""
    if not materials_file:
        return []
    try:
        text = read_text_lenient(materials_file)
    except OSError:
        return []
    names: List[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if (m := _MATERIAL_RE.match(line)) and m.group("name") not in names:
            names.append(m.group("name"))
    return names


def materials_file_of(listing: dict) -> Optional[str]:
    """``materials.txt`` of the config dir, from ``dicomexport --list-config json``, if it exists."""
    entry = listing.get("materials")
    if isinstance(entry, dict) and entry.get("exists") is True and isinstance(entry.get("path"), str):
        return entry["path"]
    return None


def submitted_rows(form) -> List[Override]:
    """Every override row of the form, blank ones included, in order.

    A row whose material is "Other Geant4 material" takes the name typed into its text field.
    """
    def values(name):
        return form.getlist(name) if hasattr(form, "getlist") else []

    rows = []
    for roi, material, other in zip_longest(values("override_roi"), values("override_material"),
                                            values("override_material_other"), fillvalue=""):
        material = material.strip()
        if material == OTHER:
            material = other.strip()
        rows.append((roi.strip(), material))
    return rows


def from_form(form, structures: Sequence[str], materials: Iterable[str]) -> List[Override]:
    """The overrides the form asks for, checked against what the page offered.

    Raises ValueError with a message to flash.  Rows without a material ("No override") are
    skipped: the page's spare rows, and Eclipse's structures the user leaves to the CT.  A
    material without a structure is incomplete, and refused.
    """
    offered = set(materials)
    overrides: List[Override] = []
    seen = set()
    for roi, material in submitted_rows(form):
        if not material:
            continue
        if not roi:
            raise ValueError(f"Material override: {material} has no structure. Choose one, or "
                             f"remove the row.")
        if roi not in structures:
            raise ValueError(f"Material override: {roi!r} is not a structure of this study.")
        if material not in offered:
            raise ValueError(f"Material override: {material!r} is not one of the offered materials"
                             f"{_close_to(material, offered)}.")
        if roi in seen:
            raise ValueError(f"Material override: {roi!r} is given more than once.")
        seen.add(roi)
        overrides.append((roi, material))
    return overrides


def _close_to(name: str, offered: Iterable[str]) -> str:
    match = difflib.get_close_matches(name, list(offered), n=1)
    return f" (did you mean {match[0]}?)" if match else ""
