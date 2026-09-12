"""The authentication backends and the allowlist.  Issue #103.

No Flask here -- this is the layer that decides whether a password belongs to a username, and
whether that username may use PregDos at all.  The gate that consumes it is test_auth_gate.py.
"""

import pathlib

import pytest

from pregdos import auth, config


@pytest.fixture
def users_file(tmp_path):
    """A password file with one account, written the way pregdos-passwd writes it."""
    path = tmp_path / "users"
    auth.write_password_file(path, {"alice": auth.hash_password("correct horse")})
    return path


def _cfg(write_config, users_file, extra=""):
    write_config(
        '[server]\nhost = "127.0.0.1"\n\n'
        f'[auth]\nmethod = "file"\npassword_file = "{users_file}"\n{extra}'
    )
    return config.load()


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def test_hash_is_salted_so_two_users_with_one_password_do_not_look_alike():
    """Equal hashes would leak that two accounts share a password."""
    assert auth.hash_password("same") != auth.hash_password("same")


def test_hash_round_trips_and_rejects_the_wrong_password():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)
    assert not auth.verify_password("Correct horse", stored)
    assert not auth.verify_password("", stored)


def test_hash_carries_its_own_parameters():
    """So raising the cost later re-hashes on next change rather than invalidating the file."""
    scheme, n, r, p, _salt, _key = auth.hash_password("x").split("$")
    assert (scheme, int(n), int(r), int(p)) == ("scrypt", 16384, 8, 1)


@pytest.mark.parametrize("stored", [
    "",
    "not-a-hash",
    "scrypt$16384$8",                    # truncated
    "bcrypt$16384$8$1$AAAA$BBBB",        # a scheme we do not implement
    "scrypt$notanumber$8$1$AAAA$BBBB",
])
def test_a_malformed_entry_fails_closed_instead_of_raising(stored):
    """One corrupt line must lock out one account, not 500 the whole login page."""
    assert not auth.verify_password("anything", stored)


# ---------------------------------------------------------------------------
# The password file
# ---------------------------------------------------------------------------

def test_password_file_is_written_0600(users_file):
    assert users_file.stat().st_mode & 0o777 == 0o600


def test_password_file_readable_by_others_is_refused(users_file):
    users_file.chmod(0o644)
    with pytest.raises(auth.AuthError, match="chmod 0600"):
        auth.read_password_file(users_file)


def test_password_file_permissions_are_not_checked_on_windows(users_file, mocker):
    """Windows has no POSIX mode bits: os.stat reports 0666 for any ordinary file, so the bare
    `st_mode & 0o077` test rejected *every* password file and made method = "file" unusable on
    a platform this project supports.  Skipped rather than faked -- see portable."""
    users_file.chmod(0o644)
    mocker.patch("pregdos.portable._no_posix_mode_bits", return_value=True)

    assert "alice" in auth.read_password_file(users_file)


def test_missing_password_file_says_how_to_make_one(tmp_path):
    with pytest.raises(auth.AuthError, match="pregdos-passwd add"):
        auth.read_password_file(tmp_path / "nope")


def test_a_missing_file_is_still_reported_as_missing_on_windows(tmp_path, mocker):
    """The permission check moved inside the try/except that turns a missing file into the
    'create it with pregdos-passwd' message; skipping the check must not skip that."""
    mocker.patch("pregdos.portable._no_posix_mode_bits", return_value=True)

    with pytest.raises(auth.AuthError, match="pregdos-passwd add"):
        auth.read_password_file(tmp_path / "nope")


def test_password_file_skips_blank_lines_and_comments(tmp_path):
    path = tmp_path / "users"
    path.write_text("# a comment\n\nalice:scrypt$1$2$3$AA$BB\n\n")
    path.chmod(0o600)
    assert list(auth.read_password_file(path)) == ["alice"]


def test_write_password_file_replaces_atomically(tmp_path, users_file):
    """tmp-file + os.replace, so a crash cannot leave a half-written file locking everyone out."""
    auth.write_password_file(users_file, {"bob": auth.hash_password("x")})

    assert list(auth.read_password_file(users_file)) == ["bob"]
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# FileBackend
# ---------------------------------------------------------------------------

def test_correct_password_signs_in(write_config, users_file):
    result = auth.login("alice", "correct horse", _cfg(write_config, users_file))

    assert result.ok
    assert result.identity.username == "alice"


@pytest.mark.parametrize("username, password", [
    ("alice", "wrong"),
    ("alice", ""),
    ("nosuchuser", "correct horse"),
    ("", "correct horse"),
])
def test_bad_credentials_are_reported_as_such(write_config, users_file, username, password):
    result = auth.login(username, password, _cfg(write_config, users_file))

    assert not result.ok
    assert result.reason == "bad-credentials"


def test_an_unknown_user_is_not_distinguishable_from_a_wrong_password(write_config, users_file):
    """The user-facing reason must be identical, or the login page enumerates accounts.

    The two are still told apart in `detail`, which goes to the audit log and nowhere else.
    """
    cfg = _cfg(write_config, users_file)

    wrong = auth.login("alice", "wrong", cfg)
    unknown = auth.login("mallory", "wrong", cfg)

    assert wrong.reason == unknown.reason == "bad-credentials"
    assert wrong.detail != unknown.detail


def test_a_broken_backend_is_unavailable_not_a_wrong_password(write_config, users_file):
    """An outage must not present as everybody's password suddenly being wrong."""
    cfg = _cfg(write_config, users_file)
    users_file.unlink()

    result = auth.login("alice", "correct horse", cfg)

    assert result.reason == "backend-unavailable"


def test_preflight_names_a_missing_password_file(write_config, users_file):
    cfg = _cfg(write_config, users_file)
    users_file.unlink()

    assert "pregdos-passwd add" in auth.get_backend(cfg).preflight()


def test_preflight_refuses_a_password_file_with_no_accounts(write_config, users_file):
    """Otherwise the server starts and rejects everyone, with nothing to say why."""
    cfg = _cfg(write_config, users_file)
    auth.write_password_file(users_file, {})

    assert "no accounts" in auth.get_backend(cfg).preflight()


def test_preflight_passes_on_a_healthy_file(write_config, users_file):
    assert auth.get_backend(_cfg(write_config, users_file)).preflight() is None


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------

def test_an_empty_allowlist_admits_every_account_the_backend_accepts(write_config, users_file):
    """The documented default, and the decision taken for the DCPT deployment."""
    cfg = _cfg(write_config, users_file)

    assert not cfg.auth.allow_users
    assert auth.login("alice", "correct horse", cfg).ok


def test_an_allowlist_narrows_to_the_names_on_it(write_config, users_file):
    cfg = _cfg(write_config, users_file, extra='allow_users = ["bob"]\n')

    result = auth.login("alice", "correct horse", cfg)

    assert not result.ok
    assert result.reason == "not-allowlisted"


def test_the_allowlist_is_applied_after_the_password_not_before(write_config, users_file):
    """Checking it first would tell an unauthenticated visitor who is on the list."""
    cfg = _cfg(write_config, users_file, extra='allow_users = ["bob"]\n')

    assert auth.login("alice", "wrong", cfg).reason == "bad-credentials"
    assert auth.login("alice", "correct horse", cfg).reason == "not-allowlisted"


def test_the_allowlist_is_case_sensitive(write_config, users_file):
    cfg = _cfg(write_config, users_file, extra='allow_users = ["Alice"]\n')

    assert auth.login("alice", "correct horse", cfg).reason == "not-allowlisted"


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def test_authentication_is_off_by_default():
    """The rest of the test suite makes 92 unauthenticated calls that depend on this, as does
    every install that predates [auth]."""
    assert config.Config().auth.method == "none"
    assert not auth.is_enabled(config.Config())


def test_the_none_backend_refuses_to_be_used_as_a_login(write_config):
    write_config("[auth]\nmethod = \"none\"\n")

    with pytest.raises(auth.AuthError, match="nothing to sign in to"):
        auth.get_backend(config.load()).check_password("alice", "x")


def test_describe_policy_says_plainly_when_there_is_no_authentication():
    """It goes in the journal at startup, so "who could sign in that day" has an answer."""
    assert "disabled" in auth.describe_policy(config.Config())


def test_describe_policy_names_the_method_and_who_may_sign_in(write_config, users_file):
    line = auth.describe_policy(_cfg(write_config, users_file, extra='allow_users = ["bob"]\n'))

    assert "method=file" in line
    assert "bob" in line


# ---------------------------------------------------------------------------
# pregdos-passwd: where it writes
#
# The first real deployment lost a service restart to this.  $STATE_DIRECTORY is exported by
# systemd to the *service* only, so running the CLI from a shell silently resolved the default
# to $HOME/.local/state/... -- the account was created, in a place pregdos-web does not read,
# and the service refused to start minutes later naming a different path.
# ---------------------------------------------------------------------------

def test_passwd_refuses_to_guess_a_path_the_service_will_not_read(write_config, monkeypatch, capsys):
    from pregdos import passwd

    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    path = write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\n')

    with pytest.raises(SystemExit):
        passwd.main(["--config", str(path), "add", "alice"])

    assert "will not guess" in capsys.readouterr().err


def test_passwd_writes_where_the_config_says(write_config, monkeypatch, tmp_path):
    from pregdos import passwd

    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    monkeypatch.setattr(passwd, "_prompt_for_password", lambda _user: "secret")
    target = tmp_path / "users"
    path = write_config(
        f'[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\npassword_file = "{target}"\n'
    )

    passwd.main(["--config", str(path), "add", "alice"])

    assert list(auth.read_password_file(target)) == ["alice"]


def test_passwd_follows_state_directory_when_systemd_set_it(write_config, monkeypatch, tmp_path):
    from pregdos import passwd

    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(passwd, "_prompt_for_password", lambda _user: "secret")
    path = write_config('[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\n')

    passwd.main(["--config", str(path), "add", "alice"])

    assert (tmp_path / "users").is_file()


# ---------------------------------------------------------------------------
# SmbBackend
#
# The status strings below are what `smbclient` actually returned on the DCPT server
# (issue #103), not what the manual implies.  This mapping IS the backend, so it is pinned to
# observed output rather than to a guess.
# ---------------------------------------------------------------------------

def _smb():
    return auth.SmbBackend(server="localhost", share="users", timeout=10)


def test_smb_accepts_a_clean_exit():
    result = _smb()._interpret("adminniebas", 0, "")

    assert result.ok
    assert result.identity.username == "adminniebas"


@pytest.mark.parametrize("output", [
    "session setup failed: NT_STATUS_LOGON_FAILURE",          # wrong password
    "tree connect failed: NT_STATUS_WRONG_PASSWORD",
    "session setup failed: NT_STATUS_ACCOUNT_LOCKED_OUT",
])
def test_smb_reports_a_refused_password_as_bad_credentials(output):
    assert _smb()._interpret("adminniebas", 1, output).reason == "bad-credentials"


def test_smb_cannot_tell_a_wrong_password_from_an_unknown_user():
    """Samba returns NT_STATUS_LOGON_FAILURE for both, which is the behaviour we want: a
    login form that distinguishes them enumerates the account list."""
    backend = _smb()
    same = "session setup failed: NT_STATUS_LOGON_FAILURE"

    assert backend._interpret("real", 1, same).reason == "bad-credentials"
    assert backend._interpret("nosuchuser", 1, same).reason == "bad-credentials"


def test_smb_tells_a_permitted_account_from_an_authenticated_one():
    """`valid users = @sambashare` refuses the tree connect after a successful session setup.
    The password WAS right, and saying so saves the user retyping a correct password."""
    result = _smb()._interpret("adminniebas", 1, "tree connect failed: NT_STATUS_ACCESS_DENIED")

    assert result.reason == "not-authorised"
    assert not result.ok


@pytest.mark.parametrize("output, why", [
    ("tree connect failed: NT_STATUS_BAD_NETWORK_NAME", "the share was renamed or removed"),
    ("do_connect: Connection to localhost failed (Error NT_STATUS_CONNECTION_REFUSED)", "smbd is down"),
    ("NT_STATUS_IO_TIMEOUT", "the server stopped answering"),
    ("smbclient: something nobody has seen before", "an unrecognised failure"),
])
def test_smb_reports_an_outage_as_an_outage_not_a_wrong_password(output, why):
    """A renamed share or a stopped service must not present as every account's password
    breaking at once -- which is what it would look like if these mapped to bad-credentials."""
    result = _smb()._interpret("adminniebas", 1, output)

    assert result.reason == "backend-unavailable", why


def test_smb_refuses_an_anonymous_fallback_whatever_the_exit_code():
    """On the DCPT server an anonymous SESSION SETUP succeeds and only the tree connect is
    refused -- so a probe against IPC$ would have admitted anyone.  A session nobody
    authenticated is never a sign-in, even if smbclient exits 0."""
    result = _smb()._interpret("anyone", 0, "Anonymous login successful\nDomain=[SAMBA]")

    assert not result.ok
    assert result.reason == "bad-credentials"


def test_smb_never_puts_the_password_on_the_command_line():
    """/proc/<pid>/cmdline is world-readable, so credentials go in on a pipe."""
    argv = _smb()._argv()

    assert "-A" in argv and "/dev/stdin" in argv
    assert not any("password" in part.lower() for part in argv)


def test_smb_preflight_requires_smbclient(monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _name: None)

    assert "smbclient" in _smb().preflight()


def test_smb_timeout_is_an_outage_not_a_rejection(monkeypatch):
    import subprocess

    monkeypatch.setattr(auth.shutil, "which", lambda _name: "/usr/bin/smbclient")

    def hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="smbclient", timeout=10)

    monkeypatch.setattr(auth.subprocess, "run", hang)

    assert _smb().check_password("adminniebas", "x").reason == "backend-unavailable"


def test_smb_sends_credentials_on_stdin_and_not_in_the_environment(monkeypatch):
    captured = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input", "")
        return Done()

    monkeypatch.setattr(auth.subprocess, "run", fake_run)

    assert _smb().check_password("adminniebas", "s3cret").ok
    assert "password = s3cret" in captured["input"]
    assert not any("s3cret" in part for part in captured["argv"])


def test_smb_refuses_an_empty_password_without_calling_smbclient(monkeypatch):
    """An empty password makes smbclient attempt an ANONYMOUS session, which can succeed."""
    def explode(*_args, **_kwargs):
        raise AssertionError("smbclient must not be run for an empty password")

    monkeypatch.setattr(auth.subprocess, "run", explode)

    assert _smb().check_password("adminniebas", "").reason == "bad-credentials"


# ---------------------------------------------------------------------------
# Concurrent account edits
#
# os.replace makes each WRITE atomic for readers; that is not the same as making
# read-modify-write safe.  Two `pregdos-passwd add` runs interleaving would each write a set
# computed before the other's change, and one account would vanish with the admin told it was
# added.  Rare, and exactly the kind of failure nobody would notice.
# ---------------------------------------------------------------------------

def test_write_uses_a_unique_temp_name_not_a_shared_one(tmp_path):
    """A shared `.tmp` lets two writers interleave into one file and publish the mixture --
    a corrupt password file, which is worse than losing one update."""
    seen = []
    real_mkstemp = auth.tempfile.mkstemp

    def spy(**kwargs):
        fd, name = real_mkstemp(**kwargs)
        seen.append(name)
        return fd, name

    auth.tempfile.mkstemp, original = spy, auth.tempfile.mkstemp
    try:
        auth.write_password_file(tmp_path / "users", {"a": auth.hash_password("x")})
        auth.write_password_file(tmp_path / "users", {"b": auth.hash_password("x")})
    finally:
        auth.tempfile.mkstemp = original

    assert len(set(seen)) == 2, "both writes used the same temp path"
    assert not list(tmp_path.glob("*.tmp")), "a temp file was left behind"


def test_write_survives_a_leftover_temp_from_a_crashed_writer(tmp_path):
    path = tmp_path / "users"
    (tmp_path / "users.tmp").write_text("junk from a previous crash")

    auth.write_password_file(path, {"alice": auth.hash_password("x")})

    assert list(auth.read_password_file(path)) == ["alice"]


def test_the_lock_is_a_sibling_file_not_the_password_file(tmp_path):
    """os.replace swaps the password file's inode out from under any lock held on it, so the
    two processes would hold locks on different inodes and both proceed."""
    path = tmp_path / "users"
    with auth.password_file_lock(path):
        pass

    assert (tmp_path / "users.lock").exists()


def test_concurrent_adds_do_not_lose_an_account(tmp_path):
    """The real thing, in two processes -- flock is per-process, so threads would prove nothing.

    Each child adds a different account with a deliberate pause between load and write, which
    is the window the lock has to close.  Without it, one account is lost.
    """
    import subprocess
    import sys
    import textwrap

    path = tmp_path / "users"
    auth.write_password_file(path, {"seed": auth.hash_password("x")})

    child = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(pathlib.Path(__file__).resolve().parent.parent)!r})
        from pathlib import Path
        from pregdos import auth
        path = Path({str(path)!r})
        with auth.password_file_lock(path):
            users = auth.read_password_file(path)
            time.sleep(0.4)                      # the read-modify-write window
            users[sys.argv[1]] = auth.hash_password("pw")
            auth.write_password_file(path, users)
    """)
    script = tmp_path / "child.py"
    script.write_text(child)

    running = [subprocess.Popen([sys.executable, str(script), name])
               for name in ("alice", "bob")]
    for process in running:
        assert process.wait(timeout=60) == 0

    assert sorted(auth.read_password_file(path)) == ["alice", "bob", "seed"]


# ---------------------------------------------------------------------------
# Record forging
#
# Both credential formats PregDos writes are line-oriented, so a newline inside a value does
# not escape a quoting layer -- it creates a new record.  For smbclient's `-A` input a later
# `username =` overrides the earlier one, which would let someone holding any valid account
# authenticate as themselves while PregDos issued the session under the name they typed.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("username, password", [
    ("alice", "x\nusername = adminniebas\npassword = theirs"),   # the injection itself
    ("alice\nusername = root", "x"),
    ("alice", "x\rusername = root"),
    ("alice", "x\x00truncate-me"),                               # NUL truncates a C string
])
def test_smb_refuses_a_credential_that_could_forge_a_record(username, password, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("smbclient must never be handed a forged credential file")

    monkeypatch.setattr(auth.subprocess, "run", explode)

    result = auth.SmbBackend("localhost", "users").check_password(username, password)

    assert result.reason == "bad-credentials"


def test_login_rejects_a_forged_credential_before_any_backend_runs(write_config, users_file):
    """Checked in login() because that is what decides the name reaching the session cookie
    and every audit line -- the two must never disagree about who signed in."""
    cfg = _cfg(write_config, users_file)

    result = auth.login("alice", "correct horse\nusername = root", cfg)

    assert result.reason == "bad-credentials"
    assert not result.ok


def test_a_legitimate_password_with_punctuation_still_works(write_config, tmp_path):
    """The check must not reject ordinary strong passwords."""
    path = tmp_path / "users"
    tricky = "a b:c=d$e#f\\g'h\"i"
    auth.write_password_file(path, {"alice": auth.hash_password(tricky)})
    write_config('[server]\nhost = "127.0.0.1"\n\n'
                 f'[auth]\nmethod = "file"\npassword_file = "{path}"\n')

    assert auth.login("alice", tricky, config.load()).ok


@pytest.mark.parametrize("username", ["a:b", "a\nb", "a\x00b", " alice", ""])
def test_passwd_refuses_a_username_the_file_format_cannot_hold(username, tmp_path,
                                                               write_config, monkeypatch, capsys):
    from pregdos import passwd

    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    target = tmp_path / "users"
    path = write_config(
        f'[server]\nhost = "127.0.0.1"\n[auth]\nmethod = "file"\npassword_file = "{target}"\n')

    with pytest.raises(SystemExit):
        passwd.main(["--config", str(path), "add", username])

    assert not target.exists(), "a malformed username reached the password file"


# ---------------------------------------------------------------------------
# Audit log integrity
#
# A submitted username is recorded even when the sign-in is DENIED -- which is exactly the
# case where an attacker controls it.  Without escaping, one request appends lines that look
# like genuine audit records, in the log that is this system's whole accountability story.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hostile, why", [
    ("alice\naudit action=login.ok user=root", "a forged second record"),
    ("alice\rDec 11 audit action=study.delete", "CR does it too"),
    ("alice\x1b[2J", "an escape sequence aimed at journalctl's terminal"),
    ("alice\u202Ecod.txt", "a bidi override that reorders what a reader sees"),
    ("alice\x00root", "NUL"),
])
def test_audit_values_cannot_forge_a_second_line(hostile, why):
    from pregdos import audit

    rendered = audit._one_line(hostile)

    assert "\n" not in rendered and "\r" not in rendered, why
    assert all(character.isprintable() for character in rendered), why


def test_audit_keeps_printable_non_ascii():
    """Study names carry patient names; mangling 'ø' would make the log worse at its job."""
    from pregdos import audit

    assert audit._one_line("PAT Ø Jensen-Bræmer") == "PAT Ø Jensen-Bræmer"


def test_audit_escapes_wide_codepoints_unambiguously():
    """U+202E as \\x202e would read as \\x20 followed by '2e' -- a different, plausible string."""
    from pregdos import audit

    assert audit._one_line("\u202E") == "\\u202e"


def test_a_denied_login_cannot_inject_into_the_journal(caplog):
    """The reachable path: auth.login rejects the newline, but the view still logs the RAW
    submitted username, so the escaping has to happen at the sink."""
    import logging

    from pregdos import audit

    with caplog.at_level(logging.INFO, logger="pregdos.audit"):
        audit.event("login.denied", user="alice\naudit action=login.ok user=root",
                    reason="bad-credentials", ip="10.0.0.1")

    assert len(caplog.records) == 1
    assert "\n" not in caplog.records[0].getMessage()


# ---------------------------------------------------------------------------
# Failing closed
#
# verify_password promises never to raise: one corrupt line must lock out one account, not
# take the login page down for everyone with a 500.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stored", [
    "scrypt$1180591620717411303424$8$1$AAAA$BBBB",   # n far beyond any integer the C layer takes
    "scrypt$0$8$1$AAAA$BBBB",                        # n = 0
    "scrypt$-1$8$1$AAAA$BBBB",                       # n negative
    "scrypt$3$8$1$AAAA$BBBB",                        # n not a power of two
    "scrypt$1099511627776$8$1$AAAA$BBBB",            # n in range but past the memory limit
    "scrypt$16384$1180591620717411303424$1$AAAA$BBBB",   # r out of range
    "scrypt$16384$8$1180591620717411303424$AAAA$BBBB",   # p out of range
    "scrypt$16384$8$1$not-base64!!$BBBB",
    "scrypt$16384$8$1$AAAA$",                        # empty key -> dklen 0
])
def test_a_hostile_hash_never_raises_whatever_python_calls_the_error(stored):
    """The exception type varies by Python and OpenSSL build, so this asserts the contract --
    returns False, does not propagate -- rather than which exception was caught."""
    assert auth.verify_password("anything", stored) is False


def test_a_corrupt_entry_is_a_failed_login_not_a_500(write_config, tmp_path):
    path = tmp_path / "users"
    path.write_text("alice:scrypt$1180591620717411303424$8$1$AAAA$BBBB\n")
    path.chmod(0o600)
    write_config('[server]\nhost = "127.0.0.1"\n\n'
                 f'[auth]\nmethod = "file"\npassword_file = "{path}"\n')

    result = auth.login("alice", "anything", config.load())

    assert result.reason == "bad-credentials"


def test_an_unforeseen_backend_error_is_an_outage_not_a_traceback(write_config, users_file, caplog):
    """A backend raising something this module never thought of must not 500 the login page;
    the user is told to try later and the operator gets the traceback in the journal."""
    import logging

    cfg = _cfg(write_config, users_file)

    class Exploding(auth.Backend):
        name = "file"

        def check_password(self, username, password):
            raise RuntimeError("something nobody anticipated")

    original = auth.get_backend
    auth.get_backend = lambda _cfg=None: Exploding()
    try:
        with caplog.at_level(logging.ERROR, logger="pregdos.auth"):
            result = auth.login("alice", "correct horse", cfg)
    finally:
        auth.get_backend = original

    assert result.reason == "backend-unavailable"
    assert not result.ok
    assert any("unexpected" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# Reading the password file can fail after the stat succeeds
#
# preflight() reports only AuthError, so anything else escapes main() as a traceback at
# startup instead of a message naming the file.
# ---------------------------------------------------------------------------

def test_a_password_file_that_is_not_utf8_is_reported_not_raised(tmp_path):
    path = tmp_path / "users"
    path.write_bytes(b"alice:scrypt$16384$8$1$AA$BB\n\xff\xfe not utf-8\n")
    path.chmod(0o600)

    with pytest.raises(auth.AuthError, match="not valid UTF-8"):
        auth.read_password_file(path)


def test_preflight_reports_a_non_utf8_file_instead_of_tracebacking(tmp_path):
    """The path that mattered: a traceback here is a service that will not start, with the
    reason buried in a stack trace rather than named."""
    path = tmp_path / "users"
    path.write_bytes(b"\xff\xfe")
    path.chmod(0o600)

    problem = auth.FileBackend(path).preflight()

    assert problem is not None
    assert "UTF-8" in problem


def test_a_read_that_fails_after_the_stat_is_reported_not_raised(tmp_path):
    """Permissions can change between the two syscalls, and the path can be a directory."""
    path = tmp_path / "users"
    path.mkdir(mode=0o700)          # stats fine, and 0700 passes the mode check

    with pytest.raises(auth.AuthError, match="cannot be read"):
        auth.read_password_file(path)


def test_a_missing_file_still_says_how_to_make_one(tmp_path):
    with pytest.raises(auth.AuthError, match="pregdos-passwd add"):
        auth.read_password_file(tmp_path / "absent")
