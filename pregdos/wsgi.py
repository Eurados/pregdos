"""The WSGI entry point.  Use this, not ``pregdos.webserver:app``, under a WSGI server.

    gunicorn -k gthread -w 2 --threads 8 pregdos.wsgi:app

The difference is two lines, and they are the ones that make sessions and the audit log work.
``pregdos-web`` does both in :func:`pregdos.webserver.main`, which a WSGI server never calls:

* **The session signing key.**  On a fresh deployment every worker would otherwise fall back
  to its own random key, so a cookie signed by one is rejected by the next and a user sees a
  login that works or does not depending on which worker answers -- with nothing in the log,
  because each worker did something reasonable.

* **Logging.**  Nothing else configures the root logger, so every audit event is dropped, and
  that log is the whole record of who saw which patient's study.

Both run at import, before the first request and before any worker forks.

``pregdos.webserver:app`` still works and is not deprecated; it simply cannot provision, and
says so in the log if it is used where it matters.
"""

from __future__ import annotations

import logging

from . import auth, config
from .webserver import app, configure_logging, ensure_secret_key

configure_logging()
_log = logging.getLogger(__name__)
_log.info("%s", ensure_secret_key())

# The same startup facts `pregdos-web` records.  Who could sign in is the question asked after
# an incident, and a deployment that never wrote it down cannot answer it.  A broken config is
# left to surface as it already does rather than becoming a new import-time crash here.
try:
    _log.info("%s", auth.describe_policy())
except config.ConfigError as exc:
    _log.error("configuration is not loadable, so no auth policy can be reported: %s", exc)

__all__ = ["app"]
