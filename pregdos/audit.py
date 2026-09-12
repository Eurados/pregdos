"""One line per auditable action: who did what, to which study, from where.  Issue #103.

Why this is not optional scope
------------------------------
PregDos has no per-user segregation: everyone who signs in sees every study and every
patient, and ``[auth] allow_users`` is empty by default, so the set of people who may sign in
is whatever the backend accepts.  That makes this log the *entire* record of who saw what.  A
site holding non-anonymized DICOM needs it to answer the only question that will ever be
asked after an incident.

Where it goes
-------------
stdlib :mod:`logging` at INFO, to stderr, which under systemd is journald -- already
timestamped, already rotated, already ``journalctl -u pregdos`` for the site admin.  There is
no file for PregDos to own, no rotation to get wrong, and a site that wants a file adds one
``StandardOutput=`` line to the unit.

Two things a site must be told, because neither is obvious
----------------------------------------------------------
1. **This log is PHI.**  Study names come from the uploaded folder or ZIP, so with
   non-anonymized data they are patient names or MRNs.  There is no way to record who
   downloaded patient X's data without naming X.  The journal therefore needs the same
   protection and retention policy as the studies root.
2. **The journal may be volatile.**  ``Storage=auto`` keeps nothing across a reboot unless
   ``/var/log/journal`` exists.  ``test -d /var/log/journal`` settles it.
"""

from __future__ import annotations

import logging
import shlex

from flask import g, has_request_context, request

log = logging.getLogger("pregdos.audit")


def current_username() -> str:
    """The signed-in user, or ``"-"``.

    ``"-"`` rather than omitting the field, so that turning [auth] on does not change the
    shape of the log -- anything parsing it keeps working, and the client address is recorded
    either way.
    """
    if not has_request_context():
        return "-"
    identity = getattr(g, "current_user", None)
    return identity.username if identity is not None else "-"


def _client_address() -> str:
    """The peer address.

    Deliberately ``request.remote_addr`` and not ``X-Forwarded-For``.  An unguarded XFF header
    is attacker-controlled and would forge this field outright; a site that puts a proxy in
    front must configure ``werkzeug.middleware.proxy_fix.ProxyFix`` for exactly the number of
    trusted hops, and only then is the header worth reading.
    """
    if not has_request_context():
        return "-"
    return request.remote_addr or "-"


def _one_line(text: str) -> str:
    """Escape anything that could forge a second audit line, or lie about this one.

    ``shlex.quote`` is about shell metacharacters and passes control characters straight
    through, which is not what this log needs.  Values here reach the journal from requests --
    a submitted username is recorded even when the sign-in is *denied*, which is exactly when
    an attacker controls it -- so without this, one request can append lines that look like
    genuine audit records, in the log that is this system's whole accountability story.

    Three classes matter, and ``str.isprintable`` covers all of them:

    * CR and LF end the record and start a forged one;
    * ESC and friends are interpreted by the terminal of whoever runs ``journalctl``;
    * bidirectional overrides (U+202E and relatives) reorder what a reader sees without
      changing the bytes, which is the classic way to make one name look like another.

    Printable non-ASCII is deliberately kept: study names carry patient names, and mangling
    ``ø`` into an escape would make the log worse at the job it exists for.
    """
    return "".join(
        _READABLE.get(character) or (character if character.isprintable() else _escape(character))
        for character in text
    )


def _escape(character: str) -> str:
    """``\\xNN`` / ``\\uNNNN`` / ``\\UNNNNNNNN``, matching the codepoint's width.

    The width matters: rendering U+202E as ``\\x202e`` reads as ``\\x20`` followed by "2e",
    which is a different and entirely plausible string -- so the escape meant to stop a
    spoofed log line would itself be ambiguous.
    """
    code = ord(character)
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


# Escaped readably rather than as \x0a, because these are the ones a person actually meets.
_READABLE = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def event(action: str, **fields) -> None:
    """Record one action.  ``fields`` are appended as ``key=value``, quoted where needed.

    ``user`` and ``ip`` are filled in from the request unless the caller overrides them --
    the login view does, because it has to name an account that did *not* end up signed in.

    Every value is escaped to a single line here, at the sink, rather than at each call site:
    this module's promise is one line per event, and a promise kept only by well-behaved
    callers is not kept.
    """
    fields.setdefault("user", current_username())
    fields.setdefault("ip", _client_address())
    parts = []
    for key, value in fields.items():
        text = _one_line(str(value))
        if text == "":
            text = "-"
        # shlex.quote only adds quoting when the value needs it, so ordinary lines stay
        # readable and a study name with a space in it still parses as one field.
        parts.append(f"{key}={shlex.quote(text)}")
    log.info("audit action=%s %s", _one_line(action), " ".join(parts))


__all__ = ["current_username", "event"]
