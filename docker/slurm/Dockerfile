# Use the smallest Debian Trixie image with systemd/cgroups support
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive


# RUN apt-get update && apt-get install -y --no-install-recommends \
#     systemd systemd-sysv dbus dbus-user-session \
#     openssh-server sudo \
#     slurm-wlm slurmctld slurmd munge libmunge2 \
#     && rm -rf /var/lib/apt/lists/*

# Install SSH server and sudo
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openssh-server \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user with sudo access
RUN useradd -m -s /bin/bash user && \
    echo "user:user" | chpasswd && \
    echo "user ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Enable SSH
RUN mkdir -p /var/run/sshd && \
    echo "PermitRootLogin prohibit-password" >> /etc/ssh/sshd_config && \
    echo "PasswordAuthentication yes" >> /etc/ssh/sshd_config && \
    echo "X11Forwarding yes" >> /etc/ssh/sshd_config

# Expose SSH port
EXPOSE 22

# Install Munge and Slurm
RUN apt-get update && \
    apt-get install -y slurm-wlm slurmctld slurmd munge libmunge2 curl ca-certificates nano && \
    rm -rf /var/lib/apt/lists/*

# Configure Munge
RUN install -d -m 0755 -o munge -g munge /var/run/munge && \
    install -d -m 0700 -o munge -g munge /etc/munge && \
    install -d -m 0700 -o munge -g munge /var/log/munge && \
    usermod -a -G munge slurm

# Configure Slurm
RUN install -d -o slurm -g slurm -m 0755 /var/spool/slurm && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/ctld && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/d && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurmd

COPY slurm.conf /etc/slurm/slurm.conf
COPY cgroup.conf /etc/slurm/cgroup.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh


# RUN systemctl enable munge slurmctld slurmd ssh
ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]

CMD [ "/bin/bash" ]
