"""Tests for the offline wheelhouse builder (#98).

The build itself needs a network and several minutes, so it runs in its own workflow. What is
tested here is the metadata surgery that decides whether the artifact installs at all -- a
regression in it would otherwise surface only on a tag, in the one job that takes longest to
fail.
"""

import base64
import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "packaging" / "build_wheelhouse.py"
VERIFIER = REPO_ROOT / "packaging" / "verify_offline_install.py"


def _load_builder():
    """packaging/ is a script directory, not an importable package."""
    spec = importlib.util.spec_from_file_location("build_wheelhouse", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_offline_install", VERIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()
verifier = _load_verifier()


METADATA = """\
Metadata-Version: 2.1
Name: pregdos
Version: 9.9.9
Requires-Dist: pydicom>=2.3.1
Requires-Dist: numpy
Requires-Dist: dicomexport @ git+https://github.com/nbassler/dicomexport@v1.5.0
Requires-Dist: ruff>=0.15.7; extra == "dev"
"""


@pytest.fixture
def wheel(tmp_path):
    """A minimal but structurally real wheel, with a RECORD that matches its METADATA."""
    path = tmp_path / "pregdos-9.9.9-py3-none-any.whl"
    metadata_name = "pregdos-9.9.9.dist-info/METADATA"
    record_name = "pregdos-9.9.9.dist-info/RECORD"
    payload = METADATA.encode()
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
    record = f"pregdos/__init__.py,sha256=abc,3\n{metadata_name},sha256={digest},{len(payload)}\n{record_name},,\n"

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pregdos/__init__.py", "x=1")
        zf.writestr(metadata_name, METADATA)
        zf.writestr(record_name, record)
    return path


def _requires(wheel_path):
    return builder.wheel_metadata(wheel_path).get_all("Requires-Dist")


# --- the dependency set fed to pip download ---

def test_runtime_requirements_drops_extras_and_direct_urls(wheel):
    """Extras are not installed on a clinical node, and a URL cannot come from an index."""
    assert builder.runtime_requirements(wheel) == ["pydicom>=2.3.1", "numpy"]


# --- the repin that makes the artifact installable offline ---

def test_repin_replaces_the_git_url_with_a_plain_pin(wheel):
    builder.repin_direct_url(wheel, "dicomexport", "1.5.0")

    requires = _requires(wheel)
    assert "dicomexport==1.5.0" in requires
    assert not any("git+https" in r for r in requires)


def test_repin_leaves_every_other_requirement_alone(wheel):
    builder.repin_direct_url(wheel, "dicomexport", "1.5.0")

    requires = _requires(wheel)
    assert "pydicom>=2.3.1" in requires
    assert "numpy" in requires
    assert 'ruff>=0.15.7; extra == "dev"' in requires


def test_repin_keeps_the_wheel_readable_and_its_payload_intact(wheel):
    builder.repin_direct_url(wheel, "dicomexport", "1.5.0")

    with zipfile.ZipFile(wheel) as zf:
        assert zf.testzip() is None
        assert zf.read("pregdos/__init__.py") == b"x=1"


def test_repin_updates_the_record_hash(wheel):
    """A stale RECORD entry is a wheel that tools are entitled to reject."""
    builder.repin_direct_url(wheel, "dicomexport", "1.5.0")

    with zipfile.ZipFile(wheel) as zf:
        metadata = zf.read("pregdos-9.9.9.dist-info/METADATA")
        record = zf.read("pregdos-9.9.9.dist-info/RECORD").decode()

    expected = base64.urlsafe_b64encode(hashlib.sha256(metadata).digest()).rstrip(b"=").decode()
    line = next(ln for ln in record.splitlines() if ln.startswith("pregdos-9.9.9.dist-info/METADATA,"))
    assert f"sha256={expected}" in line
    assert line.endswith(f",{len(metadata)}")


def test_repin_refuses_silently_succeeding_when_the_pin_is_gone(tmp_path):
    """If pyproject stops pinning by URL, this must fail loudly rather than ship a no-op."""
    path = tmp_path / "pregdos-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pregdos-9.9.9.dist-info/METADATA",
                    "Metadata-Version: 2.1\nName: pregdos\nVersion: 9.9.9\nRequires-Dist: numpy\n")
        zf.writestr("pregdos-9.9.9.dist-info/RECORD", "")

    with pytest.raises(SystemExit, match="no direct-URL requirement"):
        builder.repin_direct_url(path, "dicomexport", "1.5.0")


# --- the pin is read from pyproject, so it cannot drift from what the code declares ---

def test_dicomexport_pin_matches_pyproject():
    pin = builder.dicomexport_pin()
    assert pin.startswith("git+https://github.com/nbassler/dicomexport@")
    assert (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").count(pin) == 1


# ---------------------------------------------------------------------------
# The verifier must check the version it is describing
# ---------------------------------------------------------------------------

def test_bundled_version_is_read_from_the_wheel_beside_the_verifier(tmp_path, monkeypatch):
    """`pip install pregdos` over an existing venv is a no-op, so the check has to notice."""
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "pregdos-0.5.2.post33+g58a296f7f-py3-none-any.whl").touch()
    (wheelhouse / "numpy-2.4.6-cp311-cp311-manylinux_2_28_x86_64.whl").touch()
    monkeypatch.setattr(verifier, "__file__", str(tmp_path / "verify_offline_install.py"))

    assert verifier._bundled_version() == "0.5.2.post33+g58a296f7f"


def test_bundled_version_is_none_without_a_wheelhouse(tmp_path, monkeypatch):
    """Run from a source checkout there is nothing to compare against; that is not a failure."""
    monkeypatch.setattr(verifier, "__file__", str(tmp_path / "verify_offline_install.py"))
    assert verifier._bundled_version() is None


@pytest.mark.parametrize("a, b, same", [
    ("0.5.2.post33+g58a296f7f", "0.5.2.post33+g58a296f7f", True),
    ("0.5.2.post33+g58a296f7f", "0.5.2.post30+gbaa564881", False),   # the stale-install case
    ("1.0.0", "1.0.0", True),
    ("1.0.0.dev1+ab_cd", "1.0.0.dev1+ab-cd", True),                  # pip normalises _ and -
])
def test_version_comparison_matches_pip_normalisation(a, b, same):
    assert verifier._same_version(a, b) is same
