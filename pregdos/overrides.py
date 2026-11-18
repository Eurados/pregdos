"""Material overrides: give an RTSTRUCT structure a material in place of the CT conversion (#126).

A site defines its materials once, as TOPAS ``Ma/<name>/...`` lines, in ``materials.txt`` of its
dicomexport configuration directory.  The setup page offers those, plus a few materials TOPAS
already knows, for any structure of the study's RTSTRUCT.  dicomexport then pastes
``materials.txt`` into each field (``-S``, after the CT table) and writes the
``Ge/Patient/MaterialByRTStruct*`` lines from one ``--material-override "ROI=MATERIAL"`` per
choice, refusing a structure TOPAS would build wrongly.

Order matters: where structures overlap, TOPAS uses the first one given, so implants go before
the body.  The form's row order is the order passed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .textio import read_text_lenient

#: Materials TOPAS knows without a definition, offered after the site's own.  A short list on
#: purpose: an unknown name is caught only by TOPAS, on the compute node, so the page offers
#: names known to work rather than a free-text field.  A site needing another material defines
#: it in materials.txt.
BUILTIN_MATERIALS: Tuple[str, ...] = ("Vacuum", "Steel", "G4_AIR", "G4_WATER", "G4_Ti")

# `sv:Ma/ImplantSteel/Components = ...` -> "ImplantSteel"
_MATERIAL_RE = re.compile(r"^\s*\w+:Ma/(?P<name>[^/\s]+)/", re.MULTILINE)

Override = Tuple[str, str]
"""``(structure, material)``, in the order TOPAS should apply them."""


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
    """Every override row of the form, blank ones included, in order."""
    rois = form.getlist("override_roi") if hasattr(form, "getlist") else []
    materials = form.getlist("override_material") if hasattr(form, "getlist") else []
    return [(roi.strip(), material.strip()) for roi, material in zip(rois, materials)]


def from_form(form, structures: Sequence[str], materials: Iterable[str]) -> List[Override]:
    """The overrides the form asks for, checked against what the page offered.

    Raises ValueError with a message to flash.  Rows with neither a structure nor a material are
    the page's spare rows and are skipped.
    """
    offered = set(materials)
    overrides: List[Override] = []
    seen = set()
    for roi, material in submitted_rows(form):
        if not roi and not material:
            continue
        if not roi or not material:
            raise ValueError("Each material override needs both a structure and a material.")
        if roi not in structures:
            raise ValueError(f"Material override: {roi!r} is not a structure of this study.")
        if material not in offered:
            raise ValueError(f"Material override: {material!r} is not one of the offered materials.")
        if roi in seen:
            raise ValueError(f"Material override: {roi!r} is given more than once.")
        seen.add(roi)
        overrides.append((roi, material))
    return overrides
