"""Tests for runtime version discovery (TOPAS, Geant4) and the #49 version floor."""

import re
import subprocess

import pytest

from pregdos import versions


def _clear_caches():
    # A test may have monkeypatched these with a plain function, which has no cache.
    for fn in (versions.topas_version, versions.geant4_version):
        if hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for var in ("TOPAS_VERSION", "GEANT4_VERSION", "TOPAS_BIN", "TOPAS_G4_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    _clear_caches()
    monkeypatch.setattr(versions, "MARKER_DIR", versions.Path("/nonexistent"))
    yield
    _clear_caches()


# --- parse_version ---

@pytest.mark.parametrize("text,expected", [
    ("4.2.p3", (4, 2, 3)),      # OpenTOPAS patch releases use a `p` prefix
    ("3.9", (3, 9)),
    ("11.3.2", (11, 3, 2)),
    ("Version 4.2.p3", (4, 2, 3)),
])
def test_parse_version(text, expected):
    assert versions.parse_version(text) == expected


def test_parse_version_rejects_junk():
    assert versions.parse_version("") is None
    assert versions.parse_version("no digits here") is None


# --- topas_version ---

def test_topas_version_from_binary(monkeypatch):
    monkeypatch.setattr(versions.shutil, "which", lambda n: "/usr/bin/topas")
    monkeypatch.setattr(versions.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "4.2.p3\n", ""))
    assert versions.topas_version() == "4.2.p3"


def test_topas_version_prefers_env(monkeypatch):
    """The Docker image records what it installed; that beats asking the binary."""
    monkeypatch.setenv("TOPAS_VERSION", "4.2.3-docker")
    monkeypatch.setattr(versions.shutil, "which", lambda n: None)
    assert versions.topas_version() == "4.2.3-docker"


def test_topas_version_unknown_when_absent(monkeypatch):
    monkeypatch.setattr(versions.shutil, "which", lambda n: None)
    assert versions.topas_version() == versions.UNKNOWN


def test_topas_version_survives_a_broken_binary(monkeypatch):
    monkeypatch.setattr(versions.shutil, "which", lambda n: "/usr/bin/topas")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("topas", 30)

    monkeypatch.setattr(versions.subprocess, "run", boom)
    assert versions.topas_version() == versions.UNKNOWN     # must not raise


# --- geant4_version ---

def test_geant4_version_from_linked_library_dir(monkeypatch, tmp_path):
    (tmp_path / "Geant4-11.3.2").mkdir()
    monkeypatch.setattr(versions, "_linked_library_dirs", lambda: iter([tmp_path]))
    monkeypatch.setattr(versions.shutil, "which", lambda n: None)  # no geant4-config
    assert versions.geant4_version() == "11.3.2"


def test_geant4_version_prefers_geant4_config(monkeypatch):
    monkeypatch.setattr(versions.shutil, "which", lambda n: "/usr/bin/geant4-config")
    monkeypatch.setattr(versions.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "11.1.3\n", ""))
    assert versions.geant4_version() == "11.1.3"


def test_geant4_version_unknown_when_nothing_found(monkeypatch):
    monkeypatch.setattr(versions.shutil, "which", lambda n: None)
    monkeypatch.setattr(versions, "_linked_library_dirs", lambda: iter([]))
    assert versions.geant4_version() == versions.UNKNOWN


# --- topas_warning: the #49 version floor ---

def _with_version(monkeypatch, reported):
    _clear_caches()
    monkeypatch.setattr(versions, "topas_version", lambda: reported)


def test_supported_version_has_no_warning(monkeypatch):
    _with_version(monkeypatch, "4.2.p3")
    assert versions.topas_warning() is None


def test_version_3_9_is_flagged_as_ambiguous(monkeypatch):
    """OpenTOPAS 4.0.0 reports itself as 3.9, so a "3.9" build cannot be trusted either way."""
    _with_version(monkeypatch, "3.9")
    warning = versions.topas_warning()
    assert "4.0.0" in warning and "misreports" in warning and "#49" in warning


def test_older_opentopas_is_flagged(monkeypatch):
    _with_version(monkeypatch, "4.1.p0")
    assert "#49" in versions.topas_warning()


def test_newer_opentopas_is_fine(monkeypatch):
    _with_version(monkeypatch, "4.3.p1")
    assert versions.topas_warning() is None


def test_missing_topas_is_flagged(monkeypatch):
    _with_version(monkeypatch, versions.UNKNOWN)
    assert "not found on PATH" in versions.topas_warning()


def test_uninterpretable_version_is_flagged(monkeypatch):
    _with_version(monkeypatch, "banana")
    assert "Could not interpret" in versions.topas_warning()


# --- TOPAS_G4_DATA_DIR pre-flight ---

def test_g4_data_dir_unset_is_fine():
    assert versions.g4_data_dir_problem() is None


def test_g4_data_dir_present_is_fine(monkeypatch, tmp_path):
    monkeypatch.setenv("TOPAS_G4_DATA_DIR", str(tmp_path))
    assert versions.g4_data_dir_problem() is None


def test_g4_data_dir_missing_is_flagged(monkeypatch, tmp_path):
    """The real failure: a stale env var pointing at a path that does not exist made every
    TOPAS run abort with `ENSDFSTATE.dat is not found` seconds after submission."""
    monkeypatch.setenv("TOPAS_G4_DATA_DIR", str(tmp_path / "G4Data"))
    problem = versions.g4_data_dir_problem()
    assert "does not exist" in problem and "abort" in problem


# --- submit_blocker: the #49 pre-flight guard ---

def test_submit_blocker_none_for_supported_topas(monkeypatch):
    _with_version(monkeypatch, "4.2.p3")
    assert versions.submit_blocker() is None


def test_submit_blocker_blocks_unsupported_topas(monkeypatch):
    _with_version(monkeypatch, "3.9")
    assert "#49" in versions.submit_blocker()


def test_submit_blocker_does_not_block_unknown_topas(monkeypatch):
    """SLURM runs TOPAS on a compute node, so the webserver not finding it is not a reason
    to refuse -- the version is simply unknown here."""
    _with_version(monkeypatch, versions.UNKNOWN)
    assert versions.submit_blocker() is None


def test_submit_blocker_blocks_missing_g4_data(monkeypatch, tmp_path):
    _with_version(monkeypatch, "4.2.p3")
    monkeypatch.setenv("TOPAS_G4_DATA_DIR", str(tmp_path / "gone"))
    assert "does not exist" in versions.submit_blocker()


# --- the About page ---

def test_about_page_reports_versions(monkeypatch):
    from pregdos.webserver import app
    _with_version(monkeypatch, "4.2.p3")
    monkeypatch.setattr(versions, "geant4_version", lambda: "11.3.2")
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get("/about").data.decode()
    assert "4.2.p3" in body and "11.3.2" in body
    assert "unsupported" not in body


def test_about_page_warns_about_unsupported_topas(monkeypatch):
    from pregdos.webserver import app
    _with_version(monkeypatch, "3.9")
    monkeypatch.setattr(versions, "geant4_version", lambda: "11.1.3")
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get("/about").data.decode()
    assert "unsupported" in body and "#49" in body


def test_about_page_omits_absent_funding_logos(monkeypatch, tmp_path):
    """A checkout without the PNGs must show text, not two broken-image icons."""
    from pregdos import webserver
    from pregdos.webserver import app
    _with_version(monkeypatch, "4.2.p3")
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get("/about").data.decode()
    assert "funding-logos" not in body
    assert "PIANOFORTE" in body                       # the acknowledgement text remains
    assert webserver._funding_logos() == []


def test_about_page_shows_funding_logos_when_present(monkeypatch, tmp_path):
    from pregdos.webserver import app
    _with_version(monkeypatch, "4.2.p3")
    img = tmp_path / "img"
    img.mkdir()
    (img / "eu-flag.png").write_bytes(b"\x89PNG")
    (img / "pianoforte-logo.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get("/about").data.decode()
    assert "funding-logos" in body
    assert "img/eu-flag.png" in body and "img/pianoforte-logo.png" in body
    assert 'alt="Funded by the European Union"' in body


def _with_logos(monkeypatch, tmp_path):
    from pregdos.webserver import app
    img = tmp_path / "img"
    img.mkdir()
    (img / "eu-flag.png").write_bytes(b"\x89PNG")
    (img / "pianoforte-logo.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    app.config["TESTING"] = True
    return app


@pytest.mark.parametrize("path", ["/", "/about", "/studies", "/upload"])
def test_funding_strip_is_on_every_page(monkeypatch, tmp_path, path):
    """The acknowledgement lives in base.html's footer, so it cannot be forgotten on a
    page added later."""
    app = _with_logos(monkeypatch, tmp_path)
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "studies")
    with app.test_client() as c:
        body = c.get(path).data.decode()
    assert "funding-logos compact" in body
    assert "pianoforte-partnership.eu" in body        # hyperlinked
    assert 'class="funding-logo plated"' in body      # only the transparent logo is plated


def test_pages_without_logos_render_no_images(monkeypatch, tmp_path):
    from pregdos.webserver import app
    monkeypatch.setattr(app, "static_folder", str(tmp_path))
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get("/").data.decode()
    assert "funding-logos" not in body
    assert "PIANOFORTE" in body                       # the text acknowledgement remains


@pytest.mark.parametrize("path", ["/", "/about"])
def test_version_is_in_the_nav_bar(monkeypatch, tmp_path, path):
    """Shown on every page so it lands in screenshots and bug reports."""
    from pregdos import webserver
    from pregdos.webserver import app
    monkeypatch.setattr(webserver.importlib.metadata, "version",
                        lambda name: "0.3.0.post33+g1a87f860.d20260709")
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get(path).data.decode()
    # short form shown, full string (with commit hash) on the tooltip
    assert 'class="tab-nav-version" title="0.3.0.post33+g1a87f860.d20260709">v0.3.0.post33<' in body


def _active_nav_items(html):
    """Labels of the nav entries currently carrying the active underline."""
    return re.findall(r'class="tab-nav-(?:brand|tab) active"[^>]*>\s*([^<\n]+?)\s*[<\n]', html)


@pytest.mark.parametrize("path,expected", [
    ("/", "PregDos"),                 # the dashboard highlights the brand, not a neighbour
    ("/upload", "New simulation"),
    ("/studies", "Tasks"),
    ("/about", "About"),
])
def test_exactly_one_nav_item_is_active(monkeypatch, tmp_path, path, expected):
    from pregdos.webserver import app
    app.config["TESTING"] = True
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "studies")
    with app.test_client() as c:
        html = c.get(path).data.decode()
    assert _active_nav_items(html) == [expected]


def test_footer_no_longer_repeats_the_version(monkeypatch, tmp_path):
    """The short version is in the nav and the full one is on About; the footer stops there."""
    app = _with_logos(monkeypatch, tmp_path)
    with app.test_client() as c:
        footer = c.get("/").data.decode().split("<footer")[1]
    assert "research use only" not in footer
    assert "post" not in footer                       # no version string in the footer
    assert "pianoforte-partnership.eu" in footer      # the acknowledgement stays


def test_version_falls_back_when_package_metadata_is_absent(monkeypatch):
    from pregdos import webserver
    from pregdos.webserver import app

    def boom(name):
        raise webserver.importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(webserver.importlib.metadata, "version", boom)
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get("/").data.decode()
    assert ">vdev<" in body


def test_about_page_warns_about_missing_g4_data(monkeypatch, tmp_path):
    from pregdos.webserver import app
    _with_version(monkeypatch, "4.2.p3")
    monkeypatch.setattr(versions, "geant4_version", lambda: "11.3.2")
    monkeypatch.setenv("TOPAS_G4_DATA_DIR", str(tmp_path / "gone"))
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get("/about").data.decode()
    assert "missing" in body and "restart it" in body


# --- dicomexport minimum (dicomexport #66: the mirrored beam) ---

def test_dicomexport_at_the_minimum_is_accepted(monkeypatch):
    monkeypatch.setattr(versions, "dicomexport_version", lambda: "1.4.4")
    assert versions.dicomexport_warning() is None


def test_dicomexport_below_the_minimum_is_rejected(monkeypatch):
    """1.4.3 mirrors every field 180 deg, so the dose is wrong without looking wrong."""
    monkeypatch.setattr(versions, "dicomexport_version", lambda: "1.4.3")
    warning = versions.dicomexport_warning()
    assert warning is not None and "1.4.3" in warning and "mirror" in warning


def test_dicomexport_of_unknown_version_is_rejected(monkeypatch):
    """Unlike TOPAS, dicomexport always runs on this host -- so we can insist on knowing it."""
    monkeypatch.setattr(versions, "dicomexport_version", lambda: versions.UNKNOWN)
    assert versions.dicomexport_warning() is not None


def test_an_old_dicomexport_blocks_submission(monkeypatch):
    _with_version(monkeypatch, "4.2.p3")               # TOPAS itself is fine
    monkeypatch.delenv("TOPAS_G4_DATA_DIR", raising=False)
    monkeypatch.setattr(versions, "dicomexport_version", lambda: "1.4.3")
    blocker = versions.submit_blocker()
    assert blocker is not None and "dicomexport" in blocker


def test_a_current_dicomexport_does_not_block_submission(monkeypatch):
    _with_version(monkeypatch, "4.2.p3")
    monkeypatch.delenv("TOPAS_G4_DATA_DIR", raising=False)
    monkeypatch.setattr(versions, "dicomexport_version", lambda: "1.4.4")
    assert versions.submit_blocker() is None
