"""The login gate: what it blocks, what it must not block, and how it expires.  Issue #103.

Everything here runs against the module-level `app`, the way tests/test_webserver.py does.
The `auth_client` fixture restores it afterwards, so the 90-odd unauthenticated calls in that
file keep seeing the default: [auth] off, no gate, no CSRF token.
"""

import time

import pytest

from pregdos import auth, config, studies, webserver
from pregdos.webserver import app


@pytest.fixture
def client(tmp_path):
    """PregDos as it ships: [auth] off, no gate.  Same shape as test_webserver.py's."""
    app.config["TESTING"] = True
    app.config["WORK_DIR"] = str(tmp_path)
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth_client(tmp_path, monkeypatch, write_config):
    """PregDos with `[auth] method = "file"` and one account, `alice`, password `secret`."""
    users = tmp_path / "users"
    auth.write_password_file(users, {"alice": auth.hash_password("secret")})
    write_config(
        '[server]\nhost = "127.0.0.1"\n\n'
        f'[auth]\nmethod = "file"\npassword_file = "{users}"\n'
    )
    original_key = app.secret_key
    app.secret_key = "deterministic-key-for-tests"
    webserver._apply_config()
    # The test client speaks http://localhost, and werkzeug honours the Secure flag -- so a
    # cookie set with it would never come back and every test here would fail as "not signed
    # in".  Secure is asserted on its own below, against _apply_config's real output.
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["TESTING"] = True
    app.config["WORK_DIR"] = str(tmp_path)

    with app.test_client() as client:
        yield client

    app.secret_key = original_key
    config.reset_cache()
    webserver._apply_config()


def sign_in(client, username="alice", password="secret"):
    page = client.get("/login")
    token = page.data.decode().split('name="csrf_token" value="')[1].split('"')[0]
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def _csrf(client):
    """The current session's token, read out of a rendered form."""
    page = client.get("/studies")
    return page.data.decode().split('name="csrf_token" value="')[1].split('"')[0]


# ---------------------------------------------------------------------------
# What the gate blocks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/upload", "/studies", "/about", "/jobs"])
def test_a_page_is_a_redirect_to_the_login_form(auth_client, path):
    response = auth_client.get(path)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_a_poll_is_a_401_with_json_not_a_redirect(auth_client):
    """A redirect would be followed by fetch(), fail to parse as JSON, and land in the
    poller's catch -- which retries forever and tells nobody."""
    response = auth_client.get("/studies/fragment", headers={"Accept": "application/json"})

    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthenticated"
    assert response.headers["Cache-Control"] == "no-store"


def test_a_download_redirects_before_it_streams_a_single_byte(auth_client, tmp_path):
    studies.create_study(tmp_path, "mystudy")
    response = auth_client.get("/studies/mystudy/run_1/archive")

    assert response.status_code == 302
    assert not response.data.startswith(b"PK")
    assert "Content-Disposition" not in response.headers


def test_a_destructive_post_is_blocked_and_changes_nothing(auth_client, tmp_path):
    _, study_dir = studies.create_study(tmp_path, "mystudy")

    response = auth_client.post("/studies/mystudy/delete")

    assert response.status_code == 302
    assert study_dir.is_dir(), "the study was deleted by an unauthenticated request"


def test_an_unmatched_url_is_still_gated(auth_client):
    """request.endpoint is None for a URL that matched no rule, so a path-prefix exemption
    would have turned a 404 into a way to probe the server."""
    assert auth_client.get("/no/such/page").status_code in (302, 404)


# ---------------------------------------------------------------------------
# What the gate must NOT block
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/favicon.ico", "/static/styles.css"])
def test_the_login_page_can_load_its_own_assets(auth_client, path):
    """Gating these renders the login page unstyled and iconless, from behind its own
    redirect loop -- base.html requests both on every page."""
    assert auth_client.get(path).status_code == 200


def test_the_login_page_itself_is_reachable(auth_client):
    response = auth_client.get("/login")

    assert response.status_code == 200
    assert b'name="password"' in response.data


def test_the_login_page_hides_the_nav_whose_every_tab_would_bounce_back(auth_client):
    assert b"tab-nav-tab" not in auth_client.get("/login").data


# ---------------------------------------------------------------------------
# Signing in and out
# ---------------------------------------------------------------------------

def test_signing_in_with_the_right_password_reaches_the_dashboard(auth_client):
    assert sign_in(auth_client).status_code == 302
    assert auth_client.get("/").status_code == 200


def test_the_nav_shows_who_is_signed_in_and_offers_a_way_out(auth_client):
    sign_in(auth_client)

    body = auth_client.get("/").data.decode()

    assert "alice" in body
    assert "Sign out" in body


@pytest.mark.parametrize("username, password", [("alice", "wrong"), ("mallory", "secret")])
def test_a_bad_password_does_not_sign_anyone_in(auth_client, username, password, monkeypatch):
    monkeypatch.setattr(webserver.time, "sleep", lambda _seconds: None)

    sign_in(auth_client, username, password)

    assert auth_client.get("/").status_code == 302


def test_signing_out_ends_the_session(auth_client):
    sign_in(auth_client)
    auth_client.post("/logout", data={"csrf_token": _csrf(auth_client)})

    assert auth_client.get("/").status_code == 302


def test_logout_refuses_a_get_because_browsers_prefetch_links(auth_client):
    sign_in(auth_client)

    assert auth_client.get("/logout").status_code == 405


def test_sign_in_returns_to_the_page_that_was_asked_for(auth_client):
    redirected = auth_client.get("/about")
    assert redirected.headers["Location"] == "/login?next=/about"

    page = auth_client.get("/login", query_string={"next": "/about"})
    token = page.data.decode().split('name="csrf_token" value="')[1].split('"')[0]
    response = auth_client.post(
        "/login", query_string={"next": "/about"},
        data={"username": "alice", "password": "secret", "csrf_token": token},
    )

    assert response.headers["Location"] == "/about"


@pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example", "https://evil.example"])
def test_next_cannot_be_turned_into_an_open_redirect(auth_client, target):
    page = auth_client.get("/login", query_string={"next": target})
    token = page.data.decode().split('name="csrf_token" value="')[1].split('"')[0]

    response = auth_client.post(
        "/login", query_string={"next": target},
        data={"username": "alice", "password": "secret", "csrf_token": token},
    )

    assert response.headers["Location"] == "/"


def test_signing_in_rotates_the_session(auth_client):
    """No fixation, and no CSRF token from before the sign-in survives it."""
    auth_client.get("/login")
    with auth_client.session_transaction() as before:
        before["planted"] = "should not survive"
        anonymous_token = before.get("csrf")

    sign_in(auth_client)

    with auth_client.session_transaction() as after:
        assert "planted" not in after
        assert after["csrf"] != anonymous_token


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

def test_an_idle_session_expires(auth_client):
    sign_in(auth_client)
    with auth_client.session_transaction() as sess:
        sess["s"] = int(time.time()) - 61 * 60        # idle_minutes defaults to 60

    assert auth_client.get("/").status_code == 302


def test_activity_within_the_idle_limit_is_not_expired_by_the_anchor_s_own_lag(auth_client):
    """`s` is only refreshed every _IDLE_ANCHOR_RESOLUTION seconds, so it can lag real
    activity by that much.  Testing it against a bare idle_limit would sign someone out up to
    a minute early, mid-task, for a timeout the config says is an hour -- so the comparison
    carries one anchor period of grace."""
    sign_in(auth_client)
    with auth_client.session_transaction() as sess:
        # Someone active 30 s ago, whose anchor was last written 59 min 40 s ago because the
        # refresh only fires once a minute.  idle_minutes defaults to 60.
        sess["s"] = int(time.time()) - (60 * 60 - 20)

    assert auth_client.get("/").status_code == 200


def test_a_session_expires_on_the_absolute_cap_however_busy_it_was(auth_client):
    sign_in(auth_client)
    with auth_client.session_transaction() as sess:
        sess["t"] = int(time.time()) - 13 * 3600      # session_hours defaults to 12
        sess["s"] = int(time.time())                  # active this very second

    assert auth_client.get("/").status_code == 302


def test_a_poll_does_not_count_as_activity(auth_client):
    """Otherwise a task page left open overnight keeps its own session alive forever, and the
    idle timeout protects nothing."""
    sign_in(auth_client)
    stale = int(time.time()) - 59 * 60
    with auth_client.session_transaction() as sess:
        sess["s"] = stale

    auth_client.get("/studies/fragment", headers={"Accept": "application/json"})

    with auth_client.session_transaction() as sess:
        assert sess["s"] == stale


def test_looking_at_a_page_does_count_as_activity(auth_client):
    sign_in(auth_client)
    stale = int(time.time()) - 59 * 60
    with auth_client.session_transaction() as sess:
        sess["s"] = stale

    auth_client.get("/studies")

    with auth_client.session_transaction() as sess:
        assert sess["s"] > stale


def test_an_expired_session_is_cleared_rather_than_left_to_be_rejected_forever(auth_client):
    sign_in(auth_client)
    with auth_client.session_transaction() as sess:
        sess["s"] = int(time.time()) - 61 * 60

    auth_client.get("/")

    with auth_client.session_transaction() as sess:
        assert "u" not in sess


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def test_a_post_without_a_token_is_refused_and_changes_nothing(auth_client, tmp_path):
    _, study_dir = studies.create_study(tmp_path, "mystudy")
    sign_in(auth_client)

    response = auth_client.post("/studies/mystudy/delete")

    assert response.status_code == 403
    assert study_dir.is_dir()


def test_a_post_with_this_session_s_token_succeeds(auth_client, tmp_path):
    _, study_dir = studies.create_study(tmp_path, "mystudy")
    sign_in(auth_client)

    response = auth_client.post(
        "/studies/mystudy/delete", data={"csrf_token": _csrf(auth_client)}
    )

    assert response.status_code == 302
    assert not study_dir.exists()


def test_a_token_from_another_session_is_refused(auth_client, tmp_path):
    _, study_dir = studies.create_study(tmp_path, "mystudy")
    sign_in(auth_client)

    response = auth_client.post(
        "/studies/mystudy/delete", data={"csrf_token": "a-token-from-somewhere-else"}
    )

    assert response.status_code == 403
    assert study_dir.is_dir()


def test_the_token_in_the_polled_fragment_matches_the_one_in_the_page(auth_client, tmp_path):
    """`studies_fragment` re-renders the delete form every 5 s and the client swaps it in.  A
    per-request token would be stale the moment that happened, so every delete after the
    first poll would 403.  This is why the token is per session."""
    studies.create_study(tmp_path, "mystudy")
    sign_in(auth_client)

    page = auth_client.get("/studies").data.decode()
    fragment = auth_client.get(
        "/studies/fragment", headers={"Accept": "application/json"}
    ).get_json()["html"]

    token = page.split('name="csrf_token" value="')[1].split('"')[0]
    assert f'value="{token}"' in fragment


# ---------------------------------------------------------------------------
# Off by default -- the invariant the rest of the suite rests on
# ---------------------------------------------------------------------------

def test_with_auth_off_a_post_needs_no_token(client, tmp_path):
    """With no login there is nothing to forge: anyone who can reach the port can open the
    page and click the button.  Requiring a token would be ceremony, and would break every
    existing deployment."""
    _, study_dir = studies.create_study(tmp_path, "mystudy")

    response = client.post("/studies/mystudy/delete")

    assert response.status_code == 302
    assert not study_dir.exists()


def test_with_auth_off_the_forms_carry_no_token_at_all(client):
    assert b"csrf_token" not in client.get("/upload").data


def test_with_auth_off_the_login_page_is_not_a_dead_end(client):
    response = client.get("/login")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_with_auth_off_the_nav_has_no_account_slot(client):
    assert b"tab-nav-account" not in client.get("/").data


# ---------------------------------------------------------------------------
# Cookie flags, asserted against what _apply_config actually produces
# ---------------------------------------------------------------------------

def test_the_session_cookie_is_hardened_once_it_confers_authority(auth_client):
    webserver._apply_config()      # undo the fixture's Secure=False, see its comment

    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


# ---------------------------------------------------------------------------
# Startup refusals: better a server that will not start than one that looks protected
# ---------------------------------------------------------------------------

def _run_main(mocker, argv):
    """Call main() with app.run patched out, so nothing binds a port."""
    run = mocker.patch.object(webserver.app, "run")
    try:
        webserver.main(argv)
    finally:
        config.set_config_path(None)
        config.reset_cache()
        webserver._apply_config()
    return run


def test_host_flag_cannot_smuggle_a_login_onto_a_public_interface(tmp_path, mocker, write_config):
    """The config said 127.0.0.1 and passed validation; --host 0.0.0.0 never goes near it."""
    users = tmp_path / "users"
    auth.write_password_file(users, {"alice": auth.hash_password("secret")})
    path = write_config(
        '[server]\nhost = "127.0.0.1"\n\n'
        f'[auth]\nmethod = "file"\npassword_file = "{users}"\n'
    )

    with pytest.raises(SystemExit):
        _run_main(mocker, ["--config", str(path), "--host", "0.0.0.0"])


def test_startup_refuses_a_missing_password_file(tmp_path, mocker, write_config):
    """Otherwise the server comes up and rejects everyone, with nothing in the journal."""
    path = write_config(
        '[server]\nhost = "127.0.0.1"\n\n'
        f'[auth]\nmethod = "file"\npassword_file = "{tmp_path / "absent"}"\n'
    )

    with pytest.raises(SystemExit):
        _run_main(mocker, ["--config", str(path)])


def test_startup_succeeds_once_the_password_file_is_there(tmp_path, mocker, write_config):
    users = tmp_path / "users"
    auth.write_password_file(users, {"alice": auth.hash_password("secret")})
    path = write_config(
        '[server]\nhost = "127.0.0.1"\n\n'
        f'[auth]\nmethod = "file"\npassword_file = "{users}"\n'
    )

    run = _run_main(mocker, ["--config", str(path)])

    assert run.call_args.kwargs["host"] == "127.0.0.1"


def test_secure_is_dropped_only_where_a_proxy_terminates_tls_in_front(write_config):
    write_config('[auth]\nmethod = "file"\nallow_insecure_http = true\n')
    try:
        webserver._apply_config()
        assert app.config["SESSION_COOKIE_SECURE"] is False
    finally:
        config.reset_cache()
        webserver._apply_config()


# ---------------------------------------------------------------------------
# "Which password?" -- the first support question at any site with more than one
# ---------------------------------------------------------------------------

def test_the_sign_in_form_says_which_password_is_wanted(auth_client):
    body = auth_client.get("/login").data.decode()

    assert "specific to PregDos" in body


def test_a_site_can_word_the_hint_itself(tmp_path, write_config):
    """The wording that actually stops the question names the site's own store, and may not
    be in English -- so the config wins over the backend's default."""
    users = tmp_path / "users"
    auth.write_password_file(users, {"alice": auth.hash_password("secret")})
    write_config(
        '[server]\nhost = "127.0.0.1"\n\n[auth]\nmethod = "file"\n'
        f'password_file = "{users}"\n'
        'login_hint = "Brug dit Samba-kodeord"\n'
    )
    try:
        webserver._apply_config()
        app.config["SESSION_COOKIE_SECURE"] = False
        app.config["WORK_DIR"] = str(tmp_path)
        with app.test_client() as client:
            body = client.get("/login").data.decode()
        assert "Brug dit Samba-kodeord" in body
        assert "specific to PregDos" not in body
    finally:
        config.reset_cache()
        webserver._apply_config()


def test_the_hint_is_escaped_not_rendered_as_markup(tmp_path, write_config):
    users = tmp_path / "users"
    auth.write_password_file(users, {"alice": auth.hash_password("secret")})
    write_config(
        '[server]\nhost = "127.0.0.1"\n\n[auth]\nmethod = "file"\n'
        f'password_file = "{users}"\n'
        'login_hint = "<script>alert(1)</script>"\n'
    )
    try:
        webserver._apply_config()
        app.config["SESSION_COOKIE_SECURE"] = False
        app.config["WORK_DIR"] = str(tmp_path)
        with app.test_client() as client:
            body = client.get("/login").data.decode()
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body
    finally:
        config.reset_cache()
        webserver._apply_config()
