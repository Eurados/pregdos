#!/usr/bin/env python3
"""Build an offline wheelhouse: PregDos plus every dependency, for a site with no network.

    $ python packaging/build_wheelhouse.py
    -> dist/pregdos-<version>-wheelhouse-cp311-manylinux_2_28_x86_64.tar.gz

The target is **declared, not inherited**.  Wheels carry Python ABI and platform tags, so a
wheelhouse is only valid for what it was built against; ``pip download --only-binary=:all:
--platform ... --python-version ...`` refuses to substitute a host-specific build, whereas a
plain ``pip wheel`` would quietly compile one against whatever this machine happens to be.

Two packages cannot be downloaded and must be built here: ``pregdos`` itself, and
``dicomexport``, which is a ``git+https://`` pin rather than an index package.  Both are pure
Python, so they build to ``py3-none-any`` and stay valid for every target.

The dependency set is read from **the built wheels' own metadata**, never from
``pyproject.toml``.  That is not pedantry: ``dicomexport`` requires ``pandas``, which appears
nowhere in PregDos's dependency list, so a wheelhouse assembled from ``pyproject.toml`` would
be missing it and would fail at the airgapped machine -- the one place the gap cannot be
investigated.

TOPAS is deliberately out of scope: separate licensing, and the site supplies it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Shipped so a venv can be bootstrapped on a machine whose Python lacks a usable ensurepip.
BOOTSTRAP = ["pip", "setuptools", "wheel"]

README = """\
# PregDos offline wheelhouse

Everything needed to install PregDos on a machine with no network access.

    Target:  Python {python_dotted} on {platform}
    Version: pregdos {version}

## Install

    python{python_dotted} -m venv /opt/pregdos/venv
    /opt/pregdos/venv/bin/pip install --no-index --find-links=wheelhouse pregdos
    /opt/pregdos/venv/bin/pregdos-web

If `python -m venv` fails because the interpreter has no usable `ensurepip`, create the
environment without pip and install it from this wheelhouse instead:

    python{python_dotted} -m venv --without-pip /opt/pregdos/venv
    /opt/pregdos/venv/bin/python -m ensurepip --default-pip || true
    /opt/pregdos/venv/bin/python wheelhouse/{pip_wheel}/pip install \\
        --no-index --find-links=wheelhouse pip setuptools wheel

## Upgrading an existing install

`pip install pregdos` treats an already-installed copy as satisfied and never compares
versions, so over an existing venv it silently does nothing.  Pass `--upgrade`:

    /opt/pregdos/venv/bin/pip install --no-index --find-links=wheelhouse --upgrade pregdos

`verify_offline_install.py` below checks that what is installed is what this tarball
contains, so a missed upgrade is caught rather than passing every other check while
describing the wrong build.

## Check the contents arrived intact

    sha256sum -c sha256sums

## Check the install actually works

Run this after installing. It renders real pages rather than only importing the module,
because the bundled templates, beam models and SPR tables are resolved at runtime -- a
packaging gap shows up as a broken page, not as an import error:

    /opt/pregdos/venv/bin/python verify_offline_install.py

The same script runs in CI against this tarball before it is published.

## This tarball is target-specific

The wheels here carry Python ABI and platform tags. **This tarball is only valid for
{python_dotted} on {platform}.** On RHEL 9 the system `python3` is 3.9, so install the
`python3.11` package and build the venv with that interpreter, not with `python3`.

Installing it against a different Python will fail with "no matching distribution", which is
the intended outcome -- it is better than a half-working install.

## What is not here

**TOPAS.** Separate licensing, and the site supplies it. Point `topas_bin` in
`/etc/pregdos/config.toml` (or `$TOPAS_BIN`) at the local installation.

## Configuration

The package ships an annotated example covering the work directory, the SLURM queue, and the
update check that an airgapped site wants turned off:

    /opt/pregdos/venv/bin/python -c 'from pregdos import config; print(config.example_text())' \\
        | sudo tee /etc/pregdos/config.toml

`requirements.txt` records the exact pinned set this tarball contains.
"""


def run(argv: list[str]) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True)


def wheel_metadata(wheel: Path):
    """The parsed METADATA of a built wheel."""
    with zipfile.ZipFile(wheel) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        return BytesParser().parsebytes(zf.read(name))


def runtime_requirements(wheel: Path) -> list[str]:
    """``Requires-Dist`` entries that a plain install actually pulls in.

    Extras are dropped (nobody installs ``dicomexport[gui]`` on a clinical node), and so are
    direct-URL requirements -- ``dicomexport @ git+https://...`` is satisfied by the wheel we
    built beside it, and pip cannot download a URL requirement from an index anyway.
    """
    requirements = []
    for raw in wheel_metadata(wheel).get_all("Requires-Dist") or []:
        if "extra ==" in raw:
            continue
        if "@" in raw.split(";")[0]:
            continue
        requirements.append(raw.strip())
    return requirements


def _record_line(path: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{path},sha256={digest},{len(data)}"


def repin_direct_url(wheel: Path, name: str, version: str) -> None:
    """Rewrite ``Requires-Dist: <name>@ <url>`` to ``<name>==<version>`` inside a built wheel.

    This is what makes the wheelhouse installable at all.  ``pyproject.toml`` pins
    ``dicomexport`` by ``git+https://`` URL, and that URL is copied verbatim into the wheel's
    METADATA -- where pip treats it as authoritative and clones GitHub even under
    ``--no-index --find-links``, because a direct-URL requirement is not considered satisfied
    by a local wheel of the same name.  Offline, that is a hard failure.

    Repinning to the exact version already sitting in the wheelhouse is both installable and
    more precise than the URL was.  Only the copy inside this tarball is touched; the wheel
    published for networked installs (#85) keeps its git pin.
    """
    replaced = False
    tmp = wheel.with_suffix(".repinned")
    with zipfile.ZipFile(wheel) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        metadata_name = next(n for n in src.namelist() if n.endswith(".dist-info/METADATA"))
        record_name = next(n for n in src.namelist() if n.endswith(".dist-info/RECORD"))

        raw = src.read(metadata_name).decode("utf-8")
        lines = []
        for line in raw.splitlines():
            if line.startswith("Requires-Dist:") and "@" in line:
                requirement = line.split(":", 1)[1].strip()
                if requirement.split("@")[0].strip().lower() == name.lower():
                    line = f"Requires-Dist: {name}=={version}"
                    replaced = True
            lines.append(line)
        metadata = ("\n".join(lines) + "\n").encode("utf-8")

        for item in src.infolist():
            if item.filename == metadata_name:
                out.writestr(item, metadata)
            elif item.filename == record_name:
                record = []
                for entry in src.read(record_name).decode("utf-8").splitlines():
                    if entry.startswith(metadata_name + ","):
                        entry = _record_line(metadata_name, metadata)
                    record.append(entry)
                out.writestr(item, ("\n".join(record) + "\n").encode("utf-8"))
            else:
                out.writestr(item, src.read(item.filename))

    if not replaced:
        tmp.unlink()
        raise SystemExit(
            f"{wheel.name} has no direct-URL requirement on {name}. If the pin moved to a "
            f"plain version, delete repin_direct_url(); if it moved elsewhere, update it."
        )
    tmp.replace(wheel)
    print(f"   repinned {name} -> =={version} in {wheel.name}")


def dicomexport_pin() -> str:
    """The ``dicomexport`` requirement from pyproject, so the pin cannot drift from the code."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for dep in data["project"]["dependencies"]:
        if dep.split("@")[0].strip().lower() == "dicomexport":
            return dep.split("@", 1)[1].strip()
    raise SystemExit("pyproject.toml no longer pins dicomexport by URL; update this script.")


def build(python_version: str, platform: str, dest: Path, keep_tree: bool = False) -> Path:
    python_dotted = f"{python_version[0]}.{python_version[1:]}"
    staging = Path(tempfile.mkdtemp(prefix="pregdos-wheelhouse-"))
    wheels = staging / "wheelhouse"
    wheels.mkdir(parents=True)

    # 1. The two packages that have to be built.  --no-deps: their dependencies are resolved
    #    against the *target* in step 2, not against this machine.
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(wheels), str(REPO_ROOT)])
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(wheels), dicomexport_pin()])

    pregdos_wheel = next(wheels.glob("pregdos-*.whl"))
    dicomexport_wheel = next(wheels.glob("dicomexport-*.whl"))
    version = pregdos_wheel.name.split("-")[1]

    built = {w.name.split("-")[0].replace("_", "-").lower() for w in wheels.glob("*.whl")}

    # 2. Everything else, for the declared target.  Read from the built wheels' metadata:
    #    dicomexport needs pandas, which pregdos's own dependency list never mentions.
    #    Anything already built above is skipped: it is in the wheelhouse, and no index has it.
    requirements = sorted({
        req for req in (
            *runtime_requirements(pregdos_wheel),
            *runtime_requirements(dicomexport_wheel),
            *BOOTSTRAP,
        )
        if re.split(r"[<>=!~;\[\s]", req, maxsplit=1)[0].strip().replace("_", "-").lower() not in built
    })
    print("\nResolving for the target:")
    for req in requirements:
        print("   ", req)
    print()

    req_file = staging / "_deps.in"
    req_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    run([
        sys.executable, "-m", "pip", "download",
        "--only-binary=:all:",
        "--platform", platform,
        "--python-version", python_version,
        "--dest", str(wheels),
        "-r", str(req_file),
    ])
    req_file.unlink()

    # Last, because it turns the direct URL into a plain pin that would otherwise be looked
    # up on an index in the step above.  Without it the offline install clones GitHub.
    repin_direct_url(pregdos_wheel, "dicomexport", dicomexport_wheel.name.split("-")[1])

    # 3. The verifier travels with the artifact, so CI and the site administrator run the
    #    identical check -- and so "did this install correctly?" is answerable on a machine
    #    with no way to fetch anything.
    shutil.copy2(Path(__file__).with_name("verify_offline_install.py"), staging)

    # 4. The paperwork that makes the tarball self-explanatory on a machine with no internet.
    pinned = sorted(
        f"{w.name.split('-')[0].replace('_', '-')}=={w.name.split('-')[1]}"
        for w in wheels.glob("*.whl")
    )
    (staging / "requirements.txt").write_text(
        f"# PregDos {version} offline wheelhouse -- cp{python_version} / {platform}\n"
        "# The exact set in wheelhouse/. Install with:\n"
        "#   pip install --no-index --find-links=wheelhouse pregdos\n"
        + "\n".join(pinned) + "\n",
        encoding="utf-8",
    )
    (staging / "README.md").write_text(
        README.format(
            version=version,
            platform=platform,
            python_dotted=python_dotted,
            pip_wheel=next(wheels.glob("pip-*.whl")).name,
        ),
        encoding="utf-8",
    )
    sums = []
    for path in sorted(staging.rglob("*")):
        if path.is_file() and path.name != "sha256sums":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            sums.append(f"{digest}  {path.relative_to(staging)}")
    (staging / "sha256sums").write_text("\n".join(sums) + "\n", encoding="utf-8")

    # 5. One tarball, named after its target -- someone will eventually carry the wrong one to
    #    a machine where they cannot investigate why it fails.
    stem = f"pregdos-{version}-wheelhouse-cp{python_version}-{platform}"
    dest.mkdir(parents=True, exist_ok=True)
    tarball = dest / f"{stem}.tar.gz"

    def neutral(entry: tarfile.TarInfo) -> tarfile.TarInfo:
        """Own everything as root:root with no user names.

        A tarball carrying the build machine's uid/gid chowns its contents to a uid that
        does not exist on the target, and extracting as root on the hospital node would
        silently hand the wheels to whoever happens to hold uid 1000 there.
        """
        entry.uid = entry.gid = 0
        entry.uname = entry.gname = "root"
        entry.mtime = int(entry.mtime)
        # The staging tree comes from mkdtemp, which is 0700.  Left alone, a tarball
        # unpacked as root would be unreadable to the service account that needs it.
        entry.mode = 0o755 if entry.isdir() else 0o644
        return entry

    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(staging, arcname=stem, filter=neutral)

    if keep_tree:
        tree = dest / stem
        shutil.rmtree(tree, ignore_errors=True)
        shutil.copytree(staging, tree)
        print(f"Staged tree: {tree}")
    shutil.rmtree(staging, ignore_errors=True)

    count = len(pinned)
    size = tarball.stat().st_size / 1e6
    print(f"\n{tarball}\n  {count} wheels, {size:.1f} MB, pregdos {version}")
    return tarball


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python-version", default="311", help="target Python, no dot (default: 311)")
    parser.add_argument("--platform", default="manylinux_2_28_x86_64",
                        help="target platform tag (default: manylinux_2_28_x86_64)")
    parser.add_argument("--dest", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--keep-tree", action="store_true",
                        help="also leave the unpacked tree beside the tarball, for verification")
    args = parser.parse_args(argv)

    build(args.python_version, args.platform, args.dest, args.keep_tree)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
