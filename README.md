# PregDos

A tool for calculating dose to a fetus in proton therapy.
Converts DICOM RT plans to [OpenTOPAS](https://github.com/OpenTOPAS/OpenTOPAS)
Monte Carlo input files and submits them as SLURM jobs.

<img width="980" height="579" alt="image" src="https://github.com/user-attachments/assets/85813578-c4b5-4cd8-948b-ba12d501a3e0" />


> Still under development — not ready for use.

## What it does

1. Upload a DICOM RT plan via the web UI
2. Select which structures to include
3. Convert to TOPAS input files
4. Run TOPAS via SLURM (job scheduling)
5. Post-process and display dose/effective dose per structure

## Running the webserver locally (development)

```bash
pip install -e ".[dev]"
pregdos-web
```

Then open http://localhost:5000 in a browser.

Optionally set a custom secret key:

```bash
PREGDOS_SECRET_KEY=mysecret pregdos-web
```

## Running tests

```bash
pytest
```

## Running as Docker

Pre-built images are published to the GitHub Container Registry and work on
Linux, macOS, and Windows 10/11 (via Docker Desktop + WSL2) — no compilation needed.

```bash
docker pull ghcr.io/eurados/pregdos:latest-topas4.2.3
docker run --rm -it --hostname localhost -p 5000:5000 ghcr.io/eurados/pregdos:latest-topas4.2.3
```

Then open http://localhost:5000 in a browser.

Two variants are available, differing only in the bundled OpenTOPAS version:

| Tag | OpenTOPAS | Geant4 |
|-----|-----------|--------|
| `latest-topas4.2.3` | v4.2.3 | 11.3.2 |
| `latest-topas4.0.0` | v4.0.0 | 11.1.3 |

For release-pinned tags (e.g. `v0.2.1-topas4.2.3`) see the
[Packages](https://github.com/Eurados/pregdos/pkgs/container/pregdos) page.

### Building from source

The full production image (OpenTOPAS + SLURM + webserver) requires building
in two steps. See [docker/pregdos/README.md](docker/pregdos/README.md) for
the complete build and run instructions.

Standalone OpenTOPAS images (for testing simulations without the webserver)
are documented in [docker/opentopas/README.md](docker/opentopas/README.md).

## Project layout

```
pregdos/          Python package (Flask webserver, DICOM conversion)
tests/            pytest test suite
docker/
    opentopas/    Standalone OpenTOPAS images (v4.0.0 and v4.2.3)
    topas3.9/     Standalone TOPAS 3.9 image (local build only, not redistributable)
    slurm/        Standalone SLURM image (for testing)
    pregdos/      Combined production image
```

## Roadmap / TODO

See [TODO.md](TODO.md) for the full list of open tasks.

## Acknowledgements

This work is part of the SONORA project, which has received funding from the
European Union's EURATOM research and innovation programme under grant
agreement No 101061037
(PIANOFORTE – European Partnership for Radiation Protection Research).
