"""Optional authentication: who may use this PregDos, and how they prove it.  Issue #103.

PregDos shipped with no authentication at all, deliberately, and that is still the default.
This module exists because the first real deployment needs to hold non-anonymized patient
DICOM, which the open door cannot justify.

The shape
---------
A :class:`Backend` does exactly one thing: decide whether a password belongs to a username.
It never consults ``[auth] allow_users``.  Authorization is :func:`authorize`, one function
shared by every backend, precisely so that adding a backend cannot accidentally skip it --
every backend authenticates against something that knows more accounts than PregDos should
serve, so the filter can never be a backend's own business.

:func:`login` is the whole flow: authenticate, *then* authorize.  That order matters.  Doing
it the other way would let an unauthenticated visitor learn who is on the list by watching
which names are refused early.

What this deliberately does not do
----------------------------------
There is no per-user segregation.  Everyone who signs in sees every study and every patient;
the allowlist is the entire authorization model, and :mod:`pregdos.audit` is what records who
saw what.  Sites must be told this plainly rather than discover it.

There is also no rate limiter.  An in-process counter is worthless across the worker
processes of a WSGI server, and a half-working one is worse than none -- see the fixed delay
in the login view, and the backing store's own lockout policy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from . import config

log = logging.getLogger(__name__)

# scrypt at the parameters OWASP calls interactive: ~16 MB and a few tens of ms per check,
# which is invisible on a login form and expensive in bulk.  They are stored *in* each hash,
# so raising them later re-hashes on next password change rather than invalidating the file.
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16


class AuthError(Exception):
    """Authentication could not be *attempted*: no password file, no backing service, a
    misconfiguration.

    Deliberately distinct from a wrong password, which is an ordinary :class:`AuthResult`
    with ``ok`` false.  This one is the operator's problem and is logged at ERROR; the user
    is shown the same generic failure either way, so a broken backend cannot be probed from
    the login page.
    """


@dataclass(frozen=True)
class Identity:
    """Who signed in.  ``display_name`` is cosmetic -- the nav bar -- and may be empty."""

    username: str
    display_name: str = ""


@dataclass(frozen=True)
class AuthResult:
    identity: Identity | None = None
    # "" | "bad-credentials" | "not-allowlisted" | "not-authorised" | "backend-unavailable"
    reason: str = ""
    detail: str = ""        # for the audit log ONLY.  Never rendered, never flashed.

    @property
    def ok(self) -> bool:
        return self.identity is not None


class Backend:
    """One way of checking that a password belongs to a username.  Nothing more.

    Implementations MUST NOT consult ``allow_users``; see the module docstring.
    """

    name: str = ""

    # Shown on the sign-in form, to say WHICH password is being asked for.  At a site with
    # more than one credential store -- which is every site that made authentication hard
    # enough to need this module -- "which password?" is the first support question, and the
    # answer depends on the backend.  `[auth] login_hint` overrides it, because the wording
    # that actually stops the question is site-specific and may not be in English.
    login_hint: str = ""

    def preflight(self) -> str | None:
        """A human-readable reason this backend cannot work on this host, or None.

        Called once from ``main()`` before the port is bound, so that a missing password file
        is a startup error naming the file -- not a login page that rejects everybody with
        nothing in the journal to say why.
        """
        return None

    def check_password(self, username: str, password: str) -> AuthResult:
        raise NotImplementedError


class NoneBackend(Backend):
    """No authentication.  Exists so :func:`get_backend` is total and only :func:`is_enabled`
    ever branches on the string ``"none"``."""

    name = "none"

    def check_password(self, username: str, password: str) -> AuthResult:
        raise AuthError('[auth] method is "none": there is nothing to sign in to')


# ---------------------------------------------------------------------------
# method = "file": a password file PregDos manages itself
# ---------------------------------------------------------------------------

def password_file_path(cfg: config.Config | None = None) -> Path:
    """``[auth] password_file``, else ``users`` in the state directory."""
    cfg = cfg or config.load()
    return Path(cfg.auth.password_file) if cfg.auth.password_file else config.state_dir() / "users"


def hash_password(password: str) -> str:
    """A self-describing scrypt hash: ``scrypt$n$r$p$salt$key``, both halves base64.

    The parameters travel with the hash so that changing them later does not invalidate every
    existing entry -- an old hash still verifies with the numbers it was made with.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(key).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a hash from :func:`hash_password`.

    A malformed entry returns False rather than raising: one corrupt line must lock out one
    account, not take the whole login page down with a 500.
    """
    try:
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(key_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        log.error("password file: entry is malformed and will never verify")
        return False
    return hmac.compare_digest(actual, expected)


def read_password_file(path: Path) -> Dict[str, str]:
    """``{username: hash}``.  Raises :class:`AuthError` if the file is missing or too readable.

    Blank lines and ``#`` comments are skipped, so an admin can annotate the file even though
    ``pregdos-passwd`` is the supported way to edit it.
    """
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise AuthError(f"{path}: password file cannot be read ({exc.strerror}). "
                        f"Create it with `pregdos-passwd add <user>`.") from exc
    if mode & 0o077:
        raise AuthError(
            f"{path}: mode {mode & 0o777:04o} lets other accounts read the password hashes. "
            f"chmod 0600."
        )
    users: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        username, _, stored = line.partition(":")
        if username and stored:
            users[username] = stored
    return users


def write_password_file(path: Path, users: Dict[str, str]) -> None:
    """Replace the file atomically, 0600.

    tmp-file + ``os.replace`` so a crash or a full disk cannot leave a half-written file that
    locks everyone out -- readers see either the old set of accounts or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    body = "".join(f"{user}:{stored}\n" for user, stored in sorted(users.items()))
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


class FileBackend(Backend):
    """Passwords in a file PregDos owns, hashed with scrypt.  Written by ``pregdos-passwd``.

    The method that works everywhere -- the container included -- and the one the test suite
    exercises end to end, since it needs no external service.  Its cost is a password that
    exists only for PregDos, which at a site with an existing credential store is exactly the
    extra thing nobody wants.
    """

    name = "file"
    login_hint = ("This password is specific to PregDos. It is not your computer login, "
                  "and not your file-share password.")

    def __init__(self, path: Path):
        self.path = path

    def preflight(self) -> str | None:
        try:
            if not read_password_file(self.path):
                return (f"{self.path}: password file has no accounts in it, so nobody could "
                        f"sign in. Add one with `pregdos-passwd add <user>`.")
        except AuthError as exc:
            return str(exc)
        return None

    def check_password(self, username: str, password: str) -> AuthResult:
        users = read_password_file(self.path)          # AuthError -> the caller logs and 500s
        stored = users.get(username)
        if stored is None:
            # Spend the time anyway.  Returning early for an unknown user makes the response
            # measurably faster than for a known one, which enumerates the account list.
            verify_password(password, hash_password("decoy"))
            return AuthResult(reason="bad-credentials", detail="no such user")
        if not verify_password(password, stored):
            return AuthResult(reason="bad-credentials", detail="wrong password")
        return AuthResult(identity=Identity(username=username))


# ---------------------------------------------------------------------------
# method = "smb": the site's own Samba, over the SMB protocol
# ---------------------------------------------------------------------------

# What `smbclient` says, and what each means.  Taken from a real run against the DCPT server
# (issue #103) rather than from the manual, because the mapping is the whole backend.
_SMB_BAD_CREDENTIALS = (
    "NT_STATUS_LOGON_FAILURE",          # wrong password AND unknown user -- indistinguishable,
                                        # which is what keeps the form from enumerating accounts
    "NT_STATUS_WRONG_PASSWORD",
    "NT_STATUS_NO_SUCH_USER",
    "NT_STATUS_ACCOUNT_DISABLED",
    "NT_STATUS_ACCOUNT_LOCKED_OUT",
    "NT_STATUS_PASSWORD_EXPIRED",
    "NT_STATUS_PASSWORD_MUST_CHANGE",
)

# The password was accepted; the *share* refused.  On a server whose shares carry
# `valid users = @somegroup`, this is exactly "authenticated but not permitted", which is worth
# telling the user apart from a bad password -- it costs them a valid password to learn, and
# the alternative is retyping a password that was never the problem.
_SMB_NOT_AUTHORISED = ("NT_STATUS_ACCESS_DENIED",)

# Not about this user at all.  Reporting these as "wrong password" would make a renamed share
# or a stopped service look like every account breaking at once.
_SMB_UNAVAILABLE = (
    "NT_STATUS_BAD_NETWORK_NAME",       # the share does not exist -- renamed or removed
    "NT_STATUS_CONNECTION_REFUSED",
    "NT_STATUS_HOST_UNREACHABLE",
    "NT_STATUS_NETWORK_UNREACHABLE",
    "NT_STATUS_IO_TIMEOUT",
    "NT_STATUS_CONNECTION_DISCONNECTED",
    "NT_STATUS_INVALID_PARAMETER",
)

# smbclient prints this when it fell back to an anonymous session.  On the DCPT server an
# anonymous session setup SUCCEEDS and only the tree connect is refused -- so a probe against
# IPC$, which has no `valid users`, would have let anyone in.  Treated as failure whatever the
# exit code says, because a session nobody authenticated is never a sign-in.
_SMB_ANONYMOUS_MARKER = "anonymous login successful"


class SmbBackend(Backend):
    """Check the password against the site's own Samba, by doing an SMB session setup.

    Authenticates through ``smbclient`` rather than a Python SMB library: it matches how this
    codebase already drives ``sbatch`` and ``dicomexport``, and it keeps the offline wheelhouse
    free of a new dependency (the maintained Python SMB libraries pull in ``cryptography``,
    which is not pure Python).

    **The share is load-bearing, not cosmetic.**  ``IPC$`` accepts a session from any account
    in the passdb -- and, on a standalone server, from an anonymous one.  A real share applies
    its ``valid users``, so pointing this at a share the intended users already reach makes
    Samba's own access control PregDos's, with nothing to maintain twice.  That is why
    ``[auth] smb_share`` has no default and startup refuses without it.

    The cost of that choice, which the docs must state: renaming the share in ``smb.conf``
    breaks sign-in.  It surfaces as ``NT_STATUS_BAD_NETWORK_NAME`` and is reported as a backend
    failure naming the share, not as a wrong password.
    """

    name = "smb"
    login_hint = ("Use your file-share (Samba) password -- the same one you use for the shared "
                  "drives, not your computer login.")

    def __init__(self, server: str, share: str, domain: str = "", timeout: int = 10):
        self.server = server
        self.share = share
        self.domain = domain
        self.timeout = timeout

    def preflight(self) -> str | None:
        if not shutil.which("smbclient"):
            return ("[auth] method = \"smb\" needs the `smbclient` command, which is not on "
                    "PATH. Install the Samba client package (RHEL: `dnf install samba-client`).")
        if not self.share:
            return '[auth] smb_share is empty; name the share to authenticate against'
        return None

    def _argv(self) -> list:
        # No credentials on the command line: /proc/<pid>/cmdline is world-readable, and the
        # environment is readable by the same user.  -A /dev/stdin keeps them on a pipe.
        return [
            "smbclient", f"//{self.server}/{self.share}",
            "-A", "/dev/stdin",
            "-m", "SMB3",
            "--use-kerberos=off",       # this is a standalone server; no ticket to reuse
            "-c", "quit",               # connect and disconnect; touch nothing
        ]

    def check_password(self, username: str, password: str) -> AuthResult:
        # An empty password would make smbclient attempt an ANONYMOUS session, which on a
        # standalone server can succeed.  login() rejects empties before reaching here; this is
        # the second lock on the same door.
        if not username or not password:
            return AuthResult(reason="bad-credentials", detail="empty username or password")

        credentials = f"username = {username}\npassword = {password}\n"
        if self.domain:
            credentials += f"domain = {self.domain}\n"

        try:
            completed = subprocess.run(
                self._argv(), input=credentials, capture_output=True, text=True,
                timeout=self.timeout or None,
            )
        except subprocess.TimeoutExpired:
            return AuthResult(reason="backend-unavailable",
                              detail=f"smbclient did not answer within {self.timeout}s")
        except OSError as exc:
            return AuthResult(reason="backend-unavailable", detail=f"smbclient: {exc}")

        output = f"{completed.stdout}\n{completed.stderr}"
        return self._interpret(username, completed.returncode, output)

    def _interpret(self, username: str, returncode: int, output: str) -> AuthResult:
        """Turn one smbclient run into a result.  Separated so the tests can drive it with
        captured output instead of a live server."""
        if _SMB_ANONYMOUS_MARKER in output.lower():
            # Never a sign-in, whatever the exit code.  See _SMB_ANONYMOUS_MARKER.
            return AuthResult(reason="bad-credentials", detail="smbclient fell back to anonymous")

        if returncode == 0:
            return AuthResult(identity=Identity(username=username))

        found = _NT_STATUS_RE.findall(output)
        status = found[0] if found else ""
        if status in _SMB_BAD_CREDENTIALS:
            return AuthResult(reason="bad-credentials", detail=status)
        if status in _SMB_NOT_AUTHORISED:
            return AuthResult(reason="not-authorised", detail=status)
        # Unknown statuses land here deliberately: an unrecognised failure is the operator's
        # problem and belongs in the journal, not a "wrong password" the user will retype.
        return AuthResult(
            reason="backend-unavailable",
            detail=f"{status or 'smbclient exit ' + str(returncode)} "
                   f"(//{self.server}/{self.share})",
        )


_NT_STATUS_RE = re.compile(r"NT_STATUS_[A-Z_]+")



# ---------------------------------------------------------------------------
# Selection, authorization, and the flow that joins them
# ---------------------------------------------------------------------------

def login_hint(cfg: config.Config | None = None) -> str:
    """What to tell people on the sign-in form about *which* password to type.

    ``[auth] login_hint`` wins over the backend's own wording, so a site can name its actual
    credential store -- "your Samba password for \\\\exrhel0583", say -- or write it in the
    language the people typing it read.  Rendered through Jinja, so it is escaped; it is text,
    not markup.
    """
    cfg = cfg or config.load()
    if cfg.auth.login_hint:
        return cfg.auth.login_hint
    return get_backend(cfg).login_hint


def is_enabled(cfg: config.Config | None = None) -> bool:
    """Whether a login is required at all.  The only place ``"none"`` is special-cased."""
    cfg = cfg or config.load()
    return cfg.auth.method != "none"


def get_backend(cfg: config.Config | None = None) -> Backend:
    cfg = cfg or config.load()
    if cfg.auth.method == "file":
        return FileBackend(password_file_path(cfg))
    if cfg.auth.method == "smb":
        return SmbBackend(cfg.auth.smb_server, cfg.auth.smb_share,
                          cfg.auth.smb_domain, cfg.auth.smb_timeout)
    if cfg.auth.method == "none":
        return NoneBackend()
    # config._validate_auth rejects an unknown method, so reaching here means AUTH_METHODS
    # grew without this function growing with it.
    raise AuthError(f"[auth] method = {cfg.auth.method!r} has no backend in pregdos.auth")


def authorize(username: str, cfg: config.Config | None = None) -> AuthResult:
    """Apply ``allow_users``.  Empty means every account the backend accepts is allowed.

    Comparison is exact.  Usernames are case-sensitive on every system PregDos runs on, and
    folding them here would admit `Alice` on an entry that says `alice` -- a small widening
    of the list that nobody asked for.
    """
    cfg = cfg or config.load()
    if not cfg.auth.allow_users or username in cfg.auth.allow_users:
        return AuthResult(identity=Identity(username=username))
    return AuthResult(reason="not-allowlisted", detail="authenticated but not in allow_users")


def login(username: str, password: str, cfg: config.Config | None = None) -> AuthResult:
    """Authenticate, then authorize.  The only entry point the web layer should call.

    Never raises for a bad password.  :class:`AuthError` -- the backend could not run at all
    -- is turned into a ``backend-unavailable`` result here, so the login view has exactly one
    shape to handle and the operator's problem still reaches the journal.
    """
    cfg = cfg or config.load()
    username = (username or "").strip()
    if not username or not password:
        return AuthResult(reason="bad-credentials", detail="empty username or password")

    try:
        authenticated = get_backend(cfg).check_password(username, password)
    except AuthError as exc:
        log.error("authentication backend unavailable: %s", exc)
        return AuthResult(reason="backend-unavailable", detail=str(exc))
    if not authenticated.ok:
        return authenticated

    assert authenticated.identity is not None
    return authorize(authenticated.identity.username, cfg)


def describe_policy(cfg: config.Config | None = None) -> str:
    """One line for the startup log.  Not a warning -- an audit fact worth having in the
    journal, so that "who could sign in on the day of the incident" has an answer."""
    cfg = cfg or config.load()
    if not is_enabled(cfg):
        return "auth: disabled -- every visitor has full access to every study"
    if cfg.auth.allow_users:
        who = f"allow_users={','.join(cfg.auth.allow_users)}"
    else:
        who = "allow_users=empty (every account the backend accepts)"
    where = ""
    if cfg.auth.method == "smb":
        where = f" smb=//{cfg.auth.smb_server}/{cfg.auth.smb_share}"
    return (f"auth: method={cfg.auth.method}{where} {who} "
            f"session_hours={cfg.auth.session_hours} idle_minutes={cfg.auth.idle_minutes}")


__all__ = [
    "AuthError",
    "AuthResult",
    "Backend",
    "FileBackend",
    "Identity",
    "NoneBackend",
    "SmbBackend",
    "authorize",
    "describe_policy",
    "get_backend",
    "hash_password",
    "is_enabled",
    "login",
    "login_hint",
    "password_file_path",
    "read_password_file",
    "verify_password",
    "write_password_file",
]
