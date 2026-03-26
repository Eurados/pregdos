# ── Build stage: compile SLURM without systemd ────────────────────────────────
FROM debian:bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive
ARG SLURM_VERSION=25.11.4

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential wget ca-certificates \
    libmunge-dev munge \
    libssl-dev libpam-dev libhwloc-dev libnuma-dev \
    libcurl4-openssl-dev \
    libyaml-dev libjson-c-dev \
    libhttp-parser-dev \
    python3 perl \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q https://download.schedmd.com/slurm/slurm-${SLURM_VERSION}.tar.bz2 && \
    tar xjf slurm-${SLURM_VERSION}.tar.bz2 && \
    cd slurm-${SLURM_VERSION} && \
    ./configure \
        --without-systemd \
        --prefix=/usr \
        --sysconfdir=/etc/slurm \
        --with-munge \
        --enable-pam \
        --disable-x11 \
        && \
    make -j$(nproc) && \
    make DESTDIR=/build install && \
    cd .. && rm -rf slurm-${SLURM_VERSION} slurm-${SLURM_VERSION}.tar.bz2


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server sudo \
    munge libmunge2 \
    libssl3 libpam-runtime libhwloc15 libnuma1 \
    libcurl4 libyaml-0-2 libjson-c5 libhttp-parser2.9 \
    curl ca-certificates nano \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled SLURM from builder
COPY --from=builder /build /

# Create users
RUN useradd -m -s /bin/bash user && \
    echo "user:user" | chpasswd && \
    echo "user ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers && \
    useradd -r -m -d /home/slurm -s /bin/bash slurm

# Configure SSH
RUN mkdir -p /var/run/sshd && \
    echo "PermitRootLogin prohibit-password" >> /etc/ssh/sshd_config && \
    echo "PasswordAuthentication yes" >> /etc/ssh/sshd_config

# Configure Munge
RUN install -d -m 0755 -o munge -g munge /var/run/munge && \
    install -d -m 0700 -o munge -g munge /etc/munge && \
    install -d -m 0700 -o munge -g munge /var/log/munge

# Configure SLURM directories
RUN install -d -o slurm -g slurm -m 0755 /var/spool/slurm && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/ctld && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/d && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurmd && \
    install -d -o slurm -g slurm -m 0755 /var/log/slurm

COPY docker/slurm/slurm.conf /etc/slurm/slurm.conf
COPY docker/slurm/cgroup.conf /etc/slurm/cgroup.conf
COPY docker/slurm/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 22

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]
