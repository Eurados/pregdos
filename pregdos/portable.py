"""OS-portability shims so PregDos runs on a Windows workstation as well as on Linux.

PregDos is developed and deployed on Linux -- the Docker image ships a SLURM controller --
but people also run the *local* backend on a plain Windows machine that has nothing but a
TOPAS install.  A handful of primitives PregDos relies on are POSIX-only and simply do not
exist under that name on Windows: advisory file locks (``fcntl``), process groups
(``os.killpg`` / ``start_new_session``), and the ``/proc`` filesystem used to tell whether a
detached worker is still alive.  Importing :mod:`fcntl` at module scope was enough to make
``pregdos-web`` fail to start on Windows before it served a single page (issue #91).

Everything platform-specific is funnelled through this module, so the rest of the codebase
stays free of ``if platform`` branches.  On Linux each helper keeps the exact behaviour the
code always had; on Windows it does the nearest equivalent, or the least harmful thing.  The
platform is selected with ``sys.platform == "win32"`` so a static type checker narrows the
Windows-only symbols correctly on either OS.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Iterator, Optional

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

# A generous ceiling on how long to wait for a lock.  The locks here only serialise PregDos's
# own processes over sub-second critical sections, so contention is always brief; the timeout
# exists so a wedged holder fails loudly instead of hanging a page render forever.
_LOCK_TIMEOUT_S = 300.0


@contextlib.contextmanager
def exclusive_lock(handle: IO) -> Iterator[None]:
    """Hold an exclusive advisory lock on an open file for the duration of the ``with`` block.

    POSIX uses ``flock``; Windows uses ``msvcrt.locking`` over a single byte at offset 0 --
    that one byte is just a rendezvous every PregDos process agrees on, the file's contents
    are irrelevant to it.  Both are advisory and only coordinate PregDos with itself, which is
    all the local scheduler and the metrics pre-pass need.  The handle must be writable on
    Windows (``msvcrt`` locks require write access), so lock files are opened accordingly.
    """
    if sys.platform != "win32":
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
        return

    # Windows: msvcrt locks a byte range starting at the current file offset.
    handle.seek(0)
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def running_as_root() -> bool:
    """True only where the process can drop privileges with ``runuser`` -- i.e. root on POSIX.

    Windows has neither ``geteuid`` nor the ``runuser``/uid model, so this is always False
    there and the privilege-drop step is simply skipped.
    """
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def worker_alive(ident: str, run_dir: Optional[Path] = None) -> bool:
    """Whether the detached local worker recorded as ``ident`` is still executing.

    On POSIX ``ident`` is a process-group id and liveness is read from ``/proc`` (zombies do
    not count; see :func:`_posix_group_alive`).  On Windows it is the worker process's pid and
    we ask the OS directly whether that pid is still running.
    """
    try:
        pid = int(ident)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
    return _posix_group_alive(pid, run_dir)


def terminate_worker(ident: str) -> None:
    """Best-effort stop of the detached local worker and every process it spawned.

    POSIX signals the whole process group; Windows kills the process tree rooted at the pid
    with ``taskkill /T``.  Never raises -- a worker that already exited is not an error.
    """
    try:
        pid = int(ident)
    except (TypeError, ValueError):
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    import signal

    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:  # ProcessLookupError, PermissionError, ...
        pass


def detach_kwargs() -> dict:
    """:class:`subprocess.Popen` kwargs that detach a run from the webserver's lifetime.

    The run must outlive a Ctrl-C or a reload of the Flask dev server.  On POSIX that means a
    new session (its own process group); on Windows, a new process group via a creation flag.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _posix_group_alive(pgid: int, run_dir: Optional[Path]) -> bool:
    """Whether any *live* (non-zombie) process is left in ``pgid``'s process group.

    ``os.killpg(pgid, 0)`` alone is not enough: the detached wrapper shell lingers as a zombie
    until the web process reaps it, and signal 0 succeeds on a zombie -- so the scheduler would
    conclude a cancelled run was still going and refuse to launch anything, freezing the queue.
    So look at the real state of every process in the group and ignore the zombies.
    """
    try:
        os.killpg(pgid, 0)
    except OSError:  # ProcessLookupError and PermissionError
        return False

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # "<pid> (<comm>) <state> <ppid> <pgrp> ...".  `comm` can itself contain spaces
            # and brackets, so split after the last ')' rather than on whitespace.
            fields = (entry / "stat").read_text().rpartition(")")[2].split()
        except OSError:  # the process exited while we were looking
            continue
        if len(fields) < 3 or fields[2] != str(pgid) or fields[0] == "Z":
            continue
        if run_dir is None:
            return True
        # Confirm it is really our worker.  Pids are recycled, and a run directory kept for the
        # retention period can easily outlive the pid recorded in its metadata; without this an
        # unrelated process inheriting that pid would block the queue for good.
        try:
            if (entry / "cwd").resolve() == run_dir.resolve():
                return True
        except OSError:  # gone, or not ours to inspect
            continue
    return False


def _windows_pid_alive(pid: int) -> bool:
    """Whether ``pid`` names a still-running process on Windows.

    Uses ``OpenProcess`` + ``GetExitCodeProcess`` via ctypes -- cheap enough to call on every
    page render, and it never touches the process the way ``os.kill`` would.  Unlike the POSIX
    path this cannot confirm the pid is *our* worker rather than a recycled reuse; on a single
    local workstation that risk is slight and the whole check is already best-effort.
    """
    if sys.platform != "win32":  # unreachable; keeps the type checker off the win32-only ctypes API
        return False
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
