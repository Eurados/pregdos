"""Tests for the SLURM / local execution backends (issue #43)."""

import json
import time
from pathlib import Path

import pytest

from pregdos import executor


@pytest.fixture
def run_dir(tmp_path):
    """A run directory holding two generated TOPAS inputs."""
    (tmp_path / "topas_field01.txt").write_text("# topas")
    (tmp_path / "topas_field02.txt").write_text("# topas")
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("PREGDOS_EXECUTOR", raising=False)
    monkeypatch.delenv("TOPAS_BIN", raising=False)


# --- backend selection ---

def test_auto_prefers_slurm_when_sbatch_present(monkeypatch):
    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/sbatch")
    assert executor.select_backend() == executor.SLURM


def test_auto_falls_back_to_local_without_sbatch(monkeypatch):
    """The failure the user hit: no sbatch (and no runuser) on a plain workstation."""
    monkeypatch.setattr(executor.shutil, "which", lambda name: None)
    assert executor.select_backend() == executor.LOCAL


def test_explicit_backend_overrides_detection(monkeypatch):
    monkeypatch.setattr(executor.shutil, "which", lambda name: None)
    monkeypatch.setenv("PREGDOS_EXECUTOR", "slurm")
    assert executor.select_backend() == executor.SLURM

    monkeypatch.setattr(executor.shutil, "which", lambda name: "/usr/bin/sbatch")
    monkeypatch.setenv("PREGDOS_EXECUTOR", "local")
    assert executor.select_backend() == executor.LOCAL


# --- the wrapped command ---

def test_field_command_records_topas_exit_code(monkeypatch):
    monkeypatch.setenv("TOPAS_BIN", "/opt/topas")
    cmd = executor.field_command("topas_field01.txt")
    assert cmd == "/opt/topas topas_field01.txt > topas_field01.log 2>&1; echo $? > topas_field01.exit_code"


def test_field_command_quotes_hostile_names(monkeypatch):
    monkeypatch.setenv("TOPAS_BIN", "my topas")
    cmd = executor.field_command("a b.txt")
    assert "'my topas'" in cmd and "'a b.txt'" in cmd


# --- local backend ---

def _wait_for(path: Path, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


def test_local_backend_runs_fields_and_writes_sentinels(run_dir, monkeypatch):
    """End-to-end through the real local backend, with `true` standing in for TOPAS."""
    monkeypatch.setenv("PREGDOS_EXECUTOR", "local")
    monkeypatch.setenv("TOPAS_BIN", "true")

    info = executor.submit_run(run_dir, ["topas_field01.txt", "topas_field02.txt"])
    assert info.backend == executor.LOCAL
    assert len(info.fields) == 2

    assert _wait_for(run_dir / "topas_field02.exit_code")
    assert (run_dir / "topas_field01.exit_code").read_text().strip() == "0"
    assert executor.run_status(run_dir) == executor.COMPLETED


def test_local_backend_records_failure_and_captures_log(run_dir, monkeypatch):
    """A crashing TOPAS yields a non-zero sentinel and its output lands in the log."""
    monkeypatch.setenv("PREGDOS_EXECUTOR", "local")
    # `sh -c 'echo boom >&2; exit 3'` would need quoting; a missing binary is simpler and
    # exercises the same path: non-zero exit, diagnostics on stderr -> the log file.
    monkeypatch.setenv("TOPAS_BIN", "false")

    executor.submit_run(run_dir, ["topas_field01.txt"])
    assert _wait_for(run_dir / "topas_field01.exit_code")

    assert (run_dir / "topas_field01.exit_code").read_text().strip() == "1"
    assert (run_dir / "topas_field01.log").exists()
    assert executor.field_status(run_dir, "topas_field01.txt") == executor.FAILED
    assert executor.run_status(run_dir) == executor.FAILED


def test_local_backend_continues_after_a_failing_field(run_dir, monkeypatch):
    """Fields are chained with `;`, so one crash must not skip the rest."""
    monkeypatch.setenv("PREGDOS_EXECUTOR", "local")
    monkeypatch.setenv("TOPAS_BIN", "false")
    executor.submit_run(run_dir, ["topas_field01.txt", "topas_field02.txt"])

    assert _wait_for(run_dir / "topas_field02.exit_code")
    assert executor.field_status(run_dir, "topas_field02.txt") == executor.FAILED


def test_local_backend_queues_second_run_while_one_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("PREGDOS_EXECUTOR", "local")
    monkeypatch.setenv("TOPAS_BIN", "sleep")
    study = tmp_path / "alpha"
    run1 = study / "run_20260712_100000"
    run2 = study / "run_20260712_100001"
    run1.mkdir(parents=True)
    run2.mkdir()

    info1 = executor.submit_run(run1, ["30"])
    info2 = executor.submit_run(run2, ["30"])

    assert info1.fields[0].ident
    assert info2.fields[0].ident == ""
    assert executor.run_status(run1) == executor.RUNNING
    assert executor.run_status(run2) == executor.QUEUED

    executor.cancel_run(run1)
    # Canceling the active run advances the queue, so clean up the second worker too.
    deadline = time.time() + 2
    while time.time() < deadline and not executor.read_run_metadata(run2).fields[0].ident:
        time.sleep(0.02)
    pid2 = executor.read_run_metadata(run2).fields[0].ident
    executor.cancel_run(run2)
    if pid2:
        deadline = time.time() + 5
        while time.time() < deadline and _proc_state(int(pid2)) not in ("", "Z"):
            time.sleep(0.02)


def test_scheduler_waits_for_a_canceled_worker_to_actually_die(tmp_path, monkeypatch, mocker):
    """SIGTERM is asynchronous: a multithreaded TOPAS flushing its scorers takes time to exit.

    The cancel marker makes ``run_status`` report CANCELED immediately, so without an explicit
    liveness check the scheduler would see "nothing is RUNNING" and launch the next run *while
    the old one is still on the CPU* -- exactly what the one-run-at-a-time queue exists to stop.
    """
    monkeypatch.setenv("PREGDOS_EXECUTOR", "local")
    study = tmp_path / "alpha"
    dying = study / "run_20260712_100000"
    queued = study / "run_20260712_100001"
    dying.mkdir(parents=True)
    queued.mkdir()

    (dying / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "local", "submitted": "a",
        "fields": [{"topas_file": "topas_field01.txt", "ident": "4242"}],
    }))
    (dying / executor.CANCEL_MARKER).write_text("now\n")
    (queued / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "local", "submitted": "b",
        "fields": [{"topas_file": "topas_field01.txt", "ident": ""}],
    }))

    launch = mocker.patch("pregdos.executor._launch_local_worker")

    # The canceled worker has not exited yet: probing its process group succeeds.
    mocker.patch("pregdos.executor.os.killpg")
    assert executor.start_next_local_run(tmp_path) is None
    launch.assert_not_called()

    # It is gone now, so the queue may advance.
    mocker.patch("pregdos.executor.os.killpg", side_effect=ProcessLookupError)
    assert executor.start_next_local_run(tmp_path) == queued
    launch.assert_called_once()


def test_cancel_marks_local_run_as_canceled(run_dir):
    (run_dir / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "local", "submitted": "now",
        "fields": [{"topas_file": "topas_field01.txt", "ident": ""}],
    }))

    executor.cancel_run(run_dir)

    assert executor.run_status(run_dir) == executor.CANCELED
    assert executor.field_status(run_dir, "topas_field01.txt") == executor.CANCELED


def test_move_local_run_up_swaps_fifo_order(tmp_path):
    study = tmp_path / "alpha"
    run1 = study / "run_20260712_100000"
    run2 = study / "run_20260712_100001"
    run1.mkdir(parents=True)
    run2.mkdir()
    (run1 / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "local", "submitted": "2026-07-12T10:00:00",
        "fields": [{"topas_file": "topas_field01.txt", "ident": ""}],
    }))
    (run2 / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "local", "submitted": "2026-07-12T10:01:00",
        "fields": [{"topas_file": "topas_field01.txt", "ident": ""}],
    }))

    assert executor.move_local_run_up(tmp_path, run2)

    assert executor.read_run_metadata(run1).submitted == "2026-07-12T10:01:00"
    assert executor.read_run_metadata(run2).submitted == "2026-07-12T10:00:00"


# --- slurm backend ---

def test_slurm_backend_chdirs_into_run_dir(run_dir, monkeypatch, mocker):
    """TOPAS must run from the run dir: the .txt's paths are relative to it (#41)."""
    monkeypatch.setenv("PREGDOS_EXECUTOR", "slurm")
    fake = mocker.Mock(returncode=0, stdout="Submitted batch job 77\n", stderr="")
    run = mocker.patch("pregdos.executor.subprocess.run", return_value=fake)
    mocker.patch("pregdos.executor.os.geteuid", return_value=1000)  # not root -> no runuser

    info = executor.submit_run(run_dir, ["topas_field01.txt"])
    assert info.backend == executor.SLURM
    assert info.fields[0].ident == "77"

    argv = run.call_args[0][0]
    assert argv[0] == "sbatch"          # no runuser prefix when unprivileged
    assert f"--chdir={run_dir}" in argv
    assert argv[-1] == executor.field_command("topas_field01.txt")


def test_slurm_backend_uses_runuser_when_root(run_dir, monkeypatch, mocker):
    monkeypatch.setenv("PREGDOS_EXECUTOR", "slurm")
    mocker.patch("pregdos.executor.subprocess.run",
                 return_value=mocker.Mock(returncode=0, stdout="Submitted batch job 1\n", stderr=""))
    mocker.patch("pregdos.executor.os.geteuid", return_value=0)
    mocker.patch("pregdos.executor.shutil.which", return_value="/usr/sbin/runuser")

    executor.submit_run(run_dir, ["topas_field01.txt"])
    argv = executor.subprocess.run.call_args[0][0]
    assert argv[:4] == ["/usr/sbin/runuser", "-u", "slurm", "--"]


def test_slurm_submission_failure_is_collected_not_raised(run_dir, monkeypatch, mocker):
    monkeypatch.setenv("PREGDOS_EXECUTOR", "slurm")
    mocker.patch("pregdos.executor.subprocess.run",
                 return_value=mocker.Mock(returncode=1, stdout="", stderr="slurmctld down"))
    mocker.patch("pregdos.executor.os.geteuid", return_value=1000)

    info = executor.submit_run(run_dir, ["topas_field01.txt"])
    assert info.fields == []
    assert "slurmctld down" in info.errors[0]
    # nothing was launched, so no metadata is written
    assert not (run_dir / executor.RUN_METADATA).exists()


# --- status is read from disk, not from process state ---

def test_status_survives_a_restart(run_dir):
    """Nothing is remembered in-process: run.json plus sentinels are the whole story."""
    (run_dir / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "slurm", "submitted": "2026-07-09T12:00:00",
        "fields": [{"topas_file": "topas_field01.txt", "ident": "5"},
                   {"topas_file": "topas_field02.txt", "ident": "6"}],
    }))

    assert executor.run_status(run_dir) == executor.RUNNING

    (run_dir / "topas_field01.exit_code").write_text("0\n")
    assert executor.run_status(run_dir) == executor.RUNNING   # field02 still going

    (run_dir / "topas_field02.exit_code").write_text("0\n")
    assert executor.run_status(run_dir) == executor.COMPLETED

    (run_dir / "topas_field02.exit_code").write_text("139\n")
    assert executor.run_status(run_dir) == executor.FAILED


def test_unparseable_sentinel_counts_as_failure(run_dir):
    (run_dir / "topas_field01.exit_code").write_text("")
    assert executor.field_status(run_dir, "topas_field01.txt") == executor.FAILED


def test_status_of_unsubmitted_run_is_queued(run_dir):
    assert executor.run_status(run_dir) == executor.QUEUED
    assert executor.read_run_metadata(run_dir) is None


def test_corrupt_metadata_does_not_raise(run_dir):
    (run_dir / executor.RUN_METADATA).write_text("{ not json")
    assert executor.read_run_metadata(run_dir) is None


@pytest.mark.parametrize("payload", [
    '[1, 2, 3]',                                        # valid JSON, wrong shape
    '"a string"',
    'null',
    '{"fields": "not a list"}',
    '{"fields": [1, 2]}',                               # entries not objects
    '{"fields": [{"ident": "5"}]}',                     # entry missing topas_file
    '{"fields": [{"topas_file": null, "ident": "5"}]}',
    '{"fields": [{"topas_file": "", "ident": "5"}]}',
])
def test_malformed_metadata_never_raises(run_dir, payload):
    """run.json may be truncated by a crash or half-written while we read it.  Every caller
    is a page render, so a bad shape must not 500 the jobs page."""
    (run_dir / executor.RUN_METADATA).write_text(payload)
    info = executor.read_run_metadata(run_dir)          # must not raise
    assert info is None or info.fields == []
    assert executor.run_status(run_dir) == executor.QUEUED
    executor.cancel_run(run_dir)                        # must not raise either


def test_malformed_entries_are_skipped_not_fatal(run_dir):
    """A partially valid file still yields the fields we can understand."""
    (run_dir / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "local", "submitted": "2026-07-09T12:00:00",
        "fields": [
            {"ident": "1"},                             # no topas_file -> skipped
            {"topas_file": "topas_field01.txt", "ident": "2"},
            "garbage",                                  # not an object -> skipped
        ],
    }))
    info = executor.read_run_metadata(run_dir)
    assert [f.topas_file for f in info.fields] == ["topas_field01.txt"]


def test_unknown_backend_falls_back_to_local(run_dir):
    (run_dir / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "kubernetes", "submitted": 12345,
        "fields": [{"topas_file": "f.txt"}],
    }))
    info = executor.read_run_metadata(run_dir)
    assert info.backend == executor.LOCAL
    assert info.submitted == ""
    assert info.fields[0].ident == ""


# --- cancellation ---

def test_cancel_local_run_kills_the_process_group(run_dir, monkeypatch, mocker):
    monkeypatch.setenv("PREGDOS_EXECUTOR", "local")
    monkeypatch.setenv("TOPAS_BIN", "sleep 30 #")  # long-running stand-in

    (run_dir / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "local", "submitted": "now",
        "fields": [{"topas_file": "topas_field01.txt", "ident": "4242"}],
    }))
    killpg = mocker.patch("pregdos.executor.os.killpg")
    executor.cancel_run(run_dir)
    # `killpg` is called more than once: cancel_run() kicks the scheduler, which probes the same
    # process group with signal 0 to find out whether the worker has actually exited yet -- it
    # must not start the next run while a SIGTERMed TOPAS is still shutting down.  What this test
    # is about is only that the TERM was delivered to the group.
    killpg.assert_any_call(4242, executor.signal.SIGTERM)


def test_cancel_slurm_run_calls_scancel(run_dir, mocker):
    (run_dir / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "slurm", "submitted": "now",
        "fields": [{"topas_file": "f1.txt", "ident": "9"}, {"topas_file": "f2.txt", "ident": "9"}],
    }))
    mocker.patch("pregdos.executor.shutil.which", return_value="/usr/bin/scancel")
    run = mocker.patch("pregdos.executor.subprocess.run")
    executor.cancel_run(run_dir)
    run.assert_called_once_with(["scancel", "9"], capture_output=True, text=True)


def test_cancel_is_safe_when_process_is_gone(run_dir, mocker):
    (run_dir / executor.RUN_METADATA).write_text(json.dumps({
        "backend": "local", "submitted": "now",
        "fields": [{"topas_file": "f1.txt", "ident": "999999"}],
    }))
    mocker.patch("pregdos.executor.os.killpg", side_effect=ProcessLookupError)
    executor.cancel_run(run_dir)  # must not raise


def test_cancel_without_metadata_is_a_noop(run_dir):
    executor.cancel_run(run_dir)  # must not raise


def _proc_state(pid: int) -> str:
    """Linux process state letter, or '' when the pid is gone.

    A killed child of this process becomes a zombie ('Z') until it is reaped, and a zombie
    still answers ``killpg(pid, 0)`` -- so liveness must be judged on the state, not on
    whether the pid can be signalled.
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return ""
    # The comm field may contain spaces/parens; state is the first field after the last ')'
    return stat[stat.rindex(")") + 2]


def test_real_local_run_can_be_cancelled(run_dir, monkeypatch):
    """Kill the detached shell's process group and the running TOPAS goes with it."""
    monkeypatch.setenv("PREGDOS_EXECUTOR", "local")
    monkeypatch.setenv("TOPAS_BIN", "sleep")
    # field_command() builds `sleep 30 > 30.log 2>&1; ...`, i.e. a long-running stand-in.
    info = executor.submit_run(run_dir, ["30"])
    pid = int(info.fields[0].ident)

    time.sleep(0.1)
    assert _proc_state(pid) in ("S", "R"), "the run should still be going"
    assert not (run_dir / "30.exit_code").exists()

    executor.cancel_run(run_dir)

    deadline = time.time() + 5
    while time.time() < deadline and _proc_state(pid) not in ("", "Z"):
        time.sleep(0.02)
    # gone, or a zombie awaiting reaping -- either way it is no longer sleeping
    assert _proc_state(pid) in ("", "Z")
    assert not (run_dir / "30.exit_code").exists(), "cancelled run must not report success"
