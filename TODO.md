# PregDos — TODO / Roadmap

## Before merging #97 (config file) to main

Found while doing the first real deployment onto the DCPT host (exrhel0583, RHEL 9.8,
OpenTOPAS behind `module load opentopas/4.2`).  This branch stays open until they are done.

- [x] **`/about` cries wolf when TOPAS lives behind an environment module.**  Runs are fine —
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
  Done: (b).  The probe runs in `/bin/sh` with the prologue applied whenever one is
  configured, and takes the direct path when none is — so a site without a prologue is
  unchanged, and one with a prologue is measured in the shell its jobs will run in.  `ldd`
  runs there too, or every libG4 line reads "not found" and Geant4 stays unknown.  A marker
  line separates the prologue's own chatter from the command's output.
- [x] **TOPAS ignored the SLURM allocation and started one thread per machine core.**
  dicomexport writes `i:Ts/NumberOfThreads = 0` and 0 means "every core"; PregDos asked
  SLURM for `cpus_per_task` and never told TOPAS.  On the site's 32-core node with
  `cpus_per_task = 16`, each of two concurrent fields started 32 workers -- 64 threads on 32
  cores, and *twice* the scoring memory each, since Geant4 allocates scorer arrays per
  thread.  One field then died with SIGSEGV inside libG4processes at its very first history
  (log showed `G4WT25`, i.e. worker 25, with only 16 CPUs granted).  Fixed:
  `executor.set_thread_count` rewrites the key at submit time to match `_cpus_per_task`,
  which also keeps the single-threaded mask pre-pass at 1.
  Worth upstreaming a `--threads` flag to dicomexport so the file is right when written;
  until then PregDos rewrites it, which is also more correct — `cpus_per_task` can change
  between converting a study and submitting it.
- [ ] **The Geant4 data pre-flight is vacuous at a modules site.**  Same blind spot as the
  About-page probe, found the same day: `g4_data_dir_problem()` reads `TOPAS_G4_DATA_DIR`
  from the *web process* environment, but at a modules site the module sets it inside the
  prologue shell.  The web process sees nothing, the check returns None, and the one
  pre-flight meant to catch "Geant4 aborts seconds after submission" never fires.  It cannot
  produce a false alarm — it simply cannot fire at all — which is why it survived the probe
  fix.  Read the variable through the prologue shell, the way `versions._shell_probe` now
  does, and check the `G4*DATA` directories the module actually sets while there.
- [ ] **`versions` can raise `ConfigError` after all** — its docstring promises it never
  raises, but `topas_bin()` calls `config.load()`, which does on a malformed file.
  `pregdos-web` validates at startup so the CLI is safe; a WSGI import
  (`gunicorn pregdos.webserver:app`) never calls `main()`, and there `/about` would 500
  instead of degrading to "unknown".  Pre-existing, surfaced by this branch putting
  `config.load()` on that path.  Not a merge blocker; fix with #90 if not before.
- [ ] **A bad config under `$PREGDOS_CONFIG` fails as a traceback, not as a message.**
  `main()` catches `ConfigError` and turns it into a clean `parser.error`, but only for
  `--config`: with the environment variable set, the import-time `_apply_config()` has
  already failed by the time `main()` runs, so the site sees a stack trace whose last line
  happens to carry the real message.  The systemd unit sets `PREGDOS_CONFIG`, so this is the
  path a real deployment takes.  Same root as the item above -- config is read at import.
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
- [x] **`4.2.p3` sits exactly on the `MINIMUM_TOPAS = (4, 2, 3)` floor.**  It parses correctly
  and passes, with zero margin.  Confirm this is really the build we want validated against
  before the branch merges.
  Confirmed at the site 2026-09-09: `module load opentopas/4.2` gives
  `/TopasDCPT/topas/opentopas_4.2.p3/bin/topas`, whose `--version` prints exactly `4.2.p3`
  on stdout, which parses to (4, 2, 3) and clears the floor.  Zero margin is a deliberate
  acceptance, not an oversight: 4.2.p3 IS the fixed build for #49.  Any site running an
  older module fails the check, which is the intent.
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
