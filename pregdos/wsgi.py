"""Legacy importable WSGI application.

Deploy with ``pregdos-web``: it runs Gunicorn after validating the actual listener,
certificates, and authentication backend. This import path provisions the session key and
logging for existing integrations, but cannot validate an external server's bind options.
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
