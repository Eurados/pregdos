# PregDos — TODO / Roadmap

Open work only.  Completed items are removed rather than ticked off — the record of what was
done, and why, lives in the commits and merged PRs, and the reasoning worth keeping long-term
has been moved into the code it explains.

## Configuration and site deployment

Found during the first deployment onto a site test host: RHEL 9, with OpenTOPAS behind an
environment module.  Both share one root: config and environment are read in the web process,
which is not the shell that runs TOPAS, and not after `main()` has had a chance to report a
problem cleanly.

- [ ] **The Geant4 data pre-flight is vacuous at a modules site.**  Same blind spot as the
  since-fixed About-page probe: `g4_data_dir_problem()` reads `TOPAS_G4_DATA_DIR` from the *web
  process* environment, but at a modules site the module sets it inside the prologue shell.  The
  web process sees nothing, the check returns None, and the one pre-flight meant to catch
  "Geant4 aborts seconds after submission" never fires.  It cannot produce a false alarm — it
  simply cannot fire at all — which is why it survived the probe fix.  Read the variable through
  the prologue shell, the way `versions._shell_probe` now does, and check the `G4*DATA`
  directories the module actually sets while there.
- [ ] **A bad config under `$PREGDOS_CONFIG` fails as a traceback, not as a message.**
  `main()` catches `ConfigError` and turns it into a clean `parser.error`, but only for
  `--config`: with the environment variable set, the import-time `_apply_config()` has
  already failed by the time `main()` runs, so the site sees a stack trace whose last line
  happens to carry the real message.  The systemd unit sets `PREGDOS_CONFIG`, so this is the
  path a real deployment takes.  Same root as the item above — config is read at import.

## Webserver

Keep the WebUI minimal: a compact results table and downloads for further analysis.
Preserve this small interface when maintaining results; embedded charts and additional
viewer controls are not planned.

## Docker — combined image (`docker/pregdos/`)

- [ ] Remove `openssh-server` from production image (currently included for development convenience only)
- [ ] Trim runtime apt dependencies — current list is conservative
- [ ] Update Docker image to include new `pregdos/data/ct_to_material/` and `pregdos/data/beam_models/` package data

## Simulation workflow

- [ ] Add post-processing step triggered on SLURM job completion
- [ ] Validate generated TOPAS scorer blocks against Marijke's reference scripts in `_temp/`

## Infrastructure

- [ ] Add GitHub Actions workflow to build and test the combined `docker/pregdos/` image
- [ ] Add GitHub Actions workflow to build and smoke-test the `docker/opentopas/` images

## Known issues

- [ ] Qt OpenGL visualization fails in Docker with X11 forwarding ("failed to create drawable") — missing runtime Mesa/GLX packages. Workaround: use parameter files without visualization.
