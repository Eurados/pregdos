docker build -t debian-trixie-ssh-minimal .




docker run -it --rm \
    --privileged \
    --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    -p 2222:22 \
    debian-trixie-ssh-minimal
