# PregDos — TODO / Roadmap

## Webserver

- [x] Wire job submission to `sbatch` (Submit Jobs button → `/submit` route)
- [x] Add job status page (live `squeue` view on job_submitted page, auto-refresh every 5s)
- [x] Add fetus dose scorer configuration (neutron H*(10), gamma, proton primary/secondary)
- [x] Merge structure selection and scorer configuration into a single setup page
- [x] Bundled SPR tables and beam models selectable from dropdown (upload still available)
- [ ] Add results/file browser page
- [ ] Turn off `debug=True` in `webserver.py` for production/container use
- [ ] Remove unused `StructureSelection` dataclass from `models.py` or wire it in

## Docker — combined image (`docker/pregdos/`)

- [ ] Switch Qt5 → Qt6 runtime libs when building with `OPENTOPAS_IMAGE=pregdos-opentopas-v4.2.3`
- [ ] Remove `openssh-server` from production image (currently included for development convenience only)
- [ ] Trim runtime apt dependencies — current list is conservative
- [ ] Update Docker image to include new `pregdos/data/spr_tables/` and `pregdos/data/beam_models/` package data

## Simulation workflow

- [x] Define a job working directory convention (timestamped `job_<YYYYMMDD_HHMMSS>/` under study dir)
- [ ] Add post-processing step triggered on SLURM job completion
- [ ] Validate generated TOPAS scorer blocks against Marijke's reference scripts in `_temp/`

## Infrastructure

- [x] Document OpenTOPAS/Geant4 version compatibility matrix (see `docker/opentopas/README.md`)
- [ ] Add GitHub Actions workflow to build and test the combined `docker/pregdos/` image
- [ ] Add GitHub Actions workflow to build and smoke-test the `docker/opentopas/` images

## Known issues

- [ ] Qt OpenGL visualization fails in Docker with X11 forwarding ("failed to create drawable") — missing runtime Mesa/GLX packages. Workaround: use parameter files without visualization.
- [ ] The pregdos webserver runs as `debug=True` — not suitable for any shared deployment
