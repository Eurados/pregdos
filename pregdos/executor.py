"""Run generated TOPAS input files, on SLURM or directly on this machine.

PregDos is used both inside the Docker image (which ships a SLURM controller) and on a
plain workstation that has nothing but a TOPAS installation.  This module hides that
difference behind :func:`submit_run`, choosing a backend automatically.

Completion state is a **file on disk**, never an in-process handle
--------------------------------------------------------------------
Both backends execute the *same* wrapped shell command for each field::

    topas <field>.txt > <field>.log 2>&1; echo $? > <field>.exit_code

so the rules for reading job state are identical either way:

* ``<field>.exit_code`` exists  -> the field finished; ``0`` is success, anything else is a
  failure whose diagnostics are in ``<field>.log``
* otherwise                     -> the field is still queued or running

Nothing is remembered in the Flask process, so status survives a webserver restart, and
there are no pids to poll and no zombies to reap.  The recorded SLURM job id / pid is used
only to cancel a run, never to decide whether it finished.

Working directory
-----------------
Every command runs with ``cwd`` set to the run directory.  The generated TOPAS input
references its DICOM and SPR table by paths relative to that directory (see
:mod:`pregdos.studies`), so running it from anywhere else would not resolve.
"""

from __future__ import annotations

import datetime
import json
import os
import shlex
import shutil
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Backend identifiers, also written into run.json.
SLURM = "slurm"
LOCAL = "local"

# Per-field status values.
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

RUN_METADATA = "run.json"


def topas_bin() -> str:
    """Path or name of the TOPAS executable.  Read lazily so tests can patch the env."""
    return os.environ.get("TOPAS_BIN", "topas")


def _requested_backend() -> str:
    """``PREGDOS_EXECUTOR`` = ``auto`` (default) | ``slurm`` | ``local``."""
    return (os.environ.get("PREGDOS_EXECUTOR") or "auto").strip().lower()


def slurm_available() -> bool:
    """True when jobs can plausibly be handed to SLURM.

    We only check that ``sbatch`` is on PATH.  If the controller happens to be down,
    ``sbatch`` fails and its stderr is surfaced to the user -- which is more informative
    than a health probe on every submit.
    """
    return shutil.which("sbatch") is not None


def select_backend() -> str:
    """Resolve the configured backend, falling back to local execution."""
    requested = _requested_backend()
    if requested in (SLURM, LOCAL):
        return requested
    return SLURM if slurm_available() else LOCAL


# ---------------------------------------------------------------------------
# The wrapped command -- identical for both backends
# ---------------------------------------------------------------------------

def log_name(topas_file: str) -> str:
    return f"{Path(topas_file).stem}.log"


def exit_code_name(topas_file: str) -> str:
    return f"{Path(topas_file).stem}.exit_code"


def field_command(topas_file: str) -> str:
    """Shell command running one field and recording its exit code next to its log.

    ``echo $?`` captures the exit status of TOPAS itself (the redirections do not change
    it), so the sentinel distinguishes a clean finish from a crash.
    """
    q = shlex.quote
    return (
        f"{q(topas_bin())} {q(topas_file)} > {q(log_name(topas_file))} 2>&1; "
        f"echo $? > {q(exit_code_name(topas_file))}"
    )


# ---------------------------------------------------------------------------
# Run metadata
# ---------------------------------------------------------------------------

@dataclass
class FieldJob:
    """One TOPAS input file handed to a backend."""

    topas_file: str
    ident: str
    """SLURM job id, or the pid of the local shell.  Used only for cancellation."""


@dataclass
class RunInfo:
    backend: str
    submitted: str
    fields: List[FieldJob] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def _write_run_metadata(run_dir: Path, info: RunInfo) -> None:
    payload = {
        "backend": info.backend,
        "submitted": info.submitted,
        "fields": [{"topas_file": f.topas_file, "ident": f.ident} for f in info.fields],
    }
    (run_dir / RUN_METADATA).write_text(json.dumps(payload, indent=2) + "\n")


def read_run_metadata(run_dir: str | os.PathLike) -> Optional[RunInfo]:
    """Load ``run.json``, or None when the run was never submitted.

    Every caller is a page render or a status poll, so this must never raise on a file that
    is corrupt, truncated by a crash, or half-written while we read it.  Anything we cannot
    make sense of is skipped; a metadata file with no usable fields reads as "not submitted"
    rather than taking the jobs page down with a 500.
    """
    path = Path(run_dir) / RUN_METADATA
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    fields: List[FieldJob] = []
    entries = raw.get("fields")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        topas_file = entry.get("topas_file")
        if not isinstance(topas_file, str) or not topas_file:
            continue
        fields.append(FieldJob(topas_file, str(entry.get("ident", ""))))

    backend = raw.get("backend")
    submitted = raw.get("submitted")
    return RunInfo(
        backend=backend if backend in (SLURM, LOCAL) else LOCAL,
        submitted=submitted if isinstance(submitted, str) else "",
        fields=fields,
    )


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

def _sbatch_argv(run_dir: Path, topas_file: str) -> List[str]:
    """Build the sbatch invocation for one field.

    SLURM executes jobs as the ``slurm`` user, so when we are root (as in the container)
    we drop privileges with ``runuser``.  Outside the container ``runuser`` is typically
    absent from PATH and we are not root anyway, so we call ``sbatch`` directly.
    """
    argv: List[str] = []
    runuser = shutil.which("runuser") or "/usr/sbin/runuser"
    if os.geteuid() == 0 and os.path.exists(runuser):
        argv += [runuser, "-u", "slurm", "--"]
    argv += [
        "sbatch",
        "--export=ALL",
        f"--cpus-per-task={os.cpu_count() or 1}",
        f"--chdir={run_dir}",
        f"--output={run_dir}/slurm-%j.out",
        "--wrap", field_command(topas_file),
    ]
    return argv


def _submit_slurm(run_dir: Path, topas_files: List[str], info: RunInfo) -> None:
    """One sbatch per field; the scheduler decides how many run concurrently."""
    for topas_file in topas_files:
        result = subprocess.run(_sbatch_argv(run_dir, topas_file), capture_output=True, text=True)
        if result.returncode == 0:
            info.fields.append(FieldJob(topas_file, result.stdout.strip().split()[-1]))
        else:
            info.errors.append(f"sbatch failed for {topas_file}: {result.stderr.strip()}")


def _submit_local(run_dir: Path, topas_files: List[str], info: RunInfo) -> None:
    """Run the fields **sequentially** in one detached background shell.

    TOPAS is itself multi-threaded, so launching every field at once would oversubscribe a
    workstation badly.  Chaining them with ``;`` also means a field that crashes does not
    prevent the remaining ones from running -- and each still writes its own log and
    exit-code sentinel, so per-field status is unaffected by the sharing of one shell.
    """
    script = "; ".join(field_command(f) for f in topas_files)
    proc = subprocess.Popen(
        ["sh", "-c", script],
        cwd=str(run_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Detach from the webserver's process group: the run must outlive a Ctrl-C or a
        # reload of the Flask dev server.
        start_new_session=True,
    )
    for topas_file in topas_files:
        info.fields.append(FieldJob(topas_file, str(proc.pid)))


def submit_run(run_dir: str | os.PathLike, topas_files: List[str]) -> RunInfo:
    """Execute every field of a conversion, in the directory it was generated in.

    Returns a :class:`RunInfo` describing what was launched; any per-field submission
    failure is collected in ``info.errors`` rather than raised, so a partially submitted
    run still records what did start.
    """
    run_dir = Path(run_dir)
    backend = select_backend()
    info = RunInfo(backend=backend, submitted=datetime.datetime.now().isoformat(timespec="seconds"))

    if backend == SLURM:
        _submit_slurm(run_dir, topas_files, info)
    else:
        _submit_local(run_dir, topas_files, info)

    if info.fields:
        _write_run_metadata(run_dir, info)
    return info


# ---------------------------------------------------------------------------
# Status -- derived from files on disk
# ---------------------------------------------------------------------------

def field_status(run_dir: str | os.PathLike, topas_file: str) -> str:
    """Status of one field, read from its exit-code sentinel."""
    sentinel = Path(run_dir) / exit_code_name(topas_file)
    if not sentinel.is_file():
        return RUNNING
    try:
        code = int(sentinel.read_text().strip())
    except (OSError, ValueError):
        # A sentinel we cannot parse means the wrapper died mid-write; treat as failure
        # rather than pretending the run is still going.
        return FAILED
    return COMPLETED if code == 0 else FAILED


def run_status(run_dir: str | os.PathLike) -> str:
    """Aggregate status of a whole run.

    A run has failed if any field failed, is still running while any field is unfinished,
    and is completed only when every field exited cleanly.
    """
    info = read_run_metadata(run_dir)
    if info is None or not info.fields:
        return QUEUED
    statuses = [field_status(run_dir, f.topas_file) for f in info.fields]
    if FAILED in statuses:
        return FAILED
    if RUNNING in statuses:
        return RUNNING
    return COMPLETED


def cancel_run(run_dir: str | os.PathLike) -> None:
    """Best-effort stop of an unfinished run, so its directory can be deleted safely.

    Never raises: a job that already exited, or a pid that has been recycled away, is not
    an error at the call site (a user pressing "delete").
    """
    info = read_run_metadata(run_dir)
    if info is None:
        return

    if info.backend == SLURM:
        idents = sorted({f.ident for f in info.fields if f.ident})
        if idents and shutil.which("scancel"):
            subprocess.run(["scancel", *idents], capture_output=True, text=True)
        return

    # Local backend: the fields share one detached shell, whose pid became a process-group
    # leader via start_new_session.  Killing the group takes TOPAS down with the shell.
    for pid in sorted({f.ident for f in info.fields if f.ident}):
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, ValueError, OSError):
            pass
