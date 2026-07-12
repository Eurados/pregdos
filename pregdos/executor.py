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
import re
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import fcntl

# Backend identifiers, also written into run.json.
SLURM = "slurm"
LOCAL = "local"

# Per-field status values.
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELED = "canceled"

RUN_METADATA = "run.json"
CANCEL_MARKER = "run.cancelled"
LOCAL_SCHEDULER_LOCK = ".pregdos_local_scheduler.lock"


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


def _cpus_per_task(topas_file: str) -> int:
    """Scheduler CPUs requested for one TOPAS input.

    The structure-mask pre-pass is intentionally single-threaded: it rasterises RTSTRUCT
    masks and writes large binary arrays, but does not need the full clinical transport
    thread count.
    """
    if Path(topas_file).name == "structure_mask_prepass.txt":
        return 1
    return os.cpu_count() or 1


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


def _cancel_marker(run_dir: str | os.PathLike) -> Path:
    return Path(run_dir) / CANCEL_MARKER


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
        f"--cpus-per-task={_cpus_per_task(topas_file)}",
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


def _studies_root_for(run_dir: Path) -> Path:
    """Root containing all studies for this run directory."""
    if run_dir.name.startswith("run_"):
        return run_dir.parent.parent
    return run_dir


def _scheduler_argv(studies_root: Path) -> str:
    q = shlex.quote
    return f"{q(sys.executable)} -m pregdos.executor --start-next {q(str(studies_root))}"


def _local_run_dirs(studies_root: str | os.PathLike) -> List[Path]:
    root = Path(studies_root)
    if not root.is_dir():
        return []
    if (root / RUN_METADATA).exists():
        return [root]
    direct = [p for p in root.iterdir() if p.is_dir() and (p / RUN_METADATA).exists()]
    nested = [
        p
        for study in root.iterdir()
        if study.is_dir()
        for p in study.iterdir()
        if p.is_dir() and p.name.startswith("run_")
    ]
    return sorted({*direct, *nested})


def _local_run_is_queued(run_dir: Path, info: RunInfo) -> bool:
    """A local run has been submitted but its worker process has not started yet."""
    return (
        info.backend == LOCAL
        and bool(info.fields)
        and not _cancel_marker(run_dir).exists()
        and all(not f.ident for f in info.fields)
        and not any((run_dir / exit_code_name(f.topas_file)).exists() for f in info.fields)
    )


def _launch_local_worker(run_dir: Path, studies_root: Path, info: RunInfo) -> None:
    """Run this run's fields **sequentially** in one detached background shell.

    TOPAS is itself multi-threaded, so launching every field at once would oversubscribe a
    workstation badly.  Chaining them with ``;`` also means a field that crashes does not
    prevent the remaining ones from running -- and each still writes its own log and
    exit-code sentinel, so per-field status is unaffected by the sharing of one shell.
    """
    script = "; ".join(field_command(f.topas_file) for f in info.fields)
    script = f"{script}; {_scheduler_argv(studies_root)}"
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
    for job in info.fields:
        job.ident = str(proc.pid)
    _write_run_metadata(run_dir, info)


def start_next_local_run(studies_root: str | os.PathLike) -> Optional[Path]:
    """Start the oldest queued local run if no local run is currently active.

    This is intentionally filesystem-driven.  It can be called by the web process when a
    page is rendered, and by the detached local worker after it finishes a run.  The lock
    prevents two near-simultaneous submissions from starting two local workers.
    """
    root = Path(studies_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LOCAL_SCHEDULER_LOCK
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            queued: list[tuple[str, Path, RunInfo]] = []
            for run_dir in _local_run_dirs(root):
                info = read_run_metadata(run_dir)
                if info is None or info.backend != LOCAL:
                    continue
                status = run_status(run_dir)
                if status == RUNNING:
                    return None
                if status == CANCELED and any(f.ident for f in info.fields):
                    try:
                        os.killpg(int(info.fields[0].ident), 0)
                        return None
                    except (ProcessLookupError, PermissionError, ValueError, OSError):
                        pass
                if _local_run_is_queued(run_dir, info):
                    queued.append((info.submitted, run_dir, info))

            if not queued:
                return None
            _, run_dir, info = min(queued, key=lambda item: (item[0], item[1].name))
            _launch_local_worker(run_dir, root, info)
            return run_dir
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def move_local_run_up(studies_root: str | os.PathLike, target_run_dir: str | os.PathLike) -> bool:
    """Move one queued local run one slot earlier in FIFO order."""
    root = Path(studies_root)
    target = Path(target_run_dir).resolve()
    lock_path = root / LOCAL_SCHEDULER_LOCK
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            queued: list[tuple[str, Path, RunInfo]] = []
            for run_dir in _local_run_dirs(root):
                info = read_run_metadata(run_dir)
                if info is not None and _local_run_is_queued(run_dir, info):
                    queued.append((info.submitted, run_dir, info))
            queued.sort(key=lambda item: (item[0], item[1].name))
            for index, (_, run_dir, info) in enumerate(queued):
                if run_dir.resolve() != target:
                    continue
                if index == 0:
                    return False
                previous_info = queued[index - 1][2]
                previous_dir = queued[index - 1][1]
                info.submitted, previous_info.submitted = previous_info.submitted, info.submitted
                _write_run_metadata(run_dir, info)
                _write_run_metadata(previous_dir, previous_info)
                return True
            return False
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _submit_local(run_dir: Path, topas_files: List[str], info: RunInfo) -> None:
    """Queue a local run and kick the FIFO scheduler."""
    info.fields = [FieldJob(topas_file, "") for topas_file in topas_files]
    _write_run_metadata(run_dir, info)
    start_next_local_run(_studies_root_for(run_dir))
    refreshed = read_run_metadata(run_dir)
    if refreshed is not None:
        info.fields = refreshed.fields


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
    if _cancel_marker(run_dir).exists() and not sentinel.is_file():
        return CANCELED
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
    run_dir = Path(run_dir)
    if _cancel_marker(run_dir).exists():
        return CANCELED
    if _local_run_is_queued(run_dir, info):
        return QUEUED
    statuses = [field_status(run_dir, f.topas_file) for f in info.fields]
    if CANCELED in statuses:
        return CANCELED
    if FAILED in statuses:
        return FAILED
    if RUNNING in statuses:
        return RUNNING
    return COMPLETED


# ---------------------------------------------------------------------------
# Progress -- how far a running field has got, from its log
# ---------------------------------------------------------------------------

# dicomexport delivers the plan as one Geant4 run per spot; TOPAS prints a line per run start.
_RUN_LINE_RE = re.compile(r"Begin processing for Run:\s*(\d+)")
# The per-spot history counts: `uv:Tf/spotWeight/Values = <count> w0 w1 ...`.  Spot k is run k.
_SPOT_WEIGHTS_RE = re.compile(r"uv:Tf/spotWeight/Values\s*=\s*\d+((?:\s+\d+)+)")


@dataclass
class FieldProgress:
    """How far one field has run, for a progress bar.

    Progress is measured in **histories**, not runs: spot weights are very uneven (early
    spots can carry thousands of particles, late spots a handful), so "run 600 of 659" can be
    anywhere from a fifth to nearly all of the work.  Weighting each started run by its spot
    weight makes the bar track the actual compute done.
    """

    topas_file: str
    status: str
    histories_done: int
    histories_total: int
    runs_started: int
    total_runs: int

    @property
    def fraction(self) -> float:
        """Completed fraction in [0, 1].  A cleanly finished field is 1.0 regardless of log."""
        if self.status == COMPLETED:
            return 1.0
        if self.histories_total <= 0:
            return 0.0
        return min(1.0, self.histories_done / self.histories_total)


def _spot_weights(run_dir: Path, topas_file: str) -> List[int]:
    """Per-run history counts from the TOPAS input (weights[k] = histories in run k)."""
    try:
        text = (run_dir / topas_file).read_text()
    except OSError:
        return []
    m = _SPOT_WEIGHTS_RE.search(text)
    return [int(v) for v in m.group(1).split()] if m else []


def _runs_started(run_dir: Path, topas_file: str) -> set:
    """Set of run indices that have appeared in the log.

    Threads process runs concurrently and slightly out of order, so a set of everything seen
    is more honest than the max index -- and lets us sum the right spot weights.
    """
    try:
        text = (run_dir / log_name(topas_file)).read_text()
    except OSError:
        return set()
    return {int(m.group(1)) for m in _RUN_LINE_RE.finditer(text)}


def field_progress(run_dir: str | os.PathLike, topas_file: str) -> FieldProgress:
    """Progress of one field, weighted by histories, from its sentinel, input and log."""
    run_dir = Path(run_dir)
    status = field_status(run_dir, topas_file)
    weights = _spot_weights(run_dir, topas_file)
    total_runs = len(weights)
    histories_total = sum(weights)

    if status == COMPLETED:
        started = set(range(total_runs))
    else:
        # Only count runs we actually have a weight for, so a stray index can't overshoot.
        started = {k for k in _runs_started(run_dir, topas_file) if 0 <= k < total_runs}

    histories_done = sum(weights[k] for k in started)
    return FieldProgress(
        topas_file=topas_file, status=status,
        histories_done=histories_done, histories_total=histories_total,
        runs_started=len(started), total_runs=total_runs,
    )


def run_progress(run_dir: str | os.PathLike) -> List[FieldProgress]:
    """Per-field progress for a whole run, in submission order.  Empty if never submitted."""
    info = read_run_metadata(run_dir)
    if info is None:
        return []
    run_dir = Path(run_dir)
    if _local_run_is_queued(run_dir, info):
        progress = []
        for f in info.fields:
            weights = _spot_weights(run_dir, f.topas_file)
            progress.append(FieldProgress(
                topas_file=f.topas_file,
                status=QUEUED,
                histories_done=0,
                histories_total=sum(weights),
                runs_started=0,
                total_runs=len(weights),
            ))
        return progress
    return [field_progress(run_dir, f.topas_file) for f in info.fields]


def estimate_remaining_seconds(
    progress: List[FieldProgress], submitted: Optional[str],
    now: Optional[datetime.datetime] = None,
) -> Optional[float]:
    """Rough ETA in seconds: linear extrapolation from elapsed time and histories done.

    Deliberately simple -- ``elapsed × (1 − f) / f`` over the whole run's histories.  It
    ignores per-field startup cost and any SLURM queue wait folded into ``submitted``, so it
    is an estimate, not a promise.  Returns None until there is enough to extrapolate from.
    """
    done = sum(p.histories_done for p in progress)
    total = sum(p.histories_total for p in progress)
    if total <= 0 or done <= 0 or done >= total:
        return None
    try:
        started = datetime.datetime.fromisoformat(submitted) if submitted else None
    except (ValueError, TypeError):
        return None
    if started is None:
        return None
    elapsed = ((now or datetime.datetime.now()) - started).total_seconds()
    if elapsed <= 0:
        return None
    fraction = done / total
    return elapsed * (1.0 - fraction) / fraction


def estimate_completion_time(
    progress: List[FieldProgress], submitted: Optional[str],
    now: Optional[datetime.datetime] = None,
) -> Optional[datetime.datetime]:
    """Rough wall-clock finish time, or None if it cannot be estimated yet."""
    now = now or datetime.datetime.now()
    remaining = estimate_remaining_seconds(progress, submitted, now=now)
    if remaining is None:
        return None
    return now + datetime.timedelta(seconds=remaining)


def cancel_run(run_dir: str | os.PathLike) -> None:
    """Best-effort stop of an unfinished run, so its directory can be deleted safely.

    Never raises: a job that already exited, or a pid that has been recycled away, is not
    an error at the call site (a user pressing "delete").
    """
    run_dir = Path(run_dir)
    info = read_run_metadata(run_dir)
    if info is None:
        return
    try:
        _cancel_marker(run_dir).write_text(datetime.datetime.now().isoformat(timespec="seconds") + "\n")
    except OSError:
        pass

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
    start_next_local_run(_studies_root_for(run_dir))


def main(argv: Optional[list[str]] = None) -> int:
    """Small worker entry point used by the local FIFO scheduler."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "--start-next":
        start_next_local_run(argv[1])
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
