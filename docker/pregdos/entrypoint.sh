#!/bin/bash

# Reserve 1 CPU and 10% of RAM for the OS
NCPUS=$(( $(nproc) - 1 ))
[ "$NCPUS" -lt 1 ] && NCPUS=1
TOTAL_MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SLURM_MEM_MB=$(( TOTAL_MEM_MB * 90 / 100 ))

sed -i "s/__NPROC__/$NCPUS/g" /etc/slurm/slurm.conf
sed -i "s/__REALMEM__/$SLURM_MEM_MB/g" /etc/slurm/slurm.conf

service munge start
slurmctld
runuser -u slurm -- slurmd

# TODO: start pregdos webserver
# flask --app pregdos.webserver run --host 0.0.0.0 --port 5000 &

exec "$@"
