# PregDos — TODO / Roadmap

## Before merging #97 (config file) to main

Found while doing the first real deployment onto the DCPT host (exrhel0583, RHEL 9.8,
OpenTOPAS behind `module load opentopas/4.2`).  This branch stays open until they are done.

- [ ] **`/about` cries wolf when TOPAS lives behind an environment module.**  Runs are fine —
  `executor._with_prologue` puts `module load` in front of the TOPAS command.  But
  `versions.topas_version` / `geant4_version` probe from the *web* process with a bare
  `shutil.which(topas_bin())`, which never sees `[scheduler] prologue`.  On a modules site the
  About page therefore reports TOPAS and Geant4 as `unknown` and shows "TOPAS was not found on
  PATH — simulations cannot run." while simulations run perfectly well.  That table is
  clinical-facing provenance, so a false negative there is worse than a missing one.
  Options, cheapest first: (a) document that the service environment must have the module
  loaded, and leave the code alone; (b) run the probe through the same prologue shell the
  executor uses; (c) let `TOPAS_VERSION`/`GEANT4_VERSION` in the config file, not just the
  environment, stand in for the probe.  (b) is the honest fix — one shell per probe, cached by
  `lru_cache` — but it makes a config value shell-executed in one more place, so decide
  deliberately.
- [x] **The listen address is hard-coded** — `webserver.main` ends in
  `app.run(host="0.0.0.0", port=5000)` with no flag and no config key.  The DCPT host already
  runs an unrelated Flask app on 5000, so PregDos cannot start there at all; the workaround is
  to bypass `main()` via `flask --app pregdos.webserver:app run --port ...`, which is not
  something a site should have to discover.  This branch is *about* replacing per-knob env
  vars with a config file, and the listen address is the one knob a site cannot avoid setting,
  so it belongs here: a `[server]` section with `host` and `port`, plus matching `--host` /
  `--port` flags.  Note `0.0.0.0` as the default deserves a second look while in there — the
  service has no authentication (deliberately, see #64) and TLS is #90.
  Done: `[server]` carries `host`, `port`, `ssl_cert`, `ssl_key`; flags override the file.
  The default stayed `0.0.0.0:5000` because the container depends on it, and the example
  config now says plainly what that exposes.
- [ ] **`4.2.p03` sits exactly on the `MINIMUM_TOPAS = (4, 2, 3)` floor.**  It parses correctly
  and passes, with zero margin.  Confirm this is really the build we want validated against
  before the branch merges.
- [x] **Document the two airgapped-install traps** in `docs/installation.md`, both of which cost
  a round trip on the real host: `python3.11 -m venv` on RHEL 9 bootstraps pip 22.3.1 from
  `python3.11-pip-wheel` and should be upgraded from the wheelhouse first; and
  `--find-links=wheelhouse` is *relative*, so running it from outside the unpacked tarball
  merely warns "Location is ignored" before failing on the first missing package.
- [x] Add a worked `[scheduler] prologue` example for an environment-modules site to
  `docs/installation.md` — the `. /etc/profile.d/modules.sh` first line is the part people get
  wrong, since `module` is undefined under `/bin/sh`.


## Webserver

- [x] Wire job submission to `sbatch` (Submit Jobs button → `/submit` route)
- [x] Add job status page (live `squeue` view on job_submitted page, auto-refresh every 5s)
- [x] Add fetus dose scorer configuration (neutron H*(10), gamma, proton primary/secondary)
- [x] Merge structure selection and scorer configuration into a single setup page
- [x] Bundled SPR tables and beam models selectable from dropdown (upload still available)
- [ ] **Results viewer** — expose scorer CSV outputs from job folders in the web UI:
  - `/jobs/<name>` already lists files; extend it to parse and display scorer CSVs as a table
  - Show one row per scorer (neutron H*(10), gamma, proton primary/secondary) with mean dose ± SD
  - Add a "Delete job" button on the job page (no auth required — trusted environment)
  - Consider a simple bar chart per scorer using a lightweight JS library (Chart.js or similar)
- [ ] Turn off `debug=True` in `webserver.py` for production/container use
- [ ] Remove unused `StructureSelection` dataclass from `models.py` or wire it in

## Docker — combined image (`docker/pregdos/`)

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
