# PregDos — TODO / Roadmap

## Webserver

- [ ] Wire job submission to `sbatch` (currently no SLURM integration in the Flask routes)
- [ ] Wire `StructureSelection` dataclass into the webserver routes (currently unused)
- [ ] Add job status page (poll `squeue` / `sacct`)
- [ ] Add results/file browser page
- [ ] Turn off `debug=True` in `webserver.py` for production/container use

## Docker — combined image (`docker/pregdos/`)

- [ ] Switch Qt5 → Qt6 runtime libs when building with `OPENTOPAS_IMAGE=pregdos-opentopas-v4.2.3`
- [ ] Trim runtime apt dependencies — current list is conservative
- [ ] Add `topas3.9` install to the topas3.9 Dockerfile (`docker/topas3.9/Dockerfile`)

## Simulation workflow

- [ ] Add post-processing step triggered on SLURM job completion
- [ ] Define a job working directory convention (one dir per submission)
- [ ] Validate TOPAS input file generation against a known reference plan

## Infrastructure

- [ ] Add GitHub Actions workflow to build and test the combined `docker/pregdos/` image
- [ ] Add GitHub Actions workflow to build and smoke-test the `docker/opentopas/` images
- [ ] Document OpenTOPAS/Geant4 version compatibility matrix

## Known issues

- [ ] Qt OpenGL visualization fails in Docker with X11 forwarding ("failed to create drawable") — likely missing runtime Mesa/GLX packages (`libglx-mesa0`, `libgl1`). Workaround: use parameter files without visualization, or run non-graphical examples.

- [ ] `StructureSelection` dataclass is defined but not yet wired into the webserver
- [ ] The pregdos webserver runs as `debug=True` — not suitable for any shared deployment