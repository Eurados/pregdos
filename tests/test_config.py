"""Tests for the site config file: discovery, precedence, validation, and the shipped example."""

import re
from dataclasses import fields

import pytest

from pregdos import config, executor, versions


# ---------------------------------------------------------------------------
# Precedence: defaults, /etc, user, and the two files merging
# ---------------------------------------------------------------------------

def test_defaults_alone_when_no_file_exists():
    cfg = config.load()
    assert cfg == config.Config()
    assert cfg.paths.work_dir == "/var/tmp/pregdos"
    assert cfg.scheduler.partition == ""
    assert cfg.network.update_check is True


def test_system_file_alone(tmp_path, monkeypatch):
    system = tmp_path / "etc.toml"
    system.write_text('[paths]\nwork_dir = "/srv/pregdos"\n')
    monkeypatch.setattr(config, "SYSTEM_CONFIG_PATH", system)
    config.reset_cache()

    assert config.load().paths.work_dir == "/srv/pregdos"


def test_user_file_alone(tmp_path, monkeypatch):
    user = tmp_path / "xdg" / "pregdos"
    user.mkdir(parents=True)
    (user / "config.toml").write_text('[paths]\ntopas_bin = "/opt/topas/bin/topas"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config.reset_cache()

    assert config.load().paths.topas_bin == "/opt/topas/bin/topas"


def test_user_file_falls_back_to_dot_config_when_xdg_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: tmp_path))
    config.reset_cache()

    paths = [path for path, _ in config.config_files()]
    assert paths[-1] == tmp_path / ".config" / "pregdos" / "config.toml"


def test_system_and_user_merge_per_key(tmp_path, monkeypatch):
    system = tmp_path / "etc.toml"
    system.write_text('[paths]\nwork_dir = "/srv/pregdos"\ntopas_bin = "/system/topas"\n'
                      '[scheduler]\npartition = "clinical"\n')
    user_dir = tmp_path / "xdg" / "pregdos"
    user_dir.mkdir(parents=True)
    (user_dir / "config.toml").write_text('[paths]\ntopas_bin = "/user/topas"\n')
    monkeypatch.setattr(config, "SYSTEM_CONFIG_PATH", system)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config.reset_cache()

    cfg = config.load()
    assert cfg.paths.work_dir == "/srv/pregdos"        # only /etc set it
    assert cfg.scheduler.partition == "clinical"       # only /etc set it
    assert cfg.paths.topas_bin == "/user/topas"        # user wins where both set it


def test_pregdos_config_replaces_the_stack_rather_than_merging(tmp_path, monkeypatch):
    """The assertion that discriminates "replaces" from "merges onto"."""
    system = tmp_path / "etc.toml"
    system.write_text('[paths]\nwork_dir = "/srv/from-etc"\n')
    override = tmp_path / "only.toml"
    override.write_text('[paths]\ntopas_bin = "/opt/topas"\n')
    monkeypatch.setattr(config, "SYSTEM_CONFIG_PATH", system)
    monkeypatch.setenv(config.CONFIG_ENV, str(override))
    config.reset_cache()

    cfg = config.load()
    assert cfg.paths.topas_bin == "/opt/topas"
    assert cfg.paths.work_dir == "/var/tmp/pregdos"    # the built-in default, NOT /srv/from-etc


def test_cli_config_beats_the_environment_variable(tmp_path, monkeypatch):
    from_env = tmp_path / "env.toml"
    from_env.write_text('[paths]\nwork_dir = "/from/env"\n')
    from_cli = tmp_path / "cli.toml"
    from_cli.write_text('[paths]\nwork_dir = "/from/cli"\n')
    monkeypatch.setenv(config.CONFIG_ENV, str(from_env))
    config.set_config_path(from_cli)

    assert config.load().paths.work_dir == "/from/cli"


def test_explicitly_named_missing_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "typo.toml"))
    config.reset_cache()

    with pytest.raises(config.ConfigError, match="does not exist"):
        config.load()


def test_missing_file_in_the_default_stack_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SYSTEM_CONFIG_PATH", tmp_path / "absent.toml")
    config.reset_cache()

    assert config.load() == config.Config()


# ---------------------------------------------------------------------------
# The environment wins, and keeps winning after the config has been read
# ---------------------------------------------------------------------------

def test_environment_beats_the_config_file(write_config, monkeypatch):
    write_config('[paths]\ntopas_bin = "/from/config"\n')
    monkeypatch.setenv("TOPAS_BIN", "/from/env")

    assert executor.topas_bin() == "/from/env"
    assert versions.topas_bin() == "/from/env"


def test_environment_set_after_the_first_read_is_still_honoured(write_config, monkeypatch):
    """The invariant that keeps the existing monkeypatch.setenv suites green."""
    write_config('[paths]\ntopas_bin = "/from/config"\n')
    assert executor.topas_bin() == "/from/config"      # populates the parse cache

    monkeypatch.setenv("TOPAS_BIN", "/set/afterwards")
    assert executor.topas_bin() == "/set/afterwards"


def test_blank_environment_variable_falls_through_to_config(write_config, monkeypatch):
    write_config('[paths]\ntopas_bin = "/from/config"\n')
    monkeypatch.setenv("TOPAS_BIN", "   ")

    assert executor.topas_bin() == "/from/config"


def test_work_dir_comes_from_config_when_env_is_unset(write_config, monkeypatch):
    from pregdos import webserver

    monkeypatch.delenv("PREGDOS_WORK_DIR", raising=False)
    write_config('[paths]\nwork_dir = "/srv/studies"\n')

    assert webserver._resolve_work_dir() == "/srv/studies"


# ---------------------------------------------------------------------------
# Errors name the file and the key
# ---------------------------------------------------------------------------

def test_unknown_key_names_file_section_and_key(write_config):
    path = write_config('[paths]\nwork_dr = "/oops"\n')

    with pytest.raises(config.ConfigError) as exc:
        config.load()
    message = str(exc.value)
    assert str(path) in message
    assert "[paths]" in message
    assert "'work_dr'" in message
    assert "work_dir" in message          # the did-you-mean


def test_unknown_section_names_file_and_section(write_config):
    path = write_config('[scheduluer]\npartition = "x"\n')

    with pytest.raises(config.ConfigError) as exc:
        config.load()
    assert str(path) in str(exc.value)
    assert "scheduluer" in str(exc.value)
    assert "scheduler" in str(exc.value)   # the did-you-mean


def test_top_level_key_outside_a_section_is_an_error(write_config):
    write_config('work_dir = "/oops"\n')

    with pytest.raises(config.ConfigError, match="unknown section"):
        config.load()


def test_section_that_is_not_a_table_is_an_error(write_config):
    write_config("paths = 3\n")

    with pytest.raises(config.ConfigError, match="must be a table"):
        config.load()


@pytest.mark.parametrize("body, expected", [
    ('[paths]\ndicomexport_timeout = "900"\n', "expected int, got str"),
    ('[network]\nupdate_check = "yes"\n', "expected bool, got str"),
    # bool is a subclass of int, so an isinstance check would let this through.
    ("[paths]\ndicomexport_timeout = true\n", "expected int, got bool"),
    ("[scheduler]\npartition = 3\n", "expected str, got int"),
])
def test_type_mismatch_is_an_error(write_config, body, expected):
    path = write_config(body)

    with pytest.raises(config.ConfigError) as exc:
        config.load()
    assert expected in str(exc.value)
    assert str(path) in str(exc.value)


def test_malformed_toml_names_the_file(write_config):
    path = write_config("[paths\nwork_dir = ohno\n")

    with pytest.raises(config.ConfigError) as exc:
        config.load()
    assert str(path) in str(exc.value)
    assert "invalid TOML" in str(exc.value)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_load_is_cached_until_reset(write_config):
    path = write_config('[paths]\nwork_dir = "/first"\n')
    assert config.load().paths.work_dir == "/first"

    path.write_text('[paths]\nwork_dir = "/second"\n')
    assert config.load().paths.work_dir == "/first"     # still the cached parse

    config.reset_cache()
    assert config.load().paths.work_dir == "/second"


# ---------------------------------------------------------------------------
# [server]: checks that span two keys, so they run on the merged result
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body, missing", [
    ('[server]\nssl_cert = "/etc/pregdos/cert.pem"\n', "ssl_key"),
    ('[server]\nssl_key = "/etc/pregdos/key.pem"\n', "ssl_cert"),
])
def test_half_a_tls_pair_is_an_error(write_config, body, missing):
    """Silently serving plain HTTP when TLS was intended is the failure nobody would notice."""
    write_config(body)
    with pytest.raises(config.ConfigError, match=missing):
        config.load()


def test_a_tls_pair_split_across_the_two_files_is_accepted(tmp_path, monkeypatch):
    """The pair is validated after merging, so /etc may hold one half and the user file the other."""
    system = tmp_path / "etc.toml"
    system.write_text('[server]\nssl_cert = "/etc/pregdos/cert.pem"\n')
    monkeypatch.setattr(config, "SYSTEM_CONFIG_PATH", system)
    user = tmp_path / "xdg" / "pregdos"
    user.mkdir(parents=True)
    (user / "config.toml").write_text('[server]\nssl_key = "/etc/pregdos/key.pem"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config.reset_cache()

    assert config.load().server.ssl_key == "/etc/pregdos/key.pem"


@pytest.mark.parametrize("body, key", [
    ("[paths]\ndicomexport_timeout = -5\n", "dicomexport_timeout"),
    ("[scheduler]\ncpus_per_task = -1\n", "cpus_per_task"),
])
def test_negative_counts_are_rejected_with_what_zero_means(write_config, body, key):
    """The type check passes -- these are integers -- but the value degrades silently.

    A negative timeout makes every conversion die at once claiming it "did not finish within
    -5 s"; a negative CPU count is simply ignored in favour of every core on the machine.
    """
    write_config(body)
    with pytest.raises(config.ConfigError, match=f"{key}: -"):
        config.load()


@pytest.mark.parametrize("body", [
    "[paths]\ndicomexport_timeout = 0\n",       # documented: wait forever
    "[scheduler]\ncpus_per_task = 0\n",         # documented: as many as the host reports
])
def test_zero_stays_meaningful(write_config, body):
    write_config(body)
    config.load()      # raises if zero was caught up in the negativity check


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_port_outside_the_valid_range_is_an_error(write_config, port):
    write_config(f"[server]\nport = {port}\n")
    with pytest.raises(config.ConfigError, match="not a valid port"):
        config.load()


# ---------------------------------------------------------------------------
# [auth]: an optional login is only safe if it cannot be half-configured
# ---------------------------------------------------------------------------

def test_unknown_auth_method_is_an_error_with_a_suggestion(write_config):
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "fiel"\n')
    with pytest.raises(config.ConfigError, match="did you mean 'file'"):
        config.load()


@pytest.mark.parametrize("entry", ["3", '""', "true"])
def test_allow_users_entries_must_be_non_empty_strings(write_config, entry):
    """The per-key check cannot see inside a TOML array, and a stray integer would simply
    never match a username -- locking out whoever wrote it, silently."""
    write_config(f'[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\nallow_users = [{entry}]\n')
    with pytest.raises(config.ConfigError, match="allow_users"):
        config.load()


def test_an_allowlist_without_a_method_is_an_error(write_config):
    """The dangerous asymmetry: it enforces nothing, and the admin who wrote it has every
    reason to believe the server is now protected."""
    write_config('[auth]\nmethod = "none"\nallow_users = ["alice"]\n')
    with pytest.raises(config.ConfigError, match="nothing is enforced"):
        config.load()


def test_a_method_with_no_allowlist_is_accepted(write_config):
    """Empty means every account the backend accepts -- the documented default, and the
    decision taken for DCPT.  Not an error."""
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\n')

    assert config.load().auth.allow_users == []


def test_session_hours_must_be_at_least_one(write_config):
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\nsession_hours = 0\n')
    with pytest.raises(config.ConfigError, match="session_hours"):
        config.load()


def test_idle_minutes_zero_means_no_idle_timeout(write_config):
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\nidle_minutes = 0\n')

    assert config.load().auth.idle_minutes == 0


def test_negative_idle_minutes_is_rejected_with_what_zero_means(write_config):
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\nidle_minutes = -1\n')
    with pytest.raises(config.ConfigError, match="idle_minutes: -1"):
        config.load()


# -- a login must not send passwords in clear -------------------------------

def test_a_login_on_a_public_interface_without_tls_is_an_error(write_config):
    write_config('[server]\nhost = "0.0.0.0"\n[auth]\nmethod = "file"\n')
    with pytest.raises(config.ConfigError, match="in clear"):
        config.load()


@pytest.mark.parametrize("body", [
    # TLS terminated here
    '[server]\nhost = "0.0.0.0"\nssl_cert = "/c.pem"\nssl_key = "/k.pem"\n[auth]\nmethod = "file"\n',
    # loopback only, with a reverse proxy in front
    '[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\n',
    '[server]\nhost = "localhost"\n[auth]\nmethod = "file"\n',
    # deliberate opt-out, for a container behind a TLS-terminating proxy
    '[server]\nhost = "0.0.0.0"\n[auth]\nmethod = "file"\nallow_insecure_http = true\n',
])
def test_the_three_ways_out_of_the_plain_http_rule(write_config, body):
    write_config(body)

    config.load()       # raises if any of these stopped being accepted


def test_no_tls_is_still_fine_while_there_is_no_login(write_config):
    """The rule is about passwords crossing the network, not about TLS in general -- PregDos
    has always served plain HTTP and must keep being able to."""
    write_config('[server]\nhost = "0.0.0.0"\n[auth]\nmethod = "none"\n')

    config.load()


@pytest.mark.parametrize("host, expected", [
    ("0.0.0.0", True),
    ("10.141.32.194", True),
    ("exrhel0583.it.rm.dk", True),
    ("127.0.0.1", False),
    ("::1", False),
    ("localhost", False),
])
def test_insecure_auth_reason_judges_the_host_it_is_given(host, expected):
    """A module-level helper precisely so main() can re-check the host --host actually bound,
    which never passes through config validation at all."""
    reason = config.insecure_auth_reason("file", host, ssl_cert="", allow_insecure_http=False)

    assert bool(reason) is expected


# ---------------------------------------------------------------------------
# The shipped example must not drift from the dataclasses
# ---------------------------------------------------------------------------

def _uncomment(text: str) -> str:
    """Strip the leading "# " from key lines, leaving "##" prose comments alone."""
    out = []
    for line in text.splitlines():
        out.append(re.sub(r"^#\s?(?=[A-Za-z_][A-Za-z0-9_]*\s*=)", "", line))
    return "\n".join(out)


def _example_tables() -> dict:
    import tomllib

    return tomllib.loads(_uncomment(config.example_text()))


def test_shipped_example_parses_as_valid_toml_while_fully_commented():
    import tomllib

    tables = tomllib.loads(config.example_text())
    # As shipped, every key is commented out, so the sections exist but are empty.
    assert set(tables) == set(config._SECTIONS)
    assert all(table == {} for table in tables.values())


def test_shipped_example_key_set_matches_the_dataclasses_both_ways():
    tables = _example_tables()
    assert set(tables) == set(config._SECTIONS)
    for section, cls in config._SECTIONS.items():
        assert set(tables[section]) == {f.name for f in fields(cls)}, f"[{section}] drifted"


def test_shipped_example_documents_the_real_defaults():
    """Catches a stale *documented* default, which a key-set-only check would miss."""
    tables = _example_tables()
    for section, cls in config._SECTIONS.items():
        defaults = cls()
        for name, value in tables[section].items():
            expected = getattr(defaults, name)
            # `prologue` is shown as a worked module-load example, not as its empty default.
            if (section, name) == ("scheduler", "prologue"):
                assert expected == ""
                assert "module load" in value
                continue
            assert value == expected, f"[{section}] {name}: example says {value!r}, default is {expected!r}"


def test_uncommented_example_loads_cleanly(tmp_path, monkeypatch):
    """The example is not just shaped right, it validates."""
    path = tmp_path / "config.toml"
    path.write_text(_uncomment(config.example_text()))
    monkeypatch.setenv(config.CONFIG_ENV, str(path))
    config.reset_cache()

    config.load()   # raises ConfigError if the example names anything unknown


# -- method = "smb": the share is load-bearing --------------------------------

def test_smb_without_a_share_is_refused(write_config):
    """IPC$ would be the convenient default and the wrong one: no `valid users`, and an
    anonymous session setup against it can succeed on a standalone server."""
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "smb"\n')
    with pytest.raises(config.ConfigError, match="smb_share"):
        config.load()


def test_smb_with_a_share_is_accepted(write_config):
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "smb"\nsmb_share = "users"\n')

    assert config.load().auth.smb_share == "users"


def test_the_smb_refusal_names_ipc_as_the_thing_not_to_do(write_config):
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "smb"\n')
    with pytest.raises(config.ConfigError, match="IPC"):
        config.load()


@pytest.mark.parametrize("share", ["IPC$", "ipc$", "Ipc$", " IPC$ "])
def test_ipc_is_refused_and_not_merely_warned_about(write_config, share):
    """Naming IPC$ in the error message for a *missing* share is not the same as refusing it.

    IPC$ carries no `valid users`, so a site that configures it gets a login gate admitting
    every account in the passdb -- and, on a standalone server, an anonymous session.  Share
    names are case-insensitive in SMB, so `ipc$` has to be the same door.
    """
    write_config(f'[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "smb"\nsmb_share = "{share}"\n')
    with pytest.raises(config.ConfigError, match="IPC"):
        config.load()


def test_a_share_merely_containing_ipc_is_still_allowed(write_config):
    """The refusal is the exact name, not a substring: `ipcs` is an ordinary share."""
    write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "smb"\nsmb_share = "ipcs"\n')

    assert config.load().auth.smb_share == "ipcs"
