"""Tests for history-weighted field progress (executor.field_progress)."""

import json
import pytest

from pregdos import executor


def _field(run_dir, weights, runs_seen=(), name="topas_field01.txt", exit_code=None):
    """Write a TOPAS input with the given spot weights, and a log that has reached runs_seen."""
    stem = name[:-4]
    values = " ".join(str(w) for w in weights)
    (run_dir / name).write_text(
        "# header\n"
        f"i:Tf/NumberOfSequentialTimes         = {len(weights)}\n"
        f"uv:Tf/spotWeight/Values                   = {len(weights)} {values}\n"
    )
    if runs_seen:
        (run_dir / f"{stem}.log").write_text(
            "".join(f"G4WT{k % 4} > Begin processing for Run: {k}, History: 0\n" for k in runs_seen)
        )
    if exit_code is not None:
        (run_dir / f"{stem}.exit_code").write_text(f"{exit_code}\n")


def test_progress_is_weighted_by_histories_not_run_count(tmp_path):
    """The point of the feature: skewed weights mean run-count is a bad proxy.  Early spots
    carry most of the work, so reaching run 2 of 5 can be most of the histories."""
    # weights: run 0 and 1 are huge, runs 2-4 tiny
    _field(tmp_path, weights=[1000, 1000, 10, 10, 10], runs_seen=[0, 1])
    p = executor.field_progress(tmp_path, "topas_field01.txt")

    assert p.total_runs == 5
    assert p.histories_total == 2030
    assert p.histories_done == 2000            # runs 0+1
    assert p.runs_started == 2
    # 2/5 = 40% by run count, but 2000/2030 ≈ 99% by histories
    assert round(100 * p.fraction) == 99


def test_progress_handles_out_of_order_run_dispatch(tmp_path):
    """Threads dispatch runs concurrently, so the log may show high indices before low ones.
    Summing the weights of every started run is correct regardless of order."""
    _field(tmp_path, weights=[5, 5, 5, 5], runs_seen=[3, 0])   # 1 and 2 not started
    p = executor.field_progress(tmp_path, "topas_field01.txt")
    assert p.runs_started == 2
    assert p.histories_done == 10               # runs 0 and 3
    assert round(100 * p.fraction) == 50


def test_completed_field_is_full(tmp_path):
    _field(tmp_path, weights=[10, 20, 30], runs_seen=[0], exit_code=0)
    p = executor.field_progress(tmp_path, "topas_field01.txt")
    assert p.status == executor.COMPLETED
    assert p.fraction == 1.0                    # 100% regardless of what the log shows
    assert p.histories_done == 60


def test_failed_field_shows_how_far_it_got(tmp_path):
    _field(tmp_path, weights=[10, 20, 30], runs_seen=[0, 1], exit_code=1)
    p = executor.field_progress(tmp_path, "topas_field01.txt")
    assert p.status == executor.FAILED
    assert p.fraction < 1.0                     # not pretended complete
    assert p.histories_done == 30               # runs 0+1


def test_not_started_field_is_zero(tmp_path):
    _field(tmp_path, weights=[10, 20], runs_seen=[])
    p = executor.field_progress(tmp_path, "topas_field01.txt")
    assert p.runs_started == 0
    assert p.fraction == 0.0


def test_stray_run_index_cannot_overshoot(tmp_path):
    """A run index past the weight vector is ignored rather than indexing out of range."""
    _field(tmp_path, weights=[10, 20], runs_seen=[0, 1, 99])
    p = executor.field_progress(tmp_path, "topas_field01.txt")
    assert p.histories_done == 30
    assert p.fraction == 1.0


def test_missing_spot_weights_degrades_gracefully(tmp_path):
    (tmp_path / "topas_field01.txt").write_text("# no time features here\n")
    (tmp_path / "topas_field01.log").write_text("Begin processing for Run: 0, History: 0\n")
    p = executor.field_progress(tmp_path, "topas_field01.txt")
    assert p.total_runs == 0
    assert p.fraction == 0.0                    # no total -> no false progress


def test_run_progress_covers_all_fields_in_order(tmp_path):
    _field(tmp_path, weights=[10, 10], runs_seen=[0, 1], name="topas_field01.txt", exit_code=0)
    _field(tmp_path, weights=[10, 10], runs_seen=[0], name="topas_field02.txt")
    (tmp_path / "run.json").write_text(json.dumps({
        "backend": "local", "submitted": "now",
        "fields": [{"topas_file": "topas_field01.txt", "ident": "1"},
                   {"topas_file": "topas_field02.txt", "ident": "1"}],
    }))
    prog = executor.run_progress(tmp_path)
    assert [p.topas_file for p in prog] == ["topas_field01.txt", "topas_field02.txt"]
    assert prog[0].fraction == 1.0
    assert 0 < prog[1].fraction < 1.0


def test_run_progress_empty_when_never_submitted(tmp_path):
    assert executor.run_progress(tmp_path) == []


# --- ETR ---

import datetime  # noqa: E402


def _progress(done, total):
    return [executor.FieldProgress("f.txt", executor.RUNNING, done, total, 0, 0)]


def test_etr_extrapolates_linearly():
    """25% done after 10 min → ~30 min remaining (elapsed × (1−f)/f)."""
    now = datetime.datetime(2026, 7, 10, 12, 10, 0)
    submitted = "2026-07-10T12:00:00"                  # 600 s ago, 25% done
    secs = executor.estimate_remaining_seconds(_progress(25, 100), submitted, now=now)
    assert secs == pytest.approx(600 * 0.75 / 0.25)    # 1800 s = 30 min


def test_etr_is_none_before_any_progress():
    now = datetime.datetime(2026, 7, 10, 12, 10, 0)
    assert executor.estimate_remaining_seconds(_progress(0, 100), "2026-07-10T12:00:00", now=now) is None


def test_etr_is_none_when_complete():
    now = datetime.datetime(2026, 7, 10, 12, 10, 0)
    assert executor.estimate_remaining_seconds(_progress(100, 100), "2026-07-10T12:00:00", now=now) is None


@pytest.mark.parametrize("submitted", [None, "", "not-a-date"])
def test_etr_tolerates_bad_submitted(submitted):
    now = datetime.datetime(2026, 7, 10, 12, 10, 0)
    assert executor.estimate_remaining_seconds(_progress(25, 100), submitted, now=now) is None


def test_etr_sums_histories_across_fields():
    """Fields run sequentially, so the later ones (0 done) pull the whole-run ETA out."""
    now = datetime.datetime(2026, 7, 10, 12, 10, 0)
    prog = [
        executor.FieldProgress("f1.txt", executor.COMPLETED, 100, 100, 0, 0),
        executor.FieldProgress("f2.txt", executor.RUNNING, 50, 100, 0, 0),
        executor.FieldProgress("f3.txt", executor.RUNNING, 0, 100, 0, 0),
    ]
    # 150/300 = 50% after 600 s → ~600 s remaining
    secs = executor.estimate_remaining_seconds(prog, "2026-07-10T12:00:00", now=now)
    assert secs == pytest.approx(600.0)


def test_format_duration():
    from pregdos.webserver import _format_duration
    assert _format_duration(9000) == "~2h 30m"
    assert _format_duration(150) == "~2m"
    assert _format_duration(20) == "~20s"
    assert _format_duration(-5) == "~0s"
