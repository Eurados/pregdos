"""The authentication backends and the allowlist.  Issue #103.

No Flask here -- this is the layer that decides whether a password belongs to a username, and
whether that username may use PregDos at all.  The gate that consumes it is test_auth_gate.py.
"""

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


def test_missing_password_file_says_how_to_make_one(tmp_path):
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
