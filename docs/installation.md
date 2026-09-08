# Installation

PregDos can run either directly on a workstation with OpenTOPAS installed, or inside the
combined Docker image that contains OpenTOPAS, SLURM, and the Flask webserver.

PregDos requires OpenTOPAS 4.2.3 or newer. Older TOPAS/OpenTOPAS builds are not supported
because they can corrupt multithreaded scorer statistics: scorer `Sum` may become
`NaN`, and `Standard_Deviation` can be silently underestimated.

## Local Workstation Install

Use this mode when the same machine running the PregDos webserver can execute TOPAS directly.
SLURM is not required: if `sbatch` is unavailable, PregDos uses its local FIFO executor.

### Prerequisites

- Python 3.11 or newer. (On RHEL 9 the system `python3` is 3.9 — install the `python3.11`
  AppStream package and build the venv with it.)
- A working OpenTOPAS 4.2.3+ installation.
- Geant4 data files available to TOPAS.
- `git`, because `dicomexport` is installed from a GitHub tag.

Confirm TOPAS is reachable:

```bash
topas --version
```

If TOPAS is not on `PATH`, set `TOPAS_BIN` to the executable path before starting PregDos.

### Install PregDos

From the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

For development and tests:

```bash
pip install -e ".[dev]"
```

The package dependency list installs `dicomexport` from the pinned git dependency in
`pyproject.toml`. If that install fails, check network access and that `git` is available.

### Start The Webserver

```bash
. .venv/bin/activate
pregdos-web
```

Open http://localhost:5000.

### Airgapped Install (No Network At All)

Releases carry an **offline wheelhouse**: one tarball with PregDos, `dicomexport`, and every
dependency, plus `pip`/`setuptools`/`wheel` so a venv can be bootstrapped with no index.

```bash
tar xzf pregdos-<version>-wheelhouse-cp311-manylinux_2_28_x86_64.tar.gz
cd pregdos-<version>-wheelhouse-cp311-manylinux_2_28_x86_64
sha256sum -c sha256sums

python3.11 -m venv /opt/pregdos/venv
/opt/pregdos/venv/bin/pip install --no-index --find-links=wheelhouse pregdos
/opt/pregdos/venv/bin/python verify_offline_install.py
```

The last step renders real pages rather than only importing the module — the bundled
templates, beam models and SPR tables resolve at runtime, so a packaging gap shows up as a
broken page, not an import error. CI runs the same script against the same tarball, in a
container with no network, before the release is published.

**The tarball is target-specific.** Its wheels carry Python ABI and platform tags, so the name
records what it was built for. On RHEL 9 the system `python3` is 3.9 — install the `python3.11`
AppStream package and build the venv with that, not with `python3`. Using the wrong interpreter
fails with "no matching distribution", which is the intended outcome.

TOPAS is not included (separate licensing; the site supplies it). Point `topas_bin` at the
local installation via the config file below.

To build the artifact yourself, on a machine that *does* have a network:

```bash
python packaging/build_wheelhouse.py            # defaults to cp311 / manylinux_2_28_x86_64
python packaging/build_wheelhouse.py --python-version 313 --platform manylinux_2_28_x86_64
```

### Configuration File

For anything beyond a developer laptop, settings belong in a TOML file rather than in
environment variables. PregDos reads, lowest precedence first:

1. `/etc/pregdos/config.toml`
2. `$XDG_CONFIG_HOME/pregdos/config.toml` (falling back to `~/.config`)
3. the environment

Those two files **merge per key**. `pregdos-web --config PATH` or `$PREGDOS_CONFIG` instead
**replaces** both, so one deliberate file is the whole story; `--config` wins if both are given.
Environment variables beat every file, so the container and the tables below keep working
unchanged.

An annotated example with every key, its default, and when to change it ships inside the
package. Copy it and edit:

```bash
python -c 'from pregdos import config; print(config.example_text())' \
    | sudo tee /etc/pregdos/config.toml
```

An unknown key, an unknown section or a wrong type is a startup error naming the file and the
key — a typo never silently does nothing. `pregdos-web --config PATH` validates before binding
a port, so a bad file fails immediately rather than on whichever page first reads it.

> `--config` is a CLI flag, so a WSGI server that imports `pregdos.webserver:app` directly never
> sees it. Use `PREGDOS_CONFIG` in the unit file for those deployments.

The sections are `[paths]` (`work_dir`, `topas_bin`, `dicomexport`, `dicomexport_timeout`),
`[scheduler]` (see below), and `[network]` (`update_check`).

#### Airgapped sites

Set `update_check = false` under `[network]`. `/about` otherwise asks api.github.com whether a
newer PregDos exists, which on an isolated node can only ever time out.

#### Submitting into a site SLURM

When SLURM is already running on the node as a host service, install PregDos natively (not the
`pregdos-slurm` container, which brings its own scheduler and would double-book the CPUs) and
point `[scheduler]` at the site's queue:

```toml
[scheduler]
partition = "clinical"
cpus_per_task = 16
submit_as_user = ""
prologue = """
. /etc/profile.d/modules.sh
module load topas/4.2.3
"""
```

- **Every string is optional**, and empty means the `sbatch` flag is omitted entirely — so
  `partition`, `account`, `qos`, `walltime` and `memory` left unset let SLURM apply its own
  site defaults. Set one only when this site needs something else.
- **`cpus_per_task` matters.** Left at `0`, PregDos requests as many CPUs as the machine
  running the *web* process reports. If the partition offers fewer, SLURM rejects every field
  job at submit. The single-threaded structure-mask pre-pass always asks for 1 regardless.
- **`submit_as_user = ""`** stops PregDos dropping privileges to a `slurm` account. The default
  `"auto"` is for the shipped container, which runs as root; a site install submits as its own
  service account.
- **`prologue`** is shell source run before TOPAS, for sites where TOPAS lives behind
  environment modules — `PATH` alone is not enough, TOPAS also needs its Geant4 data and
  `LD_LIBRARY_PATH`. It runs under `/bin/sh`, where `module` is undefined, so source the module
  init first. It applies to **both** backends, because PregDos falls back to local execution
  whenever `sbatch` is off `PATH`; note that the local backend discards stderr, so a *failing*
  prologue is visible in `slurm-%j.out` but silent locally.

Because `prologue` is executed by the shell that runs TOPAS, `/etc/pregdos/config.toml` must be
owned by root and not writable by anyone else (`0644`).

> On a **multi-node** cluster `work_dir` must be on shared storage. `sbatch --chdir` resolves on
> the compute node, so a node-local path means every field starts in a directory that is empty
> or absent and dies immediately.

### Useful Environment Variables

Environment variables override the config file. Set these before running `pregdos-web`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PREGDOS_WORK_DIR` | `/var/tmp/pregdos` | Root directory for uploaded studies and generated runs. Keep it on **persistent disk** — never `/tmp`, which is usually a RAM-backed tmpfs that is wiped on reboot and steals memory from the TOPAS workers. `/var/tmp` persists across reboots and its contents are auto-reaped after ~30 days (see the retention drop-in below). |
| `TOPAS_BIN` | `topas` | TOPAS executable used by local runs and version checks. |
| `PREGDOS_EXECUTOR` | `auto` | `auto`, `local`, or `slurm`. `auto` uses SLURM when `sbatch` exists, otherwise local execution. |
| `PREGDOS_SECRET_KEY` | random per process | Flask session secret. Set a stable value for persistent deployments. |
| `PREGDOS_DEBUG` | unset | Set to `1` only for local Flask debugging. |

Example local setup:

```bash
export PREGDOS_WORK_DIR="$PWD/.pregdos_uploads"
export TOPAS_BIN=/opt/OpenTOPAS/bin/topas
export PREGDOS_EXECUTOR=local
export PREGDOS_SECRET_KEY="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
pregdos-web
```

## Docker Install

The published image is the simplest way to run the full stack:

```bash
docker pull ghcr.io/eurados/pregdos:latest-topas4.2.3
docker run --rm -it --hostname localhost -p 5000:5000 ghcr.io/eurados/pregdos:latest-topas4.2.3
```

Open http://localhost:5000.

The container includes OpenTOPAS, Geant4 data, a single-node SLURM setup, and PregDos. Job
working directories and logs live inside the container unless you mount a host volume.

To persist uploaded studies and generated runs:

```bash
mkdir -p "$PWD/pregdos_uploads"
docker run --rm -it --hostname localhost -p 5000:5000 \
  -v "$PWD/pregdos_uploads:/home/slurm/pregdos_uploads" \
  -e PREGDOS_WORK_DIR=/home/slurm/pregdos_uploads \
  ghcr.io/eurados/pregdos:latest-topas4.2.3
```

### Shared / multi-user deployment

PregDos runs as a single server process that many staff reach over the intranet; every user
shares one studies root. For such a deployment, point `PREGDOS_WORK_DIR` at a large,
persistent, non-tmpfs location owned by the account that runs the server, for example:

```bash
sudo install -d -o "$USER" -g "$USER" -m 700 /srv/pregdos   # or /var/lib/pregdos
export PREGDOS_WORK_DIR=/srv/pregdos
```

No dedicated `pregdos` system user is required — reuse the account that launches the server.
PregDos creates the directory `0700` when it makes it itself; pre-create it with your own
permissions (e.g. group-shared `0770`) if several accounts must share the tree, and PregDos
will leave those permissions untouched.

### Run retention

Runs are transient — results must be downloaded off the server (reports, or the **Full run
(ZIP)** archive on a run's page). The default `/var/tmp/pregdos` is already auto-reaped by
the distro's `systemd-tmpfiles` policy (~30 days on Debian). To make the policy explicit, or
when `PREGDOS_WORK_DIR` points elsewhere, install a `systemd-tmpfiles` drop-in. Write it
directly (this does not depend on the source tree, so it works for a `pip` install too;
change the path if you set `PREGDOS_WORK_DIR`, and the `30d` age for a different retention):

```bash
# Type Path             Mode UID GID Age
echo 'e /var/tmp/pregdos - - - 30d' | sudo tee /etc/tmpfiles.d/pregdos.conf
sudo systemd-tmpfiles --clean
```

A fuller, commented version of this file lives at `packaging/tmpfiles.d/pregdos.conf` in the
PregDos source tree.

The web UI tells users how many days a run is kept; keep that in sync with
`RUN_RETENTION_DAYS` in `pregdos/webserver.py` and the drop-in's age field.

## Build The Docker Image From Source

The combined PregDos image reuses a pre-built OpenTOPAS base image.

First build the supported OpenTOPAS base:

```bash
docker build -t pregdos-base-opentopas-v4.2.3 -f docker/opentopas/4.2.3/Dockerfile .
```

Then build the combined image:

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile .
```

If you built or named a different supported OpenTOPAS base image, pass it through
`OPENTOPAS_IMAGE`:

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile . \
  --build-arg OPENTOPAS_IMAGE=pregdos-base-opentopas-v4.2.3
```

Run the locally built image:

```bash
docker run --rm -it --hostname localhost -p 5000:5000 pregdos
```

See [`docker/pregdos/README.md`](../docker/pregdos/README.md) for lower-level Docker build
details.
