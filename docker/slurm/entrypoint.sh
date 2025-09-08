#!/bin/bash

sudo sed -i "s/__NPROC__/$(nproc)/g" /etc/slurm/slurm.conf

sudo service munge start
sudo service slurmctld start
sudo service slurmd start
sudo service ssh start

exec /bin/bash

# tail -f /dev/null
