#!/bin/bash

# Reserve 1 CPU and 10% of RAM for the OS
# NCPUS=$(( $(nproc) - 1 ))
NCPUS=$(( $(nproc) ))
[ "$NCPUS" -lt 1 ] && NCPUS=1
TOTAL_MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SLURM_MEM_MB=$(( TOTAL_MEM_MB * 90 / 100 ))

sed -i "s/__NPROC__/$NCPUS/g" /etc/slurm/slurm.conf
sed -i "s/__REALMEM__/$SLURM_MEM_MB/g" /etc/slurm/slurm.conf

service munge start
slurmctld
runuser -u slurm -- slurmd

# Gunicorn is the foreground process: container signals reach its master, and a server
# failure stops the container instead of leaving a healthy-looking idle shell behind.
exec pregdos-web "$@"
