# SLURM Docker Image

Single-node SLURM cluster compiled from source without systemd, so it runs
in a plain Docker container with no `--privileged` flag required.

## Build

Must be run from the **project root** (not from this directory), so the
COPY paths in the Dockerfile resolve correctly:

```bash
docker build -t pregdos-slurm -f docker/slurm/Dockerfile .
```

## Run

```bash
docker run --rm -it --hostname localhost pregdos-slurm
```

## Submitting jobs

Jobs must be submitted as the `slurm` user, not root. slurmd runs as
`SlurmdUser=slurm` and cannot fork steps as root.

```bash
su slurm -s /bin/bash -c 'sbatch -o /tmp/slurm-%j.out --wrap="hostname"'
```

Or switch to the slurm user first:

```bash
su - slurm
sbatch -o ~/slurm-%j.out --wrap="hostname"
squeue
```

## Quick smoke test

```bash
su - slurm
sbatch --wrap="sleep 5 && echo done" && squeue
# job should appear as R (running) within a few seconds
watch -n2 squeue
```

## Debugging

```bash
# Check daemon status
sinfo
squeue

# Run slurmctld in foreground with verbose logging
slurmctld -D -vvvv

# Run slurmd in foreground with verbose logging (must be as slurm user)
runuser -u slurm -- slurmd -D -vvvv

# Restart slurmctld
slurmctld
```

## Key design decisions

- **Compiled from source** with `--without-systemd`: the Debian-packaged SLURM
  (22.05 and 24.11 tested) requires a running systemd/dbus to create cgroup
  scopes for slurmstepd. Compiling with `--without-systemd` removes this
  dependency entirely.
- **cgroup/v1** configured in `cgroup.conf`: the cgroup/v2 plugin is not built
  when `--without-systemd` is used. cgroup/v1 namespaces (cpuset, memory) are
  not mounted in the container so cgroup constraints are disabled, but SLURM
  functions correctly for job scheduling and execution.
- **Resource reservation**: 1 CPU and 10% of RAM are reserved for the OS at
  startup. Adjust the values in `entrypoint.sh` if needed.
- **No `--privileged`** required: all cgroup constraint options are disabled in
  `cgroup.conf`, so no special container capabilities are needed.