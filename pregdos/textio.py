"""Reading text that TOPAS wrote, or that carries names copied out of DICOM.

DICOM string values are not UTF-8.  A plan may declare ``ISO_IR 100`` (latin-1), and clinics
do use it: a real RayStation export in this project carries the structure
``Tec_PTVA1_ü_PRV_Myelon``, whose ``ü`` is the single byte ``0xFC``.  TOPAS echoes structure
names into its log verbatim -- ``Found a structure named: ...`` -- so that byte lands in a
file PregDos then reads on every page render.

:meth:`pathlib.Path.read_text` with no ``encoding`` decodes using the *locale's* encoding --
UTF-8 on this machine and on any modern Linux -- and raised ``UnicodeDecodeError`` on that
byte.  That took out the whole UI: both ``/studies`` and the run page call
:func:`executor.run_progress`, so one umlaut in one structure name made every page 500.

Leaving the encoding implicit is the other half of the problem.  On a latin-1 locale the same
read would not raise at all; it would silently return a differently mangled string, and the
failure would move from a 500 to a wrong value on the page.  Naming the encoding here makes
the behaviour the same everywhere.

Everything PregDos looks for in these files is ASCII -- run numbers, spot weights, header
comments, scorer values.  A byte that will not decode is therefore never anything we need,
and must not be allowed to take a page down with it.
"""

from __future__ import annotations

from pathlib import Path


def read_text_lenient(path: str | Path) -> str:
    """:meth:`Path.read_text` that survives an undecodable byte instead of raising.

    Offending bytes become U+FFFD.  Use this for anything TOPAS produced or that quotes a
    name taken from DICOM; keep strict decoding for files PregDos wrote itself.
    """
    return Path(path).read_text(encoding="utf-8", errors="replace")
