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
    ``host = "127.0.0.1"`` instead: there is no authentication (issue #64), so anyone who can
    reach the port can read every study on the server.

    ``ssl_cert``/``ssl_key`` make the *development* server speak HTTPS.  That is encryption,
    not a production deployment -- see issue #90.  Both or neither.
    """

    host: str = "0.0.0.0"
    port: int = 5000
    ssl_cert: str = ""
    ssl_key: str = ""


@dataclass(frozen=True)
class Network:
    """Outbound network PregDos may attempt.  All of it is optional by design."""

    update_check: bool = True        # false for airgapped sites


@dataclass(frozen=True)
class Config:
    paths: Paths = field(default_factory=Paths)
    scheduler: Scheduler = field(default_factory=Scheduler)
    server: Server = field(default_factory=Server)
    network: Network = field(default_factory=Network)


# The whole schema.  Both the validator and the anti-drift test derive from this, so adding a
# section is a one-line change here plus a dataclass.
_SECTIONS: Dict[str, type] = {"paths": Paths, "scheduler": Scheduler, "server": Server, "network": Network}

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
    "load",
    "reset_cache",
    "set_config_path",
]
