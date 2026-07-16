"""Study directory layout, naming, and path resolution.

Everything PregDos knows about one uploaded DICOM study lives in a single directory
under the *studies root*::

    <studies_root>/<study_name>/
        dicom/                    pristine upload -- nothing else is EVER written here
        <spr_table>.txt           copy of the chosen SPR-to-material table
        <beam_model>.csv          copy of the chosen beam model (provenance only)
        run_<YYYYmmdd_HHMMSS>/    one directory per conversion, created by /convert
            topas_field01.txt     generated TOPAS input
            topas_field01.log     TOPAS stdout/stderr
            topas_field01.exit_code
            topas_field01_neutron_<struct>.csv   scorer output
            run.json              execution metadata

Two properties of this layout are load-bearing; changing them will break runs.

**Generate in the directory you will execute in.**  dicomexport bakes the paths it is
given straight into the TOPAS input (``s:Ge/Patient/DicomDirectory`` and ``includeFile``)
and copies no DICOM.  It performs no path resolution -- pass it relative paths and it
writes relative paths.  So we run dicomexport with ``cwd`` set to the run directory and
hand it paths relative to that directory, and we later run TOPAS from the same directory.
Every embedded path is then correct by construction: nothing is rewritten, and the whole
study directory can be moved, tarred, or mounted at a different path inside a container
without breaking.  An absolute path would survive none of those.

**The DICOM lives in its own subdirectory.**  TOPAS's ``TsDicomPatient`` reads every file
in ``DicomDirectory``.  If the run directory were nested inside it, TOPAS would try to
read our generated ``.txt`` and ``.csv`` files as DICOM.  Keeping ``dicom/`` separate
makes the run directory a *sibling* of the data it references.

Routes address studies by **name**, never by filesystem path.  :func:`study_path` and
:func:`run_path` are the only places a name becomes a path, and both refuse to escape the
studies root.
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import List, Tuple

from werkzeug.utils import secure_filename

# Subdirectory of a study holding the untouched uploaded DICOM files.
DICOM_SUBDIR = "dicom"

# Run directories are named run_<YYYYmmdd>_<HHMMSS>, with a numeric suffix appended
# if two conversions of the same study start within the same second.
RUN_PREFIX = "run_"
_RUN_ID_RE = re.compile(r"^run_\d{8}_\d{6}(?:_\d+)?$")

# Upper bound on the `name`, `name_2`, `name_3`, ... search when de-duplicating a study or
# run directory name.  Also bounds the retry loop when another request wins the race.
_MAX_NAME_ATTEMPTS = 1000


class StudyError(ValueError):
    """A study or run name could not be resolved to a path inside the studies root."""


# ---------------------------------------------------------------------------
# Studies root
# ---------------------------------------------------------------------------

def ensure_root(root: str | os.PathLike) -> str | None:
    """Create the studies root if needed.  Return an error string, or None on success."""
    path = Path(root)
    # The studies root holds patient DICOM, so lock it to the owner (0700) -- but only when
    # we create it, so an admin who provisioned a shared dir with deliberate group perms
    # (e.g. /srv/pregdos, 0770) keeps their choice (issue #71).
    newly_created = False
    try:
        path.mkdir(parents=True, exist_ok=False)
        newly_created = True
    except FileExistsError:
        pass
    except OSError as e:
        return f"Cannot create studies folder {str(path)!r}: {e}"
    if newly_created:
        try:
            path.chmod(0o700)
        except OSError:
            pass  # best-effort; some filesystems ignore mode bits
    if not path.is_dir():
        return f"Studies folder path {str(path)!r} exists but is not a directory."
    if not os.access(path, os.W_OK | os.X_OK):
        return f"Studies folder {str(path)!r} is not writable."
    return None


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def safe_study_name(raw: str) -> str:
    """Sanitize a user- or upload-derived study name into a single path component.

    Study names are human-facing (they appear in the UI and in URLs), so we keep the
    uploaded name rather than substituting a UID -- just stripped of anything that could
    escape a directory or confuse a shell.
    """
    name = secure_filename(raw or "")
    if not name:
        raise StudyError("Study name is empty after sanitization.")
    return name


def allocate_study_name(root: str | os.PathLike, raw: str) -> str:
    """Return a study name that does not yet exist under ``root``.

    Re-uploading a study must not silently merge into, or overwrite, the previous one:
    ``headphantom`` becomes ``headphantom_2``, ``headphantom_3``, and so on.
    """
    base = safe_study_name(raw)
    root = Path(root)
    if not (root / base).exists():
        return base
    for n in range(2, _MAX_NAME_ATTEMPTS):
        candidate = f"{base}_{n}"
        if not (root / candidate).exists():
            return candidate
    raise StudyError(f"Too many studies named {base!r}.")


# ---------------------------------------------------------------------------
# Path resolution -- the only place a name becomes a path
# ---------------------------------------------------------------------------

def study_path(root: str | os.PathLike, name: str) -> Path:
    """Resolve ``<root>/<name>``, refusing anything that escapes ``root``.

    ``secure_filename`` already strips ``..`` and separators, so the containment check
    below is belt-and-braces -- it also catches a symlinked study directory.
    """
    root_abs = Path(root).resolve()
    candidate = (root_abs / safe_study_name(name)).resolve()
    if candidate.parent != root_abs:
        raise StudyError(f"Invalid study name: {name!r}")
    return candidate


def dicom_path(root: str | os.PathLike, name: str) -> Path:
    """Directory holding the study's pristine DICOM files."""
    return study_path(root, name) / DICOM_SUBDIR


def run_path(root: str | os.PathLike, name: str, run_id: str) -> Path:
    """Resolve one conversion's run directory inside a study."""
    if not _RUN_ID_RE.match(run_id or ""):
        raise StudyError(f"Invalid run id: {run_id!r}")
    return study_path(root, name) / run_id


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def create_study(root: str | os.PathLike, raw_name: str) -> Tuple[str, Path]:
    """Create a fresh study directory (with its empty ``dicom/``).

    Returns the allocated name and the study path.  The name may differ from ``raw_name``
    if a study of that name already existed -- see :func:`allocate_study_name`.

    ``mkdir()`` *is* the claim on the name: it fails atomically if another request won the
    race, and we then re-derive the next free name.  Asking ``exists()`` first and creating
    afterwards would let two concurrent uploads agree on the same name -- Flask serves
    requests in threads, so that is reachable, not theoretical.
    """
    for _ in range(_MAX_NAME_ATTEMPTS):
        name = allocate_study_name(root, raw_name)
        path = Path(root) / name
        try:
            path.mkdir(parents=True)  # parents for the studies root; the leaf must be new
        except FileExistsError:
            continue  # lost the race for this name; try the next one
        (path / DICOM_SUBDIR).mkdir()
        return name, path
    raise StudyError(f"Could not allocate a directory for study {safe_study_name(raw_name)!r}.")


def create_run(root: str | os.PathLike, name: str) -> Tuple[str, Path]:
    """Create a fresh, empty run directory for one conversion.

    Because every conversion gets a directory that did not exist a moment ago, the
    generated TOPAS files are simply *the contents of that directory*.  There is nothing
    to glob across, nothing to deduplicate, and no way for a previous run's output to be
    mistaken for this one's -- which is what issue #41 was about.

    That guarantee rests on the directory being genuinely new, so -- as in
    :func:`create_study` -- the ``mkdir()`` is the claim.  Two conversions of one study
    starting in the same second get ``run_<stamp>`` and ``run_<stamp>_2``.
    """
    study = study_path(root, name)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{RUN_PREFIX}{stamp}"
    for suffix in ("", *(f"_{n}" for n in range(2, _MAX_NAME_ATTEMPTS))):
        path = study / (base + suffix)
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path.name, path
    raise StudyError("Too many runs started in the same second.")


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_studies(root: str | os.PathLike) -> List[str]:
    """Names of all studies under ``root``, newest-looking last (plain alphabetical)."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / DICOM_SUBDIR).is_dir())


def list_runs(root: str | os.PathLike, name: str) -> List[str]:
    """Run ids for one study, most recent first (ids sort chronologically by construction)."""
    study = study_path(root, name)
    if not study.is_dir():
        return []
    return sorted(
        (p.name for p in study.iterdir() if p.is_dir() and _RUN_ID_RE.match(p.name)),
        reverse=True,
    )


def find_rtstruct(root: str | os.PathLike, name: str) -> Path | None:
    """Locate the study's RTSTRUCT file.

    Searched recursively: a ZIP or folder upload may nest the DICOM one level deep
    (``dicom/<study_folder>/RS*.dcm``), which is harmless -- dicomexport globs for
    ``**/RD*.dcm`` the same way.
    """
    matches = sorted(dicom_path(root, name).rglob("RS*.dcm"))
    return matches[0] if matches else None


def find_rtplan(root: str | os.PathLike, name: str) -> Path | None:
    """Locate the study's RTPLAN file (``RN*.dcm`` for an RT Ion Plan)."""
    matches = sorted(dicom_path(root, name).rglob("RN*.dcm"))
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Relative paths for the "generate where you execute" invariant
# ---------------------------------------------------------------------------

def relative_to_run(target: str | os.PathLike, run_dir: str | os.PathLike) -> str:
    """Express ``target`` relative to ``run_dir`` (e.g. ``../dicom``).

    Every path handed to dicomexport goes through here.  dicomexport copies its arguments
    verbatim into the generated TOPAS input, so a relative argument in means a relative
    path baked into the file -- which is exactly what keeps the study directory movable.
    """
    return os.path.relpath(os.fspath(target), os.fspath(run_dir))
