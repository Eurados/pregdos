# Installation

PregDos requires OpenTOPAS 4.2.3 or newer.

Older TOPAS/OpenTOPAS builds are not supported. OpenTOPAS 4.0.0 and legacy
TOPAS 3.9 can corrupt multithreaded scorer statistics: scorer `Sum` may become
`NaN`, and `Standard_Deviation` can be silently underestimated. PregDos refuses
submissions when it can identify an unsupported runtime; see issue #49.

## Docker

The recommended installation path is the published Docker image:

```bash
docker pull ghcr.io/eurados/pregdos:latest-topas4.2.3
docker run --rm -it --hostname localhost -p 5000:5000 ghcr.io/eurados/pregdos:latest-topas4.2.3
```

Then open http://localhost:5000.

## Build From Source

Build the supported OpenTOPAS base image first:

```bash
docker build -t pregdos-base-opentopas-v4.2.3 -f docker/opentopas/4.2.3/Dockerfile .
```

Then build the combined PregDos image:

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile .
```

For local Python development without Docker:

```bash
pip install -e ".[dev]"
pregdos-web
```

Local non-Docker simulations still require a supported OpenTOPAS runtime on
`PATH`, plus valid Geant4 data files.
