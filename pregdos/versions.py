"""Discover the versions of the external tools PregDos runs against.

Sources, in order of precedence:

1. An explicit ``TOPAS_VERSION`` / ``GEANT4_VERSION`` environment variable.
2. A ``/etc/pregdos/<NAME>`` marker file, written by the Docker image at build time.
3. The runtime itself -- ``topas --version``, and the ``Geant4-<version>`` directory beside
   the Geant4 libraries TOPAS is linked against.

The build-time sources come first because they are authoritative about *what was installed*,
whereas the runtime answer can be less authoritative than the build marker.  PregDos only
supports OpenTOPAS 4.2.3 or newer because older builds can corrupt multithreaded scorer
statistics (issue #49).

Nothing here raises: a missing binary or a slow NFS mount yields ``"unknown"``, never a 500.
"""

from __future__ import annotations

import functools
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

import requests

from . import config

UNKNOWN = "unknown"

# Minimum OpenTOPAS that reports a trustworthy scorer Sum and Standard_Deviation (#49).
MINIMUM_TOPAS = (4, 2, 3)

# Minimum dicomexport PregDos will compute with.  1.5.0 brought the field-numbering contract
# -- output fields named by DICOM BeamNumber, setup beams without meterset skipped
# (dicomexport #75) -- which PregDos relies on for field labels and RTDOSE attribution.
# 1.5.1 corrected two catalogued range shifter thicknesses (dicomexport #92): an older
# install computes a different range for CCB and WPE plans, and silently, since nothing in
# the output says which catalog produced it.  That is a dose error, not an interface
# mismatch, which is why the floor moves for a patch release.
MINIMUM_DICOMEXPORT = (1, 5, 1)

MARKER_DIR = Path("/etc/pregdos")

# "4.2.p3" -> (4, 2, 3);  "4.2" -> (4, 2);  "11.3.2" -> (11, 3, 2)
_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.p?(\d+))?")

# The versioned Geant4 directory sits next to the libG4*.so files, e.g.
# /opt/geant4-install/lib/Geant4-11.3.2
_GEANT4_DIR_RE = re.compile(r"^Geant4-(?P<version>[\d.]+)$")
_PREGDOS_RELEASES_URL = "https://api.github.com/repos/Eurados/pregdos/releases/latest"


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


# Printed by the probe shell between the site prologue and the command being measured, so a
# `module load` that greets the user -- or a login profile that prints a banner -- cannot have
# its chatter parsed as a TOPAS version.
_PROBE_MARKER = "__pregdos_probe__"


def _config() -> config.Config:
    """The site config, or the built-in defaults when it cannot be read.

    Nothing in this module raises (see the module docstring), and that has to hold for the
    config file too.  ``pregdos-web`` validates it at startup and refuses to run on a bad
    one, but an import-based deployment (``gunicorn pregdos.wsgi:app``) never calls
    ``main()`` -- and there, a malformed file should degrade the About page to "unknown"
    rather than turn it into a 500.
    """
    try:
        return config.load()
    except config.ConfigError:
        return config.Config()


def _update_check_enabled() -> bool:
    """Whether the About page may ask GitHub for a newer release.

    Unlike everything else here, an unreadable config falls back to *disabled* rather than to
    the built-in default of enabled: a file PregDos cannot parse is not permission to make an
    outbound request, and on an airgapped node that request can only ever cost a timeout.
    """
    try:
        return config.load().network.update_check
    except config.ConfigError:
        return False


def _prologue() -> str:
    """``[scheduler] prologue``: the shell source that puts TOPAS on PATH at a modules site.

    Kept in step with :func:`executor._with_prologue` on purpose.  The About page is
    provenance for a clinical report, so it must measure the TOPAS that will actually run,
    not the one visible to the web process -- at a site where TOPAS lives behind
    ``module load``, those are different, and the web process sees nothing at all.
    """
    return _config().scheduler.prologue.strip()


def _shell_probe(command: str, timeout: int = 30) -> Tuple[int, str]:
    """``(status, output)`` of ``command`` run in the shell that will run TOPAS.

    Only reached when a prologue is configured: without one there is nothing to set up, and
    the direct call is both cheaper and easier to reason about.
    """
    script = f"{_prologue()}\nprintf '%s\\n' {_PROBE_MARKER}\n{command}\n"
    try:
        proc = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    # No marker means the prologue died before the command ran -- a bad module name, say.
    # Its own output is not an answer, so report nothing rather than something wrong.
    if _PROBE_MARKER not in proc.stdout:
        return proc.returncode or 127, ""
    return proc.returncode, proc.stdout.split(_PROBE_MARKER, 1)[1].strip()


def _resolve(binary: str) -> Optional[str]:
    """Absolute path of ``binary`` as the shell that runs TOPAS resolves it, or None."""
    if not _prologue():
        return shutil.which(binary)
    status, out = _shell_probe(f"command -v {shlex.quote(binary)}")
    return out.splitlines()[0].strip() if status == 0 and out.strip() else None


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """Turn a reported version string into a comparable tuple, or None."""
    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def topas_bin() -> str:
    """``TOPAS_BIN``, else ``[paths] topas_bin``.  Mirrors executor.topas_bin."""
    return config.env_or("TOPAS_BIN", _config().paths.topas_bin)


@functools.lru_cache(maxsize=1)
def topas_version() -> str:
    """Version of the TOPAS binary on PATH.

    ``topas --version`` prints just the version and exits.  (``-V`` is *not* a version flag:
    TOPAS treats it as a parameter-file name and tries to open it.)
    """
    explicit = _explicit("TOPAS_VERSION")
    if explicit:
        return explicit

    binary = _resolve(topas_bin())
    if not binary:
        return UNKNOWN

    if _prologue():
        # 2>&1 because TOPAS is not consistent about which stream it prints to, and the
        # marker has already separated this from anything the prologue said.
        _, out = _shell_probe(f"{shlex.quote(binary)} --version 2>&1")
    else:
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

    geant4_config = _resolve("geant4-config")
    if geant4_config:
        if _prologue():
            status, out = _shell_probe(f"{shlex.quote(geant4_config)} --version")
            if status == 0 and out:
                return out.splitlines()[0].strip()
        else:
            try:
                proc = subprocess.run([geant4_config, "--version"], capture_output=True, text=True, timeout=30)
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
    binary = _resolve(topas_bin())
    if not binary:
        return
    # ldd resolves against LD_LIBRARY_PATH, which at a modules site is exactly what the
    # prologue sets -- run it outside and every libG4 line reads "not found".
    if _prologue():
        _, output = _shell_probe(f"ldd {shlex.quote(binary)}")
    else:
        try:
            proc = subprocess.run(["ldd", binary], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return
        output = proc.stdout
    seen = set()
    for line in output.splitlines():
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
        if _prologue():
            return ("TOPAS was not found on PATH, even with the [scheduler] prologue applied — "
                    "simulations cannot run.  Check that the prologue names a module that exists "
                    "and that it sources the module system's init script first.")
        return "TOPAS was not found on PATH — simulations cannot run."

    parsed = parse_version(reported)
    if parsed is None:
        return f"Could not interpret the reported TOPAS version {reported!r}."

    if parsed < MINIMUM_TOPAS:
        return (f"OpenTOPAS {reported} is older than "
                f"{'.'.join(map(str, MINIMUM_TOPAS))}, whose multithreaded scorer merge "
                "corrupts the reported Sum and under-estimates the uncertainty (issue #49).")
    return None


def dicomexport_version() -> str:
    """Canonical version of the installed ``dicomexport`` package, or ``"unknown"``."""
    return canonical_package_version("dicomexport")


def package_version(name: str) -> str:
    """Installed Python package version, or ``"unknown"``."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return UNKNOWN


def _git_value(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if proc.returncode != 0:
        return UNKNOWN
    return proc.stdout.strip() or UNKNOWN


def _git_state(repo_root: Path) -> str:
    status = _git_value(["status", "--short"], repo_root)
    if status == UNKNOWN:
        return UNKNOWN
    return "dirty" if status else "clean"


def _dist_git_commit(name: str) -> str:
    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return UNKNOWN
    try:
        direct_url = dist.read_text("direct_url.json")
    except OSError:
        return UNKNOWN
    if not direct_url:
        return UNKNOWN
    try:
        data = json.loads(direct_url)
    except json.JSONDecodeError:
        return UNKNOWN
    commit = data.get("vcs_info", {}).get("commit_id")
    return commit[:8] if commit else UNKNOWN


def canonical_package_version(name: str, repo_root: Path | None = None) -> str:
    """Package version with a git local-version suffix when available.

    For editable/local checkouts, pass ``repo_root`` to read the current git commit and dirty
    state. For VCS-installed packages, ``direct_url.json`` supplies the install commit.
    """
    version = package_version(name)
    if "+" in version or version == UNKNOWN:
        return version

    git_state = UNKNOWN
    if repo_root is not None:
        commit = _git_value(["rev-parse", "--short=8", "HEAD"], repo_root)
        git_state = _git_state(repo_root)
    else:
        commit = _dist_git_commit(name)
    if commit == UNKNOWN:
        return version

    local = f"g{commit}"
    if git_state == "dirty":
        local += ".dirty"
    return f"{version}+{local}"


def latest_pregdos_release() -> str:
    """Latest GitHub release tag for PregDos, or ``"unknown"``.

    The ``[network] update_check`` gate lives here, *outside* the cache on
    :func:`_fetch_latest_release`: inside it, the first call would pin its answer for the
    lifetime of the process regardless of what the config says afterwards.
    """
    if not _update_check_enabled():
        return UNKNOWN
    return _fetch_latest_release()


@functools.lru_cache(maxsize=1)
def _fetch_latest_release() -> str:
    """Ask GitHub.  Best-effort and short-timeout: the About page must not become slow or
    fail just because GitHub or the network is unavailable.
    """
    try:
        response = requests.get(
            _PREGDOS_RELEASES_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=1.5,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return UNKNOWN
    tag = str(data.get("tag_name") or "").strip()
    return tag or UNKNOWN


def newer_pregdos_release(current: str, latest: str) -> bool:
    """Whether ``latest`` names a newer PregDos release than ``current``."""
    current_version = parse_version(current)
    latest_version = parse_version(latest)
    if current_version is None or latest_version is None:
        return False
    return latest_version > current_version


def dicomexport_warning() -> Optional[str]:
    """Why the installed dicomexport must not be trusted, or None when it is fine."""
    reported = dicomexport_version()
    minimum = ".".join(map(str, MINIMUM_DICOMEXPORT))
    parsed = parse_version(reported)
    if parsed is None:
        return f"dicomexport version cannot be determined. PregDos requires {minimum} or newer."
    if parsed < MINIMUM_DICOMEXPORT:
        return (f"dicomexport {reported} is older than {minimum}: PregDos relies on the "
                "BeamNumber field-output contract for field labels and RTDOSE attribution "
                "(dicomexport #75), and on the corrected CCB and WPE range shifter "
                "thicknesses (dicomexport #92) -- an older install computes a different "
                "range for those centres.")
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
    * dicomexport is below the supported minimum (or unreadable) -- fields would be numbered
      under the wrong contract.

    An **unknown** TOPAS version does *not* block: under the SLURM backend the binary runs on
    a compute node, not on the webserver host, so the host's ``topas --version`` (or its
    absence) is not authoritative.  The About page still surfaces that as a warning.
    """
    g4 = g4_data_dir_problem()
    if g4:
        return g4
    # dicomexport always runs on this host, so its version *is* authoritative -- and a field
    # numbered under the wrong contract produces a result that looks entirely plausible, so an
    # unreadable version blocks too.  Nothing downstream can catch that.
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
