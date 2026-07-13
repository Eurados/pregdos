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

- Python 3.10 or newer.
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

### Useful Environment Variables

Set these before running `pregdos-web`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `UPLOAD_FOLDER` | system temp directory, `pregdos_uploads` | Root directory for uploaded studies and generated runs. |
| `TOPAS_BIN` | `topas` | TOPAS executable used by local runs and version checks. |
| `PREGDOS_EXECUTOR` | `auto` | `auto`, `local`, or `slurm`. `auto` uses SLURM when `sbatch` exists, otherwise local execution. |
| `PREGDOS_SECRET_KEY` | random per process | Flask session secret. Set a stable value for persistent deployments. |
| `PREGDOS_DEBUG` | unset | Set to `1` only for local Flask debugging. |

Example local setup:

```bash
export UPLOAD_FOLDER="$PWD/.pregdos_uploads"
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
  -e UPLOAD_FOLDER=/home/slurm/pregdos_uploads \
  ghcr.io/eurados/pregdos:latest-topas4.2.3
```

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
