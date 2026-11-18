"""Files dicomexport pasted into a TOPAS input with ``-S`` (``--include-inline``).

dicomexport >= 2.0 pastes each such file between two marker lines::

    # BEGIN INCLUDE_INLINE: ../table.txt sha256=7a4ce097...
    <the file's bytes>
    <one LF>
    # END INCLUDE_INLINE: ../table.txt

``<path>`` is the path PregDos passed (relative to the run directory), or the absolute path a
bare name resolved to.  The SHA-256 covers the file's bytes: the block without the extra LF.

PregDos passes the CT-number-to-material table as the **first** ``-S``, so
:func:`material_table` is the first block.  Three places read it: the run's provenance
(:mod:`pregdos.executor`), the mask pre-pass that copies it, and the structure mass
(:mod:`pregdos.structure_metrics`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BEGIN_RE = re.compile(
    r"^# BEGIN INCLUDE_INLINE: (?P<path>.+) sha256=(?P<sha256>[0-9a-f]{64})[ \t]*\n",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class InlineInclude:
    """One pasted file."""

    path: str
    """The path as dicomexport recorded it."""
    sha256: str
    """SHA-256 of the file's bytes, as dicomexport computed it when pasting."""
    content: str
    """The file's text, without the LF dicomexport adds before the END line."""
    block: str
    """The whole block, BEGIN and END lines included, to copy into another TOPAS file."""


def inline_includes(text: str) -> list[InlineInclude]:
    """Every pasted file in ``text``, in the order dicomexport wrote them."""
    found = []
    pos = 0
    while begin := _BEGIN_RE.search(text, pos):
        path = begin.group("path")
        end_line = f"# END INCLUDE_INLINE: {path}"
        # The LF before the END line is dicomexport's own, not the file's.  Searching from
        # begin.end() - 1 finds the END line of an empty file, too.
        end = text.find(f"\n{end_line}", begin.end() - 1)
        if end < 0:
            break                   # truncated: an unterminated block is not a file
        after = end + 1 + len(end_line)
        if after < len(text) and text[after] == "\n":
            after += 1
        found.append(InlineInclude(
            path=path,
            sha256=begin.group("sha256"),
            content=text[begin.end():end],
            block=text[begin.start():after],
        ))
        pos = after
    return found


def without_inline_includes(text: str) -> str:
    """``text`` with every pasted block removed.

    For reading parameters dicomexport itself wrote: a pasted file may set parameters too, and
    must not be mistaken for dicomexport's.
    """
    for include in inline_includes(text):
        text = text.replace(include.block, "", 1)
    return text


def material_table(text: str) -> InlineInclude | None:
    """The CT-number-to-material table: the first pasted file, or None."""
    includes = inline_includes(text)
    return includes[0] if includes else None
