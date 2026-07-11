# PregDos

[![CI](https://github.com/Eurados/pregdos/actions/workflows/ci.yml/badge.svg)](https://github.com/Eurados/pregdos/actions/workflows/ci.yml)
[![Docker](https://github.com/Eurados/pregdos/actions/workflows/docker-pregdos.yml/badge.svg)](https://github.com/Eurados/pregdos/actions/workflows/docker-pregdos.yml)
[![Dependabot](https://img.shields.io/badge/dependabot-enabled-025E8C?logo=dependabot)](https://github.com/Eurados/pregdos/security/dependabot)

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

## How structure dose is computed (design note)

PregDos reports a mean dose per RT structure **on the CT voxel grid alone** — it never
builds a separate RTDOSE dose grid. This is a deliberate design choice.

A single-bin (`1x1x1`) TOPAS scorer filtered to a structure
(`OnlyIncludeIfInRTStructure`) filters *which hits count* but normalises the result by the
**whole patient bounding box**, not the structure (see issue #50), so its absolute value is
wrong by roughly the patient/structure volume ratio. PregDos corrects this without a second
grid:

1. **Mask pre-pass.** A cheap ~1-history TOPAS run writes a per-structure binary mask on the
   native CT voxel grid (`SetBinToMinusOneIfNotInRTStructure`).
2. **Metrics.** From that mask plus the CT Hounsfield units and the Schneider HU→density
   table already embedded in the TOPAS input, PregDos computes each structure's **volume**
   (voxel count × voxel volume) and **mass** (Σ density × voxel volume).
3. **Renormalise.** The single-bin scorer, which TOPAS divided by the patient box, is
   rescaled to the structure:
   - `EnergyDeposit` (proton/gamma absorbed dose) — divide by structure **mass** → Gy.
   - `DoseToWater` and `AmbientDoseEquivalent` (H\*(10)) — multiply by
     `V_patient / V_structure` (**volume**), since TOPAS already applied the local density.

**Why the CT grid and not an RTDOSE grid:**

- **Memory.** The CT voxel grid is loaded anyway, so structure scoring reuses it and adds
  nothing. Superimposing a clinical RTDOSE grid over the CT means allocating and tracking a
  second full voxel grid — the exact pattern that has OOM'd this machine on large cases.
- **Coverage.** The CT covers the whole patient, so **out-of-field structures such as the
  fetus are scored even when they lie outside the clinical RTDOSE grid** — which the RTDOSE
  route cannot reach at all.
- **Runtime.** Scoring happens only in the CT geometry, not in a dose grid layered on top of
  it — a modest speedup and simpler geometry.

All PregDos doses are **physical** absorbed dose (Gy) / equivalent dose (Sv); no RBE is
applied. A clinical Eclipse proton RTDOSE is `Gy(RBE) = physical dose-to-water × 1.1`.

The method is validated against a full in-field RTDOSE cube on the Eclipse grid: the cube's
CTV mask-mean reproduces the mask/metrics route to ~1.4 %, and lands within ~8 % of the
Eclipse RTDOSE CTV **mask-mean** (mean-to-mean — not the hotter single-point centroid).

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

PregDos requires OpenTOPAS 4.2.3 or newer. Older TOPAS/OpenTOPAS builds are not supported
because OpenTOPAS 4.0.0 and legacy TOPAS 3.9 can corrupt multithreaded scorer statistics
(issue #49).

| Tag | OpenTOPAS | Geant4 |
|-----|-----------|--------|
| `latest-topas4.2.3` | v4.2.3 | 11.3.2 |

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
    opentopas/    Standalone supported OpenTOPAS image (v4.2.3)
    slurm/        Standalone SLURM image (for testing)
    pregdos/      Combined production image
```

## Roadmap / TODO

See [TODO.md](TODO.md) for the full list of open tasks.

## Acknowledgements

This work is part of the
[SONORA project](https://pianoforte-partnership.eu/sonora/), which has received
funding from the European Union's EURATOM research and innovation programme
under grant agreement No 101061037
([PIANOFORTE](https://pianoforte-partnership.eu/) - European Partnership for
Radiation Protection Research).
