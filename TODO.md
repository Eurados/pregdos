# PregDos — TODO / Roadmap

## Webserver

- [x] Wire job submission to `sbatch` (Submit Jobs button → `/submit` route)
- [ ] Wire `StructureSelection` dataclass into the webserver routes (currently unused)
- [x] Add job status page (live `squeue` view on job_submitted page, auto-refresh every 5s)
- [ ] Add results/file browser page
- [ ] Turn off `debug=True` in `webserver.py` for production/container use

## Docker — combined image (`docker/pregdos/`)

- [ ] Switch Qt5 → Qt6 runtime libs when building with `OPENTOPAS_IMAGE=pregdos-opentopas-v4.2.3`
- [ ] Remove `openssh-server` from production image (currently included for development convenience only)
- [ ] Trim runtime apt dependencies — current list is conservative
- [ ] Add `topas3.9` install to the topas3.9 Dockerfile (`docker/topas3.9/Dockerfile`)

## Simulation workflow

- [ ] Add post-processing step triggered on SLURM job completion
- [x] Define a job working directory convention (timestamped `job_<YYYYMMDD_HHMMSS>/` under study dir)
- [ ] Validate TOPAS input file generation against a known reference plan

## Infrastructure

- [ ] Add GitHub Actions workflow to build and test the combined `docker/pregdos/` image
- [ ] Add GitHub Actions workflow to build and smoke-test the `docker/opentopas/` images
- [x] Document OpenTOPAS/Geant4 version compatibility matrix (see `docker/opentopas/README.md`)

## Known issues

- [ ] Qt OpenGL visualization fails in Docker with X11 forwarding ("failed to create drawable") — missing runtime Mesa/GLX packages. Workaround: use parameter files without visualization.
- [ ] `StructureSelection` dataclass is defined but not yet wired into the webserver
- [ ] The pregdos webserver runs as `debug=True` — not suitable for any shared deployment