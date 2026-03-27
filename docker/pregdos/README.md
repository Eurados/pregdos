# PregDos — Combined Production Image

Single Docker container running the full PregDos pipeline:
- **OpenTOPAS** (Geant4 + TOPAS Monte Carlo simulation)
- **SLURM** (job scheduling, compiled without systemd)
- **Pregdos webserver** (Flask frontend)

The pregdos image does **not** recompile Geant4 or OpenTOPAS — it reuses a
pre-built OpenTOPAS image. Build that first, then build this one.

## Step 1 — Build the OpenTOPAS base image

```bash
# Default: OpenTOPAS v4.0.0 on Geant4 11.1.3 (Qt5)
docker build -t pregdos-base-opentopas-v4.0.0 -f docker/opentopas/4.0.0/Dockerfile .
```

This takes ~35 minutes (Geant4 compile + dataset download). Subsequent builds
use the Docker layer cache unless `GEANT4_VERSION` changes.

## Step 2 — Build the combined pregdos image

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile .
```

Override the SLURM version or pregdos package version:

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile . \
    --build-arg SLURM_VERSION=25.11.4 \
    --build-arg PREGDOS_VERSION=1.0.0
```

## Switching to OpenTOPAS v4.2.3

Build the v4.2.3 base image first:

```bash
docker build -t pregdos-base-opentopas-v4.2.3 -f docker/opentopas/4.2.3/Dockerfile .
```

Then point the pregdos build at it:

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile . \
    --build-arg OPENTOPAS_IMAGE=pregdos-base-opentopas-v4.2.3
```

> **Note:** v4.2.3 uses Qt6. The runtime Qt libraries in the Dockerfile
> are currently Qt5 (matching v4.0.0). When switching to v4.2.3, the Qt
> runtime packages must be updated accordingly (see TODO in Dockerfile).

## Run

```bash
docker run --rm -it --hostname localhost -p 5000:5000 pregdos
```

Job working directories and SLURM logs are under `/home/slurm/jobs/`.

To debug interactively:

```bash
docker exec -it $(docker ps -q --filter ancestor=pregdos) /bin/bash
```

The webserver starts automatically and is available at http://localhost:5000.

## Submit jobs

Jobs must be submitted as the `slurm` user:

```bash
su - slurm
sbatch --wrap="topas my_simulation.txt"
squeue
```

## TODO

See [TODO.md](../../TODO.md) at the project root for the full list.