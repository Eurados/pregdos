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


def event(action: str, **fields) -> None:
    """Record one action.  ``fields`` are appended as ``key=value``, quoted where needed.

    ``user`` and ``ip`` are filled in from the request unless the caller overrides them --
    the login view does, because it has to name an account that did *not* end up signed in.
    """
    fields.setdefault("user", current_username())
    fields.setdefault("ip", _client_address())
    parts = []
    for key, value in fields.items():
        text = str(value)
        if text == "":
            text = "-"
        # shlex.quote only adds quoting when the value needs it, so ordinary lines stay
        # readable and a study name with a space in it still parses as one field.
        parts.append(f"{key}={shlex.quote(text)}")
    log.info("audit action=%s %s", action, " ".join(parts))


__all__ = ["current_username", "event"]
