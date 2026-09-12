"""The WSGI entry point.  Use this, not ``pregdos.webserver:app``, under a WSGI server.

    gunicorn -k gthread -w 2 --threads 8 pregdos.wsgi:app

The difference is one line, and it is the line that makes sessions work.  ``pregdos-web``
provisions the persistent session-signing key in :func:`pregdos.webserver.main`; a WSGI server
imports the module and never calls ``main``, so on a *fresh* deployment -- no
``$PREGDOS_SECRET_KEY``, no key file yet -- every worker would fall back to its own random key.
Cookies signed by one worker are then rejected by the next, and a user sees a login that works
or does not depending on which worker happens to answer.  Nothing logs an error, because from
each worker's point of view it did something reasonable.

Importing this module provisions the key first, then exposes the app.  Every worker imports
it, and :func:`pregdos.webserver.ensure_secret_key` claims the file with ``O_EXCL`` -- so the
first worker to get there writes it and the rest read what it wrote, which is exactly the race
that function was already written to survive.

``pregdos.webserver:app`` still works and is not deprecated; it simply cannot provision, and
says so loudly in the log if it is used where it matters.
"""

from __future__ import annotations

from .webserver import app, ensure_secret_key

# At import, before the first request and before any worker forks.
ensure_secret_key()

__all__ = ["app"]
