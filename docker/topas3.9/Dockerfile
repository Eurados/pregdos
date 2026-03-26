# TOPAS 3.9 on Geant4 10.07.p03
#
# NOTE: TOPAS 3.9 source is downloaded from Google Drive — the link may break.
# NOTE: Base image kept as debian:bullseye-slim for Geant4/TOPAS 3.9 compatibility.
#
# Build from project root:
#   docker build -t pregdos-topas3.9 -f docker/topas3.9/Dockerfile .

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM debian:bullseye-slim AS builder
SHELL ["/bin/bash", "-c"]

ARG GEANT4_VERSION=10.07.p03
ARG GDCM_VERSION=3.0.23
ENV DEBIAN_FRONTEND=noninteractive
ENV APPDIR=/opt

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget zip cmake g++ patch \
    libexpat1-dev libx11-dev libharfbuzz-dev \
    libglu1-mesa-dev libxt-dev libgl1-mesa-dev \
    qtbase5-dev libqt5opengl5-dev libxmu-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Geant4 datasets
RUN mkdir -p ${APPDIR}/G4DATA && \
    cd ${APPDIR}/G4DATA && \
    wget -q -4 \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4NDL.4.6.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4EMLOW.7.13.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4PhotonEvaporation.5.7.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4RadioactiveDecay.5.6.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4PARTICLEXS.3.1.1.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4SAIDDATA.2.0.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4ABLA.3.1.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4INCL.1.0.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4PII.1.3.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4ENSDFSTATE.2.3.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4RealSurface.2.2.tar.gz \
    https://geant4-data.web.cern.ch/geant4-data/datasets/G4TENDL.1.3.2.tar.gz && \
    for f in *.tar.gz; do tar -zxf "$f"; done && \
    rm *.tar.gz

# GDCM
RUN mkdir -p ${APPDIR}/GDCM && \
    cd ${APPDIR}/GDCM && \
    wget -q https://github.com/malaterre/GDCM/archive/refs/tags/v${GDCM_VERSION}.tar.gz && \
    tar -zxf v${GDCM_VERSION}.tar.gz && \
    mkdir gdcm-{build,install} && \
    cd gdcm-build && \
    cmake ../GDCM-${GDCM_VERSION} \
    -DGDCM_BUILD_SHARED_LIBS=ON \
    -DGDCM_BUILD_DOCBOOK_MANPAGES=OFF \
    -DGDCM_WRAP_PYTHON=OFF -DGDCM_WRAP_CSHARP=OFF \
    -DGDCM_WRAP_JAVA=OFF -DGDCM_WRAP_PHP=OFF \
    -DGDCM_USE_VTK=OFF -DGDCM_BUILD_APPLICATIONS=ON \
    -DGDCM_BUILD_TESTING=OFF -DGDCM_DOCUMENTATION=OFF \
    -DGDCM_BUILD_EXAMPLES=OFF \
    -DCMAKE_INSTALL_PREFIX=../gdcm-install && \
    make -j$(nproc) install && \
    rm -rf ../GDCM-${GDCM_VERSION} ../v${GDCM_VERSION}.tar.gz ../gdcm-build

# TOPAS 3.9 (downloaded from Google Drive — link may break)
RUN mkdir -p ${APPDIR}/TOPAS && \
    cd ${APPDIR}/TOPAS && \
    wget -q -O topas.zip \
    'https://drive.google.com/uc?id=1iTG8iuwVyT-rmR7s88AlGWmAQ5h1MH-a&export=download' && \
    unzip -q topas.zip && \
    rm -rf __MACOSX topas.zip

# Geant4
RUN mkdir -p ${APPDIR}/GEANT4 && \
    cd ${APPDIR}/GEANT4 && \
    wget -q https://geant4-data.web.cern.ch/geant4-data/releases/geant4.${GEANT4_VERSION}.tar.gz && \
    tar -zxf geant4.${GEANT4_VERSION}.tar.gz && \
    patch -p0 < ${APPDIR}/TOPAS/topas_3_9/patches/geant4_10_07_p03.patch && \
    mkdir geant4-{build,install} && \
    cd geant4-build && \
    cmake ../geant4.${GEANT4_VERSION} \
    -DGEANT4_INSTALL_DATA=OFF \
    -DGEANT4_BUILD_MULTITHREADED=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DGEANT4_USE_OPENGL_X11=ON \
    -DGEANT4_USE_RAYTRACER_X11=ON \
    -DGEANT4_USE_QT=ON \
    -DCMAKE_INSTALL_PREFIX=../geant4-install && \
    make -j$(nproc) install && \
    rm -rf ../geant4.${GEANT4_VERSION}.tar.gz ../geant4.${GEANT4_VERSION} ../geant4-build

# TOPAS 3.9 build
RUN cd ${APPDIR}/TOPAS && \
    mkdir topas-{build,install} && \
    cd topas-build && \
    cmake ../topas_3_9 \
    -DTOPAS_TYPE=public \
    -DGeant4_DIR=${APPDIR}/GEANT4/geant4-install \
    -DGDCM_DIR=${APPDIR}/GDCM/gdcm-install \
    -DCMAKE_INSTALL_PREFIX=../topas-install && \
    make -j$(nproc) install && \
    rm -rf ../topas_3_9 ../topas-build


# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM debian:bullseye-slim
SHELL ["/bin/bash", "-c"]

ENV DEBIAN_FRONTEND=noninteractive
ENV APPDIR=/opt
ENV G4DATA=${APPDIR}/G4DATA
ENV TOPAS_G4_DATA_DIR=${G4DATA}
ENV Geant4_DIR=${APPDIR}/GEANT4/geant4-install
ENV GDCM_DIR=${APPDIR}/GDCM/gdcm-install
ENV LD_LIBRARY_PATH=${APPDIR}/TOPAS/topas-install/lib:${APPDIR}/GEANT4/geant4-install/lib
ENV PATH=${APPDIR}/TOPAS/topas-install/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
    libqt5widgets5 libqt5opengl5 libxmu6 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=builder ${APPDIR}/GDCM/gdcm-install    ${APPDIR}/GDCM/gdcm-install
COPY --from=builder ${APPDIR}/G4DATA               ${APPDIR}/G4DATA
COPY --from=builder ${APPDIR}/GEANT4/geant4-install ${APPDIR}/GEANT4/geant4-install
COPY --from=builder ${APPDIR}/TOPAS/topas-install  ${APPDIR}/TOPAS/topas-install

# Create a non-root user
RUN useradd -m user
ENV HOME=/home/user

# Set environment variables in bashrc
USER user
RUN echo "export Geant4_DIR=${Geant4_DIR}" >> ${HOME}/.bashrc && \
    echo "export GDCM_DIR=${GDCM_DIR}" >> ${HOME}/.bashrc && \
    echo "export TOPAS_G4_DATA_DIR=${TOPAS_G4_DATA_DIR}" >> ${HOME}/.bashrc

WORKDIR ${HOME}
CMD ["/bin/bash"]