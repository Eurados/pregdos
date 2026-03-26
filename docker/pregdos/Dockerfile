# PregDos — combined production image
#
# Build stages (longest first for cache efficiency):
#   geant4-builder  — compiles Geant4
#   topas-builder   — compiles OpenTOPAS (depends on Geant4)
#   slurm-builder   — compiles SLURM without systemd (independent)
#   runtime         — lean final image combining all three + webserver
#
# Useful build args:
#   GEANT4_VERSION   default 11.1.3
#   OPENTOPAS_REF    default main  (branch, tag, or commit)
#   SLURM_VERSION    default 25.11.4
#
# Build from project root:
#   docker build -t pregdos -f docker/pregdos/Dockerfile .

# ── Stage 1: Geant4 ───────────────────────────────────────────────────────────
FROM debian:bookworm AS geant4-builder
SHELL ["/bin/bash", "-c"]

ARG GEANT4_VERSION=11.1.3
ENV DEBIAN_FRONTEND=noninteractive
ENV APPDIR=/opt

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake wget ca-certificates \
    libexpat1-dev libgl1-mesa-dev libglu1-mesa-dev libxt-dev xorg-dev \
    libharfbuzz-dev qtbase5-dev qt5-qmake qtbase5-dev-tools \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p ${APPDIR}/GEANT4 ${APPDIR}/G4DATA

RUN cd ${APPDIR}/GEANT4 && \
    wget -q https://gitlab.cern.ch/geant4/geant4/-/archive/v${GEANT4_VERSION}/geant4-v${GEANT4_VERSION}.tar.gz && \
    tar -zxf geant4-v${GEANT4_VERSION}.tar.gz && \
    rm geant4-v${GEANT4_VERSION}.tar.gz && \
    mkdir geant4-build geant4-install && \
    cd geant4-build && \
    cmake ../geant4-v${GEANT4_VERSION} \
        -DGEANT4_INSTALL_DATA=OFF \
        -DGEANT4_BUILD_MULTITHREADED=ON \
        -DCMAKE_INSTALL_PREFIX=../geant4-install \
        -DCMAKE_PREFIX_PATH=/usr/lib/qt5 \
        -DGEANT4_USE_QT=ON \
        -DGEANT4_USE_OPENGL_X11=ON \
        -DGEANT4_USE_RAYTRACER_X11=ON && \
    make -j$(nproc) install && \
    rm -rf ../geant4-build ../geant4-v${GEANT4_VERSION}

# Geant4 datasets
RUN cd ${APPDIR}/G4DATA && \
    wget -q https://cern.ch/geant4-data/datasets/G4NDL.4.7.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4EMLOW.8.2.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4PhotonEvaporation.5.7.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4RadioactiveDecay.5.6.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4PARTICLEXS.4.0.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4PII.1.3.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4RealSurface.2.2.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4SAIDDATA.2.0.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4ABLA.3.1.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4INCL.1.0.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4ENSDFSTATE.2.3.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4TENDL.1.4.tar.gz && \
    for f in *.tar.gz; do tar -zxf "$f"; done && \
    rm -f *.tar.gz


# ── Stage 2: OpenTOPAS ────────────────────────────────────────────────────────
FROM geant4-builder AS topas-builder

ARG OPENTOPAS_REF=v4.0.0
ENV APPDIR=/opt

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p ${APPDIR}/TOPAS ${APPDIR}/GDCM

RUN cd ${APPDIR}/TOPAS && \
    git clone https://github.com/OpenTOPAS/OpenTOPAS.git && \
    cd OpenTOPAS && \
    git checkout ${OPENTOPAS_REF} && \
    mv gdcm-2.6.8.tar.gz ${APPDIR}/GDCM/

RUN cd ${APPDIR}/GDCM && \
    tar -zxf gdcm-2.6.8.tar.gz && \
    rm gdcm-2.6.8.tar.gz && \
    mkdir gdcm-build gdcm-install && \
    cd gdcm-build && \
    cmake ../gdcm-2.6.8 \
        -DGDCM_BUILD_SHARED_LIBS=ON \
        -DGDCM_BUILD_DOCBOOK_MANPAGES=OFF \
        -DCMAKE_INSTALL_PREFIX=../gdcm-install && \
    make -j$(nproc) install && \
    rm -rf ../gdcm-build ../gdcm-2.6.8

RUN mkdir -p ${APPDIR}/TOPAS/OpenTOPAS-build ${APPDIR}/TOPAS/OpenTOPAS-install && \
    cd ${APPDIR}/TOPAS/OpenTOPAS-build && \
    cmake ../OpenTOPAS \
        -DCMAKE_INSTALL_PREFIX=../OpenTOPAS-install \
        -DGeant4_DIR=${APPDIR}/GEANT4/geant4-install/lib/Geant4-11.1.3 \
        -DGDCM_DIR=${APPDIR}/GDCM/gdcm-install && \
    make -j$(nproc) install && \
    rm -rf ../OpenTOPAS-build


# ── Stage 3: SLURM (independent of TOPAS) ────────────────────────────────────
FROM debian:bookworm AS slurm-builder

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


# ── Stage 4: Runtime ──────────────────────────────────────────────────────────
FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV APPDIR=/opt
ENV TOPAS_G4_DATA_DIR=${APPDIR}/G4DATA
ENV Geant4_DIR=${APPDIR}/GEANT4/geant4-install
ENV GDCM_DIR=${APPDIR}/GDCM/gdcm-install
ENV LD_LIBRARY_PATH=${APPDIR}/TOPAS/OpenTOPAS-install/lib:${APPDIR}/GEANT4/geant4-install/lib

# Runtime deps for TOPAS + SLURM + webserver
# TODO: trim this list once we know exactly what's needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    # TOPAS/Geant4 runtime
    libexpat1 libgl1-mesa-glx libglu1-mesa libxt6 \
    libqt5core5a libqt5gui5 libqt5opengl5 libqt5widgets5 \
    # SLURM runtime
    munge libmunge2 libssl3 libpam-runtime libhwloc15 libnuma1 \
    libcurl4 libyaml-0-2 libjson-c5 libhttp-parser2.9 \
    # webserver
    python3 python3-pip \
    # misc
    sudo curl ca-certificates nano \
    && rm -rf /var/lib/apt/lists/*

# Copy TOPAS + Geant4 from builder
COPY --from=topas-builder ${APPDIR}/GEANT4/geant4-install ${APPDIR}/GEANT4/geant4-install
COPY --from=topas-builder ${APPDIR}/G4DATA ${APPDIR}/G4DATA
COPY --from=topas-builder ${APPDIR}/TOPAS/OpenTOPAS-install ${APPDIR}/TOPAS/OpenTOPAS-install
COPY --from=topas-builder ${APPDIR}/GDCM/gdcm-install ${APPDIR}/GDCM/gdcm-install

# Copy SLURM from builder
COPY --from=slurm-builder /build /

# Users
RUN useradd -m -s /bin/bash user && \
    echo "user ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers && \
    useradd -r -m -d /home/slurm -s /bin/bash slurm

# Munge + SLURM directories
RUN install -d -m 0755 -o munge -g munge /var/run/munge && \
    install -d -m 0700 -o munge -g munge /etc/munge && \
    install -d -m 0700 -o munge -g munge /var/log/munge && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/ctld && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/d && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurmd && \
    install -d -o slurm -g slurm -m 0755 /var/log/slurm

# SLURM config
COPY docker/pregdos/slurm.conf /etc/slurm/slurm.conf
COPY docker/pregdos/cgroup.conf /etc/slurm/cgroup.conf

# TODO: install pregdos webserver
# COPY . /app
# RUN pip install /app

COPY docker/pregdos/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 5000 22

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]