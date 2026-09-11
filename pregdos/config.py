"""Site configuration read from a TOML file, for deployments nobody can log into.

PregDos settings used to be environment variables only, which is fine for a container and
useless for a hospital node: a systemd drop-in full of ``Environment=`` lines cannot be
commented, cannot show the admin what the defaults were, and grows one line per knob.  This
module gives such a site exactly one file to edit.

Precedence, lowest to highest
-----------------------------
1. The built-in defaults in the dataclasses below.
2. ``/etc/pregdos/config.toml`` (:data:`SYSTEM_CONFIG_PATH`).
3. ``$XDG_CONFIG_HOME/pregdos/config.toml``, falling back to ``~/.config``.
4. The environment.

Those two files merge *per key*.  ``--config PATH`` or ``$PREGDOS_CONFIG`` instead
**replaces** the pair, so one deliberate file is the whole story; ``--config`` wins when both
are given.  A file missing from the default stack is skipped, but a file named explicitly and
absent is an error -- a typo'd ``--config`` must never silently fall back to built-in
defaults on a clinical node.

The environment comes last for two reasons: the shipped container keeps working with no
config file at all, and the existing test suites set ``TOPAS_BIN`` and ``PREGDOS_EXECUTOR``
with ``monkeypatch.setenv``.  That only holds if resolvers read the environment on *every*
call, so :func:`load` is cached but :func:`env_or` is not -- see the resolvers in
:mod:`pregdos.executor`, :mod:`pregdos.versions` and :mod:`pregdos.webserver`.

Unknown keys are errors
-----------------------
A typo'd ``work_dr`` that silently does nothing is the worst failure mode when the admin is
at another hospital and you are debugging by email.  Every unknown key, unknown section and
type mismatch raises :class:`ConfigError` naming the file and the key, with a did-you-mean.

This module imports stdlib only -- no Flask, nothing from :mod:`pregdos` -- so any module can
import it without a cycle.
"""

from __future__ import annotations

import difflib
import functools
import importlib.resources
import ipaddress
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Read by the default discovery stack.  A module constant so tests can repoint it, the same
# way versions.MARKER_DIR is -- and config.toml lands beside those TOPAS_VERSION markers.
SYSTEM_CONFIG_PATH = Path("/etc/pregdos/config.toml")

# The one environment variable this feature adds.  Everything else new is file-only; that is
# the point of having a file.
CONFIG_ENV = "PREGDOS_CONFIG"


class ConfigError(Exception):
    """A config file is unreadable, malformed, or names something PregDos does not know."""


@dataclass(frozen=True)
class Paths:
    """Where PregDos keeps its work and finds the programs it drives."""

    # `/var/tmp` (not `/tmp`!) is deliberate: it is persistent disk that survives reboot, and
    # systemd-tmpfiles reaps its contents after ~30 days -- exactly the auto-cleanup we want,
    # since results must be downloaded off the server anyway and stale runs should not pile
    # up.  `/tmp` would be wrong: it is usually a RAM-backed tmpfs, wiped on every reboot and
    # stealing memory from the TOPAS workers (issue #71).
    work_dir: str = "/var/tmp/pregdos"
    topas_bin: str = "topas"
    dicomexport: str = ""            # "" = autodetect beside the running interpreter
    dicomexport_timeout: int = 900   # seconds; 0 = wait forever


@dataclass(frozen=True)
class Scheduler:
    """How a field job is handed to SLURM.

    Every string here is empty by default, and an empty value means the corresponding
    ``sbatch`` flag is **omitted entirely** rather than passed empty -- so SLURM applies its
    own site default for the partition, account, QoS, time limit and memory.
    """

    partition: str = ""
    account: str = ""
    qos: str = ""
    walltime: str = ""               # --time, e.g. "04:00:00"
    memory: str = ""                 # --mem, e.g. "16G"
    cpus_per_task: int = 0           # 0 = os.cpu_count() of the host running the web process
    # "auto" = drop to the container's `slurm` account when running as root; "" = never drop
    # privileges (a site SLURM where PregDos submits as its own service account); any other
    # value = that user.
    submit_as_user: str = "auto"
    # Shell *source* prepended to the wrapped command, for sites where TOPAS lives behind
    # `module load`.  Applies to both backends -- see executor._with_prologue.
    prologue: str = ""


@dataclass(frozen=True)
class Server:
    """Where the web interface listens, and whether it terminates TLS itself.

    The default binds every interface, which is what the shipped container needs to be
    reachable through ``-p``.  A site that fronts PregDos with a reverse proxy should set
    ``host = "127.0.0.1"`` instead.  Authentication is :class:`Auth` below and is **off by
    default**, so on a default install anyone who can reach the port can read every study on
    the server.

    ``ssl_cert``/``ssl_key`` make the *development* server speak HTTPS.  That is encryption,
    not a production deployment -- see issue #90.  Both or neither.
    """

    host: str = "0.0.0.0"
    port: int = 5000
    ssl_cert: str = ""
    ssl_key: str = ""


# The authentication methods pregdos.auth actually implements.  This tuple grows as backends
# land, never ahead of them: a `method` the config accepts but nothing enforces would be a
# site believing it is protected when it is not, which is the worst outcome this module has.
AUTH_METHODS = ("none", "file", "smb")


@dataclass(frozen=True)
class Auth:
    """Who may use this PregDos, and how they prove it.  Issue #103.

    OFF BY DEFAULT.  The shipped container, every existing install and the whole test suite
    predate this section and must keep working untouched, so ``method = "none"`` reproduces
    exactly the behaviour PregDos has always had: no login page, no session, no CSRF token.

    ``"file"`` checks a password against a PregDos-managed file -- see
    :mod:`pregdos.auth`.  It is the method that works everywhere, including the container,
    and the one the tests exercise end to end.  It also means a new password for every user,
    which is precisely what a site with an existing credential store does not want; ``"smb"``
    is being added for that case.

    ``allow_users`` is a NARROWING filter and is empty by default, which means every account
    the backend accepts may sign in.  For ``"file"`` that is the password file itself, which
    is already an explicit list.  Note what this does *not* do: PregDos has no per-user
    segregation, so everyone who signs in sees every study and every patient.  The audit log
    is what records who saw what.
    """

    method: str = "none"
    allow_users: List[str] = field(default_factory=list)
    login_hint: str = ""             # "" = the backend's own wording; see pregdos.auth
    session_hours: int = 12          # absolute cap on one sign-in
    idle_minutes: int = 60           # 0 = never time an idle session out
    password_file: str = ""          # method="file"; "" = $STATE_DIRECTORY/users
    smb_server: str = "localhost"    # method="smb"
    smb_share: str = ""              # REQUIRED for "smb"; see pregdos.auth.SmbBackend
    smb_domain: str = ""
    smb_timeout: int = 10            # seconds; 0 = wait forever
    secret_key_file: str = ""        # "" = $STATE_DIRECTORY/secret_key
    allow_insecure_http: bool = False


@dataclass(frozen=True)
class Network:
    """Outbound network PregDos may attempt.  All of it is optional by design."""

    update_check: bool = True        # false for airgapped sites


@dataclass(frozen=True)
class Config:
    paths: Paths = field(default_factory=Paths)
    scheduler: Scheduler = field(default_factory=Scheduler)
    server: Server = field(default_factory=Server)
    auth: Auth = field(default_factory=Auth)
    network: Network = field(default_factory=Network)


# The whole schema.  Both the validator and the anti-drift test derive from this, so adding a
# section is a one-line change here plus a dataclass.
_SECTIONS: Dict[str, type] = {
    "paths": Paths, "scheduler": Scheduler, "server": Server, "auth": Auth, "network": Network,
}

# Set by `pregdos-web --config PATH`.  Beats $PREGDOS_CONFIG: the flag is the more explicit,
# per-invocation signal.
_cli_path: Path | None = None


def set_config_path(path: str | os.PathLike | None) -> None:
    """Point at one config file, replacing the discovery stack.  Clears the parse cache."""
    global _cli_path
    _cli_path = Path(path) if path else None
    reset_cache()


def reset_cache() -> None:
    """Forget the parsed config.  For tests, and for :func:`set_config_path`."""
    load.cache_clear()


def env_or(name: str, fallback: str) -> str:
    """The environment variable ``name`` if set and non-blank, else the configured value.

    Deliberately not cached: the environment must be re-read on every call, or setting a
    variable after the first config read would stop working.
    """
    return (os.environ.get(name) or "").strip() or fallback


def config_files() -> List[Tuple[Path, bool]]:
    """The files to read, lowest precedence first, as ``(path, must_exist)`` pairs."""
    override = _cli_path or (os.environ.get(CONFIG_ENV) or "").strip() or None
    if override:
        return [(Path(override), True)]
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip() or os.path.join(Path.home(), ".config")
    return [(SYSTEM_CONFIG_PATH, False), (Path(xdg) / "pregdos" / "config.toml", False)]


def state_dir() -> Path:
    """Where PregDos keeps files it generates and must not lose: the session key, the users file.

    ``$STATE_DIRECTORY`` is set by systemd's ``StateDirectory=pregdos`` (see
    packaging/pregdos.service), which creates the directory 0700 and owns it to the service
    account.  systemd may hand over a colon-separated list; the first entry is ours.  Outside
    systemd the XDG state directory is the equivalent.

    Distinct from ``[paths] work_dir``, which holds studies and is reaped after ~30 days.
    Nothing here may ever be reaped.
    """
    state = (os.environ.get("STATE_DIRECTORY") or "").split(":")[0].strip()
    if state:
        return Path(state)
    xdg = (os.environ.get("XDG_STATE_HOME") or "").strip() or str(Path.home() / ".local" / "state")
    return Path(xdg) / "pregdos"


def _did_you_mean(word: str, options) -> str:
    match = difflib.get_close_matches(word, list(options), n=1)
    return f" (did you mean {match[0]!r}?)" if match else ""


def _expected_types(cls: type) -> Dict[str, type]:
    """Field name -> expected type, taken from the *default value* of each field.

    Deriving from the default rather than the annotation keeps this working under
    ``from __future__ import annotations``, where annotations are strings.  It does mean a
    ``float`` field would reject a TOML integer; no such key exists, and widening it is two
    lines when one appears.
    """
    defaults = cls()
    return {f.name: type(getattr(defaults, f.name)) for f in fields(cls)}


def _read_file(path: Path, must_exist: bool) -> Dict[str, Dict[str, Any]]:
    """Parse and fully validate one file.  Returns only the keys it actually sets."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if must_exist:
            raise ConfigError(f"{path}: config file does not exist") from None
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc

    parsed: Dict[str, Dict[str, Any]] = {}
    for section, values in raw.items():
        if section not in _SECTIONS:
            raise ConfigError(f"{path}: unknown section [{section}]{_did_you_mean(section, _SECTIONS)}")
        if not isinstance(values, dict):
            raise ConfigError(
                f"{path}: [{section}] must be a table, got {type(values).__name__} -- "
                f"write it as a [{section}] header with keys under it"
            )
        expected = _expected_types(_SECTIONS[section])
        for key, value in values.items():
            if key not in expected:
                raise ConfigError(f"{path}: [{section}]: unknown key {key!r}{_did_you_mean(key, expected)}")
            # `type(...) is not` rather than isinstance: bool is a subclass of int, so
            # `dicomexport_timeout = true` would otherwise be accepted as an integer.
            if type(value) is not expected[key]:
                raise ConfigError(
                    f"{path}: [{section}] {key}: expected {expected[key].__name__}, "
                    f"got {type(value).__name__} ({value!r})"
                )
        parsed[section] = dict(values)
    return parsed


def _validate_values(cfg: Config, sources: List[Path]) -> None:
    """Checks the per-key validator cannot express: ranges, and pairs of keys.

    Runs on the merged result rather than per file.  The two halves of a pair may legitimately
    arrive from different files -- the system file and the user one merge per key -- so this
    can only name the files as a set.

    The per-key validator above enforces *types*, which is what catches a typo.  It cannot
    catch a value of the right type that is meaningless, and every one of those below reaches
    somewhere it degrades badly rather than loudly: a negative timeout kills each conversion
    at once claiming it "did not finish within -5 s", and a negative CPU count quietly means
    "every core on the machine".
    """
    where = ", ".join(str(p) for p in sources)

    if not 1 <= cfg.server.port <= 65535:
        raise ConfigError(f"{where}: [server] port: {cfg.server.port} is not a valid port (1-65535)")

    for section, key, value, meaning_of_zero in (
        ("paths", "dicomexport_timeout", cfg.paths.dicomexport_timeout, "wait forever"),
        ("scheduler", "cpus_per_task", cfg.scheduler.cpus_per_task, "use every core this machine reports"),
        ("auth", "idle_minutes", cfg.auth.idle_minutes, "never time an idle session out"),
        ("auth", "smb_timeout", cfg.auth.smb_timeout, "wait forever for the SMB server"),
    ):
        if value < 0:
            raise ConfigError(
                f"{where}: [{section}] {key}: {value} is negative. Use a positive value, "
                f"or 0 to {meaning_of_zero}."
            )

    # Half a TLS pair is the dangerous case: Flask would fall back to plain HTTP, and the
    # admin who wrote one line of two would have no reason to look.  Refuse instead.
    if bool(cfg.server.ssl_cert) != bool(cfg.server.ssl_key):
        missing = "ssl_key" if cfg.server.ssl_cert else "ssl_cert"
        raise ConfigError(
            f"{where}: [server] ssl_cert and ssl_key must be set together -- {missing} is "
            f"missing, and PregDos will not silently serve plain HTTP when TLS was intended"
        )

    _validate_auth(cfg, where)


def _validate_auth(cfg: Config, where: str) -> None:
    """The [auth] rules.  Split out only because there are enough of them to lose the others."""
    if cfg.auth.method not in AUTH_METHODS:
        raise ConfigError(
            f"{where}: [auth] method: unknown method {cfg.auth.method!r}"
            f"{_did_you_mean(cfg.auth.method, AUTH_METHODS)}. "
            f"Known methods: {', '.join(repr(m) for m in AUTH_METHODS)}."
        )

    # The one thing the per-key check cannot see: a TOML array satisfies `list` whatever is
    # inside it.  A stray integer would simply never match a username, locking out the person
    # who wrote it with no error anywhere -- and, since the list is a *filter*, doing so
    # silently.
    for value in cfg.auth.allow_users:
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"{where}: [auth] allow_users: every entry must be a non-empty string, "
                f"got {value!r}"
            )

    # The dangerous asymmetry.  An allowlist with no method does nothing at all, and the admin
    # who wrote one has every reason to believe the server is now protected.
    if cfg.auth.method == "none" and cfg.auth.allow_users:
        raise ConfigError(
            f'{where}: [auth] allow_users is set but method is "none", so nothing is '
            f"enforced and every visitor still has full access to every study. Set a method, "
            f"or remove allow_users."
        )

    # IPC$ would be the convenient default and is the wrong one: it carries no `valid users`,
    # so it admits every account in the passdb -- and on a standalone server an anonymous
    # session setup can succeed against it outright.  A real share applies its own access
    # control, which is the whole reason this method is safe without an allowlist.  So there is
    # no default: naming the share is a decision the site has to make deliberately.
    if cfg.auth.method == "smb" and not cfg.auth.smb_share.strip():
        raise ConfigError(
            f'{where}: [auth] method = "smb" needs smb_share -- the share to authenticate '
            f"against, e.g. smb_share = \"users\". Name one whose `valid users` already lists "
            f"the people who should reach PregDos; Samba then does the authorisation. Do not "
            f"use IPC$: it has no `valid users`, so it would admit every account on the server."
        )

    if cfg.auth.session_hours < 1:
        raise ConfigError(
            f"{where}: [auth] session_hours: {cfg.auth.session_hours} must be at least 1 -- "
            f"there is no way to express 'never expires', deliberately."
        )

    # Same shape as the TLS pair above.  A password typed into a login form that travels in
    # clear over a shared network is worse than no login at all: it hands out a credential
    # that, at a site reusing one, opens more than PregDos.
    if reason := insecure_auth_reason(
        cfg.auth.method, cfg.server.host, cfg.server.ssl_cert, cfg.auth.allow_insecure_http
    ):
        raise ConfigError(f"{where}: {reason}")


def insecure_auth_reason(method: str, host: str, ssl_cert: str, allow_insecure_http: bool) -> str | None:
    """Why enabling a login on this listener would send passwords in clear, or None.

    A module-level function rather than a branch inside :func:`_validate_values`, because
    ``pregdos-web --host`` never passes through config validation at all: a config that says
    ``host = "127.0.0.1"`` run as ``--host 0.0.0.0`` would otherwise defeat this entirely.
    ``main()`` calls it again on the host it is actually about to bind.
    """
    if method == "none" or ssl_cert or allow_insecure_http:
        return None
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an address: a hostname, or "" (which Flask treats as localhost).
        loopback = host in ("localhost", "")
    if loopback:
        return None
    return (
        f'[auth] method = "{method}" on [server] host = "{host}" with no TLS would send every '
        f"password over the network in clear. Set [server] ssl_cert and ssl_key, or bind "
        f"127.0.0.1 and put a reverse proxy in front, or -- if a proxy already terminates TLS "
        f"ahead of this port -- set [auth] allow_insecure_http = true."
    )


@functools.lru_cache(maxsize=1)
def load() -> Config:
    """The merged configuration.  Parsed once per process; call :func:`reset_cache` to reread.

    Each file is validated on its own and only then merged, so an error can name the file the
    offending key came from -- and so a lower-precedence file's value is overwritten only by a
    key another file actually sets, never by that file's defaults.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for path, must_exist in config_files():
        for section, values in _read_file(path, must_exist).items():
            merged.setdefault(section, {}).update(values)
    cfg = Config(**{name: cls(**merged.get(name, {})) for name, cls in _SECTIONS.items()})
    _validate_values(cfg, [path for path, _ in config_files()])
    return cfg


def example_text() -> str:
    """The annotated example config shipped as package data.

    One copy only: the packaging installs *this* file to /etc/pregdos/config.toml, so a
    second copy under packaging/ would drift.
    """
    return (importlib.resources.files("pregdos") / "data" / "config.toml.example").read_text(encoding="utf-8")


__all__ = [
    "AUTH_METHODS",
    "Auth",
    "CONFIG_ENV",
    "Config",
    "ConfigError",
    "Network",
    "Paths",
    "SYSTEM_CONFIG_PATH",
    "Scheduler",
    "Server",
    "config_files",
    "env_or",
    "example_text",
    "insecure_auth_reason",
    "load",
    "state_dir",
    "reset_cache",
    "set_config_path",
]
