"""Non-UTF-8 bytes in TOPAS output must not take a page down.

The failing case: a RayStation study with the structure `Tec_PTVA1_ü_PRV_Myelon`.  DICOM
declares latin-1, so the `ü` is the single byte 0xFC; TOPAS echoes structure names into its
log, and `Path.read_text()` then raised `UnicodeDecodeError` inside `run_progress` -- which
both `/studies` and the run page call, so every page returned 500.
"""

import pytest

from pregdos import executor
from pregdos.textio import read_text_lenient

LATIN1_LOG = (
    b"Found a structure named: PRV_HS_CT 2->CT 1 \n"
    b"Found a structure named: Tec_PTVA1_\xfc_PRV_Myelon_CT 2->CT 1 \n"
    b"Begin processing for Run: 0, History: 0\n"
    b"Begin processing for Run: 1, History: 0\n"
)


def test_read_text_lenient_survives_a_latin1_byte(tmp_path):
    path = tmp_path / "topas_field01.log"
    path.write_bytes(LATIN1_LOG)

    # What the code used to do.  Spelled with an explicit encoding rather than relying on
    # `read_text()`'s locale default, so the test asserts the same thing on any machine.
    with pytest.raises(UnicodeDecodeError):
        path.read_text(encoding="utf-8")

    text = read_text_lenient(path)
    assert "Begin processing for Run: 1" in text   # the ASCII we actually parse survives
    assert "�" in text                        # the bad byte is replaced, not fatal


def test_runs_started_reads_a_log_with_a_latin1_structure_name(tmp_path):
    (tmp_path / "topas_field01.log").write_bytes(LATIN1_LOG)

    assert executor._runs_started(tmp_path, "topas_field01.txt") == {0, 1}


def test_field_progress_does_not_raise_on_a_latin1_log(tmp_path):
    """The actual 500: run_progress -> field_progress -> _runs_started."""
    (tmp_path / "topas_field01.log").write_bytes(LATIN1_LOG)
    (tmp_path / "topas_field01.txt").write_bytes(
        b'uv:Tf/spotWeight/Values = 2 10 20\n# structure: Tec_PTVA1_\xfc_PRV_Myelon\n')

    progress = executor.field_progress(tmp_path, "topas_field01.txt")

    assert progress.total_runs == 2
    assert progress.runs_started == 2
