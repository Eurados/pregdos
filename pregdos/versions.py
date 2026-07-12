"""Discover the versions of the external tools PregDos runs against.

Sources, in order of precedence:

1. An explicit ``TOPAS_VERSION`` / ``GEANT4_VERSION`` environment variable.
2. A ``/etc/pregdos/<NAME>`` marker file, written by the Docker image at build time.
3. The runtime itself -- ``topas --version``, and the ``Geant4-<version>`` directory beside
   the Geant4 libraries TOPAS is linked against.

The build-time sources come first because they are authoritative about *what was installed*,
whereas the runtime answer can be wrong: **OpenTOPAS 4.0.0 reports its version as "3.9"**.
That is not a legacy TOPAS 3.9 -- it is a 4.0.0 build that misreports, and it carries the
multithreaded scorer bug that corrupts every dose (see issue #49).  4.2.3 reports "4.2.p3"
honestly.

Nothing here raises: a missing binary or a slow NFS mount yields ``"unknown"``, never a 500.
"""

from __future__ import annotations

import functools
import importlib.metadata
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

UNKNOWN = "unknown"

# Minimum OpenTOPAS that reports a trustworthy scorer Sum and Standard_Deviation (#49).
MINIMUM_TOPAS = (4, 2, 3)

# Minimum dicomexport that aims the beam at the right side of the patient.  Up to and including
# 1.4.3 the ``--nozzle-side`` default put the source on the far side of the isocenter, mirroring
# every field by 180 deg: the dose landed ~9 cm off, so a target read milligray instead of gray
# while still looking plausible (dicomexport #66).  Nothing downstream can detect that, which is
# why it is checked here rather than trusted to the install.
MINIMUM_DICOMEXPORT = (1, 4, 4)

MARKER_DIR = Path("/etc/pregdos")

# "4.2.p3" -> (4, 2, 3);  "3.9" -> (3, 9);  "11.3.2" -> (11, 3, 2)
_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.p?(\d+))?")

# The versioned Geant4 directory sits next to the libG4*.so files, e.g.
# /opt/geant4-install/lib/Geant4-11.3.2
_GEANT4_DIR_RE = re.compile(r"^Geant4-(?P<version>[\d.]+)$")


def _explicit(env_name: str) -> Optional[str]:
    """Env var, else the Docker build marker file.  None when neither is set."""
    value = (os.environ.get(env_name) or "").strip()
    if value:
        return value
    try:
        marker = MARKER_DIR / env_name
        if marker.is_file():
            value = marker.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        pass
    return None


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """Turn a reported version string into a comparable tuple, or None."""
    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def topas_bin() -> str:
    return os.environ.get("TOPAS_BIN", "topas")


@functools.lru_cache(maxsize=1)
def topas_version() -> str:
    """Version of the TOPAS binary on PATH.

    ``topas --version`` prints just the version and exits.  (``-V`` is *not* a version flag:
    TOPAS treats it as a parameter-file name and tries to open it.)
    """
    explicit = _explicit("TOPAS_VERSION")
    if explicit:
        return explicit

    binary = topas_bin()
    if not shutil.which(binary):
        return UNKNOWN
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    out = (proc.stdout or proc.stderr or "").strip()
    return out.splitlines()[0].strip() if out else UNKNOWN


@functools.lru_cache(maxsize=1)
def geant4_version() -> str:
    """Version of the Geant4 that TOPAS is linked against.

    ``geant4-config`` is usually not installed alongside a manual build, so fall back to
    locating the ``Geant4-<version>`` directory that sits beside the linked ``libG4*.so``.
    """
    explicit = _explicit("GEANT4_VERSION")
    if explicit:
        return explicit

    if shutil.which("geant4-config"):
        try:
            proc = subprocess.run(["geant4-config", "--version"], capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass

    for lib_dir in _linked_library_dirs():
        for entry in lib_dir.iterdir():
            if entry.is_dir() and (m := _GEANT4_DIR_RE.match(entry.name)):
                return m.group("version")
    return UNKNOWN


def _linked_library_dirs():
    """Directories holding the Geant4 libraries TOPAS links against."""
    binary = shutil.which(topas_bin())
    if not binary:
        return
    try:
        proc = subprocess.run(["ldd", binary], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return
    seen = set()
    for line in proc.stdout.splitlines():
        if "libG4" not in line or "=>" not in line:
            continue
        path = line.split("=>", 1)[1].strip().split(" ")[0]
        if not path.startswith("/"):
            continue
        parent = Path(path).parent
        if parent not in seen and parent.is_dir():
            seen.add(parent)
            try:
                yield parent
            except OSError:
                continue


def topas_warning() -> Optional[str]:
    """Why the installed TOPAS must not be trusted, or None when it is fine.

    Kept separate from the version string so the About page can show *what is installed*
    and *whether it is usable* as two different facts.
    """
    reported = topas_version()
    if reported == UNKNOWN:
        return "TOPAS was not found on PATH — simulations cannot run."

    parsed = parse_version(reported)
    if parsed is None:
        return f"Could not interpret the reported TOPAS version {reported!r}."

    if parsed[:2] == (3, 9):
        # OpenTOPAS 4.0.0 misreports itself as 3.9, so we cannot tell the two apart.
        return ("This build reports version 3.9. Either it is legacy TOPAS 3.9, or it is "
                "OpenTOPAS 4.0.0, which misreports its version. Both corrupt the scorer Sum "
                f"under multithreading (issue #49). Upgrade to OpenTOPAS "
                f"{'.'.join(map(str, MINIMUM_TOPAS))} or newer.")

    if parsed < MINIMUM_TOPAS:
        return (f"OpenTOPAS {reported} is older than "
                f"{'.'.join(map(str, MINIMUM_TOPAS))}, whose multithreaded scorer merge "
                "corrupts the reported Sum and under-estimates the uncertainty (issue #49).")
    return None


def dicomexport_version() -> str:
    """Version of the installed ``dicomexport`` package, or ``"unknown"``."""
    try:
        return importlib.metadata.version("dicomexport")
    except importlib.metadata.PackageNotFoundError:
        return UNKNOWN


def dicomexport_warning() -> Optional[str]:
    """Why the installed dicomexport must not be trusted, or None when it is fine."""
    reported = dicomexport_version()
    minimum = ".".join(map(str, MINIMUM_DICOMEXPORT))
    parsed = parse_version(reported)
    if parsed is None:
        return f"dicomexport version cannot be determined. PregDos requires {minimum} or newer."
    if parsed < MINIMUM_DICOMEXPORT:
        return (f"dicomexport {reported} is older than {minimum}, which mirrors every proton "
                "field 180 deg about the isocenter: the dose lands on the wrong side of the "
                "patient (dicomexport #66).")
    return None


def g4_data_dir_problem() -> Optional[str]:
    """Why ``TOPAS_G4_DATA_DIR`` will make Geant4 abort, or None.

    An unset variable is fine — Geant4 then uses its build-time default.  A variable pointing
    at a directory that does not exist is not fine: every run aborts with
    ``ENSDFSTATE.dat is not found``, seconds after submission.
    """
    value = (os.environ.get("TOPAS_G4_DATA_DIR") or "").strip()
    if not value:
        return None
    if not Path(value).is_dir():
        return f"TOPAS_G4_DATA_DIR points at {value!r}, which does not exist. Geant4 will abort on every run."
    return None


def submit_blocker() -> Optional[str]:
    """A reason to refuse launching a run now, or None.

    Only *definite* problems block a submission:

    * ``TOPAS_G4_DATA_DIR`` points at a missing directory -- Geant4 aborts every run seconds
      in (the stale-environment failure from issue #52's neighbourhood).
    * TOPAS reports a version we can read and it is below the #49 minimum -- every scorer Sum
      would come out NaN.
    * dicomexport is below the #66 minimum (or unreadable) -- every field would be mirrored
      about the isocenter, putting the dose on the wrong side of the patient.

    An **unknown** TOPAS version does *not* block: under the SLURM backend the binary runs on
    a compute node, not on the webserver host, so the host's ``topas --version`` (or its
    absence) is not authoritative.  The About page still surfaces that as a warning.
    """
    g4 = g4_data_dir_problem()
    if g4:
        return g4
    # dicomexport always runs on this host, so its version *is* authoritative -- and a mirrored
    # beam produces a wrong dose that looks entirely plausible, so an unreadable version blocks
    # too.  This is the one failure nothing downstream can catch (dicomexport #66).
    dicomexport = dicomexport_warning()
    if dicomexport:
        return dicomexport
    # topas_warning() is None when supported, a message when the *parsed* version is too old.
    if parse_version(topas_version()) is not None:
        return topas_warning()
    return None


def summary() -> dict:
    """Everything the About page needs, in one call."""
    return {
        "topas": topas_version(),
        "geant4": geant4_version(),
        "topas_warning": topas_warning(),
        "g4_data_dir": os.environ.get("TOPAS_G4_DATA_DIR") or "",
        "g4_data_dir_problem": g4_data_dir_problem(),
        "minimum_topas": ".".join(map(str, MINIMUM_TOPAS)),
        "dicomexport_warning": dicomexport_warning(),
        "minimum_dicomexport": ".".join(map(str, MINIMUM_DICOMEXPORT)),
    }
