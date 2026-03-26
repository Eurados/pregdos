# PregDos — Combined Production Image

Single Docker container running the full PregDos pipeline:
- **OpenTOPAS** (Geant4 + TOPAS Monte Carlo simulation)
- **SLURM** (job scheduling, compiled without systemd)
- **Pregdos webserver** (Flask frontend)

## Build

From the project root:

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile .
```

Override versions via build args:

```bash
docker build -t pregdos -f docker/pregdos/Dockerfile . \
    --build-arg GEANT4_VERSION=11.2.0 \
    --build-arg OPENTOPAS_REF=main \
    --build-arg SLURM_VERSION=25.11.4
```

> **Warning:** Geant4 takes ~25 minutes to compile. Subsequent builds use
> the Docker layer cache unless `GEANT4_VERSION` changes.

## Run

```bash
docker run --rm -it --hostname localhost -p 5000:5000 pregdos
```

## Submit jobs

Jobs must be submitted as the `slurm` user:

```bash
su - slurm
sbatch --wrap="topas my_simulation.txt"
squeue
```

## TODO

- [ ] Fix `Geant4_DIR` cmake path — currently hardcodes `Geant4-11.1.3`
      subfolder; should derive from `GEANT4_VERSION` build arg
- [ ] Install pregdos webserver in runtime stage (`pip install .`)
- [ ] Start Flask in entrypoint.sh
- [ ] Wire webserver job submission to `sbatch`
- [ ] Add job status page (query `squeue`/`sacct`)
- [ ] Add results/file browser page
- [ ] Trim runtime apt dependencies — current list is conservative
- [ ] Test SLURM on Trixie base (currently using Bookworm for SLURM build)
- [ ] Add post-processing step triggered on SLURM job completion
- [ ] Consider adding a job working directory convention (one dir per submission)