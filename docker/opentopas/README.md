# OpenTOPAS Docker Image

Standalone development/test image with Geant4 + OpenTOPAS compiled from source.

## Build

From the project root:

```bash
docker build -t pregdos-opentopas -f docker/opentopas/Dockerfile .
```

Override versions via build args:

```bash
docker build -t pregdos-opentopas -f docker/opentopas/Dockerfile . \
    --build-arg GEANT4_VERSION=11.1.3 \
    --build-arg OPENTOPAS_REF=v4.0.0
```

> **Note:** Geant4 takes ~25 minutes to compile. The dataset download adds
> another ~10 minutes. Subsequent builds use the Docker layer cache unless
> `GEANT4_VERSION` changes.

## Run

```bash
docker run -it --rm pregdos-opentopas
```

## Verify TOPAS works

```bash
docker run --rm pregdos-opentopas /opt/TOPAS/OpenTOPAS-install/bin/topas --version
```

## Notes

- Base image: `debian:trixie`
- SLURM is **not** included here — use `docker/pregdos/` for the full
  production image (OpenTOPAS + SLURM + webserver).
- `OPENTOPAS_REF` accepts any git branch, tag, or commit hash.
- `GEANT4_VERSION` must match a tag on the
  [Geant4 GitLab](https://gitlab.cern.ch/geant4/geant4/-/tags) and a
  corresponding `lib/Geant4-<version>` directory produced by the build.
  Known working combination: `GEANT4_VERSION=11.1.3` + `OPENTOPAS_REF=v4.0.0`.