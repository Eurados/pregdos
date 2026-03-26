# PregDos — combined production image
#
# Reuses a pre-built OpenTOPAS image rather than recompiling Geant4/TOPAS.
# Build the OpenTOPAS image first, then build this one.
#
# Build stages:
#   topas-builder  — the pre-built opentopas image (no compilation here)
#   slurm-builder  — compiles SLURM without systemd
#   runtime        — lean final image combining both + webserver
#
# Useful build args:
#   OPENTOPAS_IMAGE   default pregdos-opentopas-v4.0.0
#   SLURM_VERSION     default 25.11.4
#   PREGDOS_VERSION   default 0.0.0  (passed to setuptools-scm)
#
# Build from project root:
#   docker build -t pregdos -f docker/pregdos/Dockerfile .
#
# Switch to OpenTOPAS v4.2.3:
#   docker build -t pregdos --build-arg OPENTOPAS_IMAGE=pregdos-opentopas-v4.2.3 \
#       -f docker/pregdos/Dockerfile .
#
# NOTE: when switching to v4.2.3 the runtime Qt libraries below must be
#       changed from Qt5 to Qt6 (see TODO comment in the runtime stage).

# ── Stage 1: OpenTOPAS (pre-built, no compilation) ────────────────────────────
ARG OPENTOPAS_IMAGE=pregdos-opentopas-v4.0.0
FROM ${OPENTOPAS_IMAGE} AS topas-builder


# ── Stage 2: SLURM (independent) ─────────────────────────────────────────────
FROM debian:trixie AS slurm-builder

ARG SLURM_VERSION=25.11.4
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential wget ca-certificates \
    libmunge-dev munge \
    libssl-dev libpam-dev libhwloc-dev libnuma-dev \
    libcurl4-openssl-dev libyaml-dev libjson-c-dev libhttp-parser-dev \
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
        --disable-x11 && \
    make -j$(nproc) && \
    make DESTDIR=/build install && \
    cd .. && rm -rf slurm-${SLURM_VERSION} slurm-${SLURM_VERSION}.tar.bz2


# ── Stage 3: Runtime ──────────────────────────────────────────────────────────
FROM debian:trixie

ENV DEBIAN_FRONTEND=noninteractive
ENV APPDIR=/opt
ENV TOPAS_G4_DATA_DIR=${APPDIR}/G4DATA
ENV Geant4_DIR=${APPDIR}/GEANT4/geant4-install
ENV GDCM_DIR=${APPDIR}/GDCM/gdcm-install
ENV LD_LIBRARY_PATH=${APPDIR}/TOPAS/OpenTOPAS-install/lib:${APPDIR}/GEANT4/geant4-install/lib
ENV PATH=${APPDIR}/TOPAS/OpenTOPAS-install/bin:${PATH}

# Runtime deps for TOPAS + SLURM + webserver
# TODO: trim this list once we know exactly what's needed
# TODO: switch Qt5 → Qt6 libs when using OPENTOPAS_IMAGE=pregdos-opentopas-v4.2.3
RUN apt-get update && apt-get install -y --no-install-recommends \
    # TOPAS/Geant4 runtime (Qt5 — matches pregdos-opentopas-v4.0.0)
    libexpat1 libgl1 libglu1-mesa libxt6 libxmu6 \
    libqt5core5a libqt5gui5 libqt5opengl5 libqt5widgets5 \
    # SLURM runtime
    munge libmunge2 libssl3 libpam-runtime libhwloc15 libnuma1 \
    libcurl4 libyaml-0-2 libjson-c5 libhttp-parser2.9 \
    # webserver (git needed for dicomexport pip install)
    python3 python3-pip python3-venv git \
    # misc
    sudo curl ca-certificates nano \
    && rm -rf /var/lib/apt/lists/*

# Copy TOPAS + Geant4 from pre-built opentopas image
COPY --from=topas-builder ${APPDIR}/GEANT4/geant4-install ${APPDIR}/GEANT4/geant4-install
COPY --from=topas-builder ${APPDIR}/G4DATA              ${APPDIR}/G4DATA
COPY --from=topas-builder ${APPDIR}/TOPAS/OpenTOPAS-install ${APPDIR}/TOPAS/OpenTOPAS-install
COPY --from=topas-builder ${APPDIR}/GDCM/gdcm-install   ${APPDIR}/GDCM/gdcm-install

# Copy SLURM from builder
COPY --from=slurm-builder /build /

# Users
RUN useradd -m -s /bin/bash user && \
    echo "user:user" | chpasswd && \
    echo "user ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers && \
    useradd -r -m -d /home/slurm -s /bin/bash slurm

# TODO: re-enable openssh-server here for development debugging (see TODO.md)

# Munge + SLURM directories
RUN install -d -m 0755 -o munge -g munge /var/run/munge && \
    install -d -m 0700 -o munge -g munge /etc/munge && \
    install -d -m 0700 -o munge -g munge /var/log/munge && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/ctld && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/d && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurmd && \
    install -d -o slurm -g slurm -m 0755 /var/log/slurm && \
    install -d -o slurm -g slurm -m 0755 /home/slurm/jobs

# SLURM config
COPY docker/pregdos/slurm.conf /etc/slurm/slurm.conf
COPY docker/pregdos/cgroup.conf /etc/slurm/cgroup.conf

# Install pregdos webserver into a venv
# .git is excluded via .dockerignore; pass a pretend version for setuptools-scm
COPY . /app
ARG PREGDOS_VERSION=0.0.0
RUN python3 -m venv /app/venv && \
    SETUPTOOLS_SCM_PRETEND_VERSION=${PREGDOS_VERSION} \
    /app/venv/bin/pip install --no-cache-dir /app
ENV PATH=/app/venv/bin:${PATH}

COPY docker/pregdos/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 5000 22

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]