# OpenTOPAS Docker Images

Standalone development/test image with Geant4 + OpenTOPAS compiled from source.

PregDos requires OpenTOPAS 4.2.3 or newer. Older TOPAS/OpenTOPAS builds are
unsupported because they can corrupt multithreaded scorer statistics (issue #49).

| Dockerfile | OpenTOPAS | Geant4 | Qt |
|---|---|---|---|
| `docker/opentopas/4.2.3/Dockerfile` | v4.2.3 | 11.3.2 | Qt6 |

## Build

```bash
docker build -t pregdos-base-opentopas-v4.2.3 -f docker/opentopas/4.2.3/Dockerfile .
```

Override versions:

```bash
docker build -t pregdos-base-opentopas-v4.2.3 -f docker/opentopas/4.2.3/Dockerfile . \
    --build-arg GEANT4_VERSION=11.3.2 \
    --build-arg OPENTOPAS_REF=v4.2.3
```

> **Note:** Geant4 takes substantial time to compile. The dataset download adds
> another ~5-10 minutes. Subsequent builds use the Docker layer cache unless
> `GEANT4_VERSION` changes.

## Run

```bash
docker run -it --rm pregdos-base-opentopas-v4.2.3
```

## Verify TOPAS works

```bash
docker run --rm pregdos-base-opentopas-v4.2.3 /opt/TOPAS/OpenTOPAS-install/bin/topas --version
```

## Notes

- Base image: `debian:trixie` (both)
- SLURM is **not** included here — use `docker/pregdos/` for the full
  production image (OpenTOPAS + SLURM + webserver).
- `OPENTOPAS_REF` accepts any supported OpenTOPAS git branch, tag, or commit hash.
  PregDos requires the resulting TOPAS runtime to be OpenTOPAS 4.2.3 or newer.
- `GEANT4_VERSION` must match a tag on the
  [Geant4 GitLab](https://gitlab.cern.ch/geant4/geant4/-/tags) and a
  corresponding `lib/Geant4-<version>` directory produced by the build.
- Qt6 is required for OpenTOPAS v4.2.3.
- Known working combinations:
  - `GEANT4_VERSION=11.3.2` + `OPENTOPAS_REF=v4.2.3` (Qt6)
