# OpenTOPAS v4.2.3 on Geant4 11.3.2 — Qt6
#
# Build from project root:
#   docker build -t pregdos-base-opentopas-v4.2.3 -f docker/opentopas/4.2.3/Dockerfile .
#
# Override versions:
#   docker build -t pregdos-base-opentopas-v4.2.3 -f docker/opentopas/4.2.3/Dockerfile . \
#       --build-arg GEANT4_VERSION=11.3.2 \
#       --build-arg OPENTOPAS_REF=v4.2.3

FROM debian:trixie
SHELL ["/bin/bash", "-c"]

ARG GEANT4_VERSION=11.3.2
ARG OPENTOPAS_REF=v4.2.3
ARG TOPAS_VERSION=4.2.3
ENV GEANT4_VERSION=${GEANT4_VERSION}
ENV TOPAS_VERSION=${TOPAS_VERSION}
ENV DEBIAN_FRONTEND=noninteractive
ENV APPDIR=/opt
ENV G4DATA=${APPDIR}/G4DATA
ENV TOPAS_G4_DATA_DIR=${G4DATA}
ENV Geant4_DIR=${APPDIR}/GEANT4/geant4-install
ENV GDCM_DIR=${APPDIR}/GDCM/gdcm-install
ENV QT_QPA_PLATFORM_PLUGIN_PATH=${APPDIR}/TOPAS/OpenTOPAS-install/Frameworks

# Create a non-root user
RUN useradd -m user
ENV HOME=/home/user

# Install dependencies (Qt6 required for OpenTOPAS >= v4.2)
RUN apt-get update && \
    apt-get install -y \
    libexpat1-dev \
    libgl1-mesa-dev \
    libglu1-mesa-dev \
    libxt-dev \
    xorg-dev \
    build-essential \
    libharfbuzz-dev \
    cmake \
    git-all \
    wget \
    qt6-base-dev \
    qt6-base-dev-tools \
    qt6-tools-dev \
    qt6-tools-dev-tools \
    libqt6opengl6-dev \
    nano \
    && rm -rf /var/lib/apt/lists/*

# Create directories
RUN mkdir -p ${APPDIR}/G4DATA \
    && mkdir -p ${APPDIR}/GEANT4 \
    && mkdir -p ${APPDIR}/TOPAS \
    && mkdir -p ${APPDIR}/GDCM

# Download and install Geant4
RUN cd ${APPDIR}/GEANT4 && \
    wget -q https://gitlab.cern.ch/geant4/geant4/-/archive/v${GEANT4_VERSION}/geant4-v${GEANT4_VERSION}.tar.gz && \
    tar -zxf geant4-v${GEANT4_VERSION}.tar.gz && \
    rm geant4-v${GEANT4_VERSION}.tar.gz && \
    mkdir geant4-{build,install} && \
    cd geant4-build && \
    cmake ../geant4-v${GEANT4_VERSION} \
    -DGEANT4_INSTALL_DATA=OFF \
    -DGEANT4_BUILD_MULTITHREADED=ON \
    -DGEANT4_BUILD_VERBOSE_CODE=OFF \
    -DCMAKE_INSTALL_PREFIX=../geant4-install \
    -DCMAKE_PREFIX_PATH=/usr/lib/qt6 \
    -DGEANT4_USE_QT=ON \
    -DGEANT4_USE_QT_QT6=ON && \
    make -j$(nproc) install && \
    rm -rf ../geant4-build ../geant4-v${GEANT4_VERSION}

# Download Geant4 datasets (versions matching Geant4 11.3.2)
RUN cd ${G4DATA} && \
    wget -q https://cern.ch/geant4-data/datasets/G4NDL.4.7.1.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4EMLOW.8.6.1.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4PhotonEvaporation.6.1.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4RadioactiveDecay.6.1.2.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4PARTICLEXS.4.1.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4ABLA.3.3.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4INCL.1.2.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4ENSDFSTATE.3.0.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4CHANNELING.1.0.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4NUDEXLIB.1.0.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4URRPT.1.1.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4PII.1.3.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4RealSurface.2.2.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4SAIDDATA.2.0.tar.gz && \
    wget -q https://cern.ch/geant4-data/datasets/G4TENDL.1.4.tar.gz && \
    for file in *.tar.gz; do tar -zxf "$file"; done && \
    rm -f *.tar.gz

# Clone and install TOPAS
RUN cd ${APPDIR}/TOPAS && \
    git clone --branch ${OPENTOPAS_REF} --depth 1 https://github.com/OpenTOPAS/OpenTOPAS.git && \
    cd OpenTOPAS && \
    mv gdcm-2.6.8.tar.gz ../../GDCM && \
    cd ../../GDCM && \
    tar -zxf gdcm-2.6.8.tar.gz && \
    rm gdcm-2.6.8.tar.gz && \
    mkdir gdcm-{build,install} && \
    cd gdcm-build && \
    cmake ../gdcm-2.6.8 \
    -DGDCM_BUILD_SHARED_LIBS=ON \
    -DGDCM_BUILD_DOCBOOK_MANPAGES:BOOL=OFF \
    -DCMAKE_INSTALL_PREFIX=../gdcm-install && \
    make -j$(nproc) install && \
    cd ${APPDIR}/TOPAS && \
    mkdir OpenTOPAS-{build,install} && \
    cd OpenTOPAS-build && \
    cmake ../OpenTOPAS \
    -DCMAKE_INSTALL_PREFIX=../OpenTOPAS-install \
    -DGeant4_DIR=${APPDIR}/GEANT4/geant4-install/lib/Geant4-${GEANT4_VERSION} \
    -DGDCM_DIR=${APPDIR}/GDCM/gdcm-install \
    -DTOPAS_USE_QT=ON \
    -DTOPAS_USE_QT6=ON && \
    make -j$(nproc) install && \
    rm -rf ../OpenTOPAS-build

ENV LD_LIBRARY_PATH=${APPDIR}/TOPAS/OpenTOPAS-install/lib:${APPDIR}/GEANT4/geant4-install/lib
ENV PATH=${APPDIR}/TOPAS/OpenTOPAS-install/bin:${PATH}

USER user

# Set environment variables in bashrc
RUN echo "export Geant4_DIR=$Geant4_DIR" >> ${HOME}/.bashrc && \
    echo "export GDCM_DIR=$GDCM_DIR" >> ${HOME}/.bashrc && \
    echo "export TOPAS_G4_DATA_DIR=$TOPAS_G4_DATA_DIR" >> ${HOME}/.bashrc && \
    echo "export TOPAS_VERSION=$TOPAS_VERSION" >> ${HOME}/.bashrc && \
    echo "export QT_QPA_PLATFORM_PLUGIN_PATH=$QT_QPA_PLATFORM_PLUGIN_PATH" >> ${HOME}/.bashrc

WORKDIR ${HOME}
