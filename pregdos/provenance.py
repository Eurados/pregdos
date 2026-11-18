"""What a run was converted with, from the header dicomexport writes into each field (#132).

dicomexport >= 2.0 records its inputs in the header of every TOPAS file it writes::

    # using dicomexport 2.0.0
    # CONFIG_DIR: /etc/dicomexport (--config-dir)
    # BEAM_MODEL: /etc/dicomexport/beam_models/DCPT_beam_model__v5.csv
    # RANGE_SHIFTER_CATALOG: /etc/dicomexport/range_shifters.csv
    # INCLUDE_INLINE: ../HUtoMaterialSchneider.txt sha256=7a4c...
    # INCLUDE_INLINE: /etc/dicomexport/materials.txt sha256=e49e...
    # MATERIAL_OVERRIDE: Hip implant=ImplantSteel
    # SPOT_MODE: sources
    # RUNS: 10

Files picked from the configuration directory have absolute paths; paths PregDos typed appear as
typed.  :mod:`pregdos.executor` reads this at submission into ``run.json``, so a report states
what the run was made with even after the site's directory or dicomexport itself has changed.
All fields of a run come from one dicomexport call, so the first field speaks for all of them.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import List, Optional

from .textio import read_text_lenient

_VERSION_RE = re.compile(r"^#\s*using dicomexport\s+(?P<version>\S+)\s*$", re.MULTILINE)
_VALUE_RE = re.compile(r"^#\s*(?P<key>CONFIG_DIR|BEAM_MODEL|RANGE_SHIFTER_CATALOG|SPOT_MODE|RUNS):\s*(?P<value>.+?)\s*$",
                       re.MULTILINE)
_INCLUDE_RE = re.compile(r"^#\s*INCLUDE:\s*(?P<path>.+?)\s*$", re.MULTILINE)
_INCLUDE_INLINE_RE = re.compile(r"^#\s*INCLUDE_INLINE:\s*(?P<path>.+?)\s+sha256=(?P<sha256>[0-9a-f]{64})\s*$",
                                re.MULTILINE)
_OVERRIDE_RE = re.compile(r"^#\s*MATERIAL_OVERRIDE:\s*(?P<override>.+?)\s*$", re.MULTILINE)


#: Report rows for the record, ``(key, label)`` in the order shown; the values are made by
#: :func:`pregdos.reporting.report_provenance`.  Here so the CSV and PDF reports share them.
#: Labels no wider than "Material overrides", the widest the PDF's label column holds.
CONVERSION_FIELDS = (
    ("beam_model", "Beam model"),
    ("other_includes", "Also included"),
    ("material_overrides", "Material overrides"),
    ("spot_mode", "Spot mode"),
    ("config_dir", "dicomexport dir"),
    ("range_shifter_catalog", "Range shifters"),
)


@dataclass
class Include:
    path: str
    sha256: str = ""
    """Set for a pasted file (``-S``); "" for one included by path (``-s``)."""

    @property
    def label(self) -> str:
        """``name (12345678)``: the file's name, and the hash prefix that identifies it."""
        name = PureWindowsPath(self.path).name
        return f"{name} ({self.sha256[:8]})" if self.sha256 else name


@dataclass
class ConversionRecord:
    dicomexport: str = ""
    """The dicomexport version that wrote the field -- not necessarily the one installed now."""
    config_dir: str = ""
    """As dicomexport wrote it, with how it was found: ``/etc/dicomexport (--config-dir)``."""
    beam_model: str = ""
    range_shifter_catalog: str = ""
    includes: List[Include] = field(default_factory=list)
    """In the order given; the first is the CT-number-to-material table."""
    material_overrides: List[str] = field(default_factory=list)
    """``ROI=MATERIAL``, in the order given: where structures overlap, TOPAS uses the first."""
    spot_mode: str = ""
    runs: int = 0

    @property
    def layout(self) -> str:
        """How the field was run, for a report: ``sources, 10 batches``."""
        if self.spot_mode == "sources":
            return ("sources, single batch (no error bars)" if self.runs == 1
                    else f"sources, {self.runs} batches")
        return self.spot_mode

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw) -> Optional["ConversionRecord"]:
        """Back from ``run.json``.  None for anything that is not a record: ``run.json`` is read
        on every page render and must never take one down."""
        if not isinstance(raw, dict):
            return None

        def text(key: str) -> str:
            value = raw.get(key)
            return value if isinstance(value, str) else ""

        def items(key: str) -> list:
            value = raw.get(key)
            return value if isinstance(value, list) else []

        includes = []
        for entry in items("includes"):
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                sha256 = entry.get("sha256")
                includes.append(Include(entry["path"], sha256 if isinstance(sha256, str) else ""))
        overrides = [o for o in items("material_overrides") if isinstance(o, str)]
        runs = raw.get("runs")
        return cls(
            dicomexport=text("dicomexport"), config_dir=text("config_dir"),
            beam_model=text("beam_model"), range_shifter_catalog=text("range_shifter_catalog"),
            includes=includes, material_overrides=overrides, spot_mode=text("spot_mode"),
            runs=runs if isinstance(runs, int) and not isinstance(runs, bool) else 0,
        )


def _header(text: str) -> str:
    """The comment lines dicomexport writes first, up to the first pasted file or parameter.

    Pasted files keep their own comments, and a table may well contain a line that looks like
    one of these keys; only dicomexport's own header counts.
    """
    lines = []
    for line in text.splitlines():
        if line.startswith("# BEGIN INCLUDE_INLINE:") or (line.strip() and not line.startswith("#")):
            break
        lines.append(line)
    return "\n".join(lines)


def parse(text: str) -> Optional[ConversionRecord]:
    """The record in one field's header, or None for a field without one (dicomexport < 2.0)."""
    text = _header(text)
    values = {m.group("key"): m.group("value") for m in _VALUE_RE.finditer(text)}
    if "CONFIG_DIR" not in values:
        return None
    includes = sorted(
        [(m.start(), Include(m.group("path"))) for m in _INCLUDE_RE.finditer(text)]
        + [(m.start(), Include(m.group("path"), m.group("sha256"))) for m in _INCLUDE_INLINE_RE.finditer(text)],
        key=lambda pair: pair[0],
    )
    version = _VERSION_RE.search(text)
    try:
        runs = int(values.get("RUNS", "0"))
    except ValueError:
        runs = 0
    return ConversionRecord(
        dicomexport=version.group("version") if version else "",
        config_dir=values.get("CONFIG_DIR", ""),
        beam_model=values.get("BEAM_MODEL", ""),
        range_shifter_catalog=values.get("RANGE_SHIFTER_CATALOG", ""),
        includes=[include for _, include in includes],
        material_overrides=[m.group("override") for m in _OVERRIDE_RE.finditer(text)],
        spot_mode=values.get("SPOT_MODE", ""),
        runs=runs,
    )


def of_field(path: str | Path) -> Optional[ConversionRecord]:
    """:func:`parse` of a field file, or None if it cannot be read."""
    try:
        return parse(read_text_lenient(path))
    except OSError:
        return None
