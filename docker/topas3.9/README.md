# TOPAS 3.9 Docker Image

Standalone image with Geant4 10.07.p03 + TOPAS 3.9, for backwards compatibility
with legacy simulation parameter files.

## Redistribution restriction

**TOPAS 3.9 may not be redistributed.** This image is therefore:
- Not pushed to any container registry
- Not built by any CI/CD workflow
- For local use only — build it yourself if you need it

## Prerequisites

The TOPAS 3.9 source is downloaded from a Google Drive link during the build.
**This link may break at any time.** If it does, you will need to obtain the
source archive through other means and replace the `wget` step with a `COPY`.

## Base image

`debian:bookworm` is required. `debian:trixie` does **not** work:
- trixie ships cmake 3.31, which is incompatible with Geant4 10.7.3's generated
  cmake config files (`Geant4PackageCache.cmake` macro breakage).
- bookworm provides cmake 3.25 + gcc-12, both compatible with this vintage.

## Build

```bash
docker build -t pregdos-base-topas-v3.9 -f docker/topas3.9/Dockerfile .
```

This takes ~40 minutes (Geant4 compile + dataset download).

## Run

```bash
docker run -it --rm pregdos-base-topas-v3.9
```

## Verify TOPAS works

```bash
docker run --rm pregdos-base-topas-v3.9 /opt/TOPAS/topas-install/bin/topas --version
```

## Integrating with the pregdos image

If you need to combine TOPAS 3.9 with the pregdos webserver and SLURM, the
pregdos runtime stage must also use `debian:bookworm` (not trixie) to avoid
library version mismatches. Pass the image as a build arg:

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile . \
    --build-arg OPENTOPAS_IMAGE=pregdos-base-topas-v3.9 \
    --build-arg DEBIAN_VERSION=bookworm
```

> **Note:** The pregdos Dockerfile does not yet support `DEBIAN_VERSION` — this
> would need to be added if integration is required.
