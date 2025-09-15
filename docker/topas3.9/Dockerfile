# STAGE 1 Builder stage using slim image
FROM debian:bullseye-slim as builder

ENV INSTALL_PATH=/opt

# Install build dependencies
RUN apt-get update -y && apt-get upgrade -y && \
    apt-get install -y wget zip cmake g++ patch libexpat1-dev libx11-dev libharfbuzz-dev \
    libglu1-mesa-dev libxt-dev libgl1-mesa-dev qtbase5-dev libqt5opengl5-dev libxmu-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*


############## Geant4 DATA #################
WORKDIR $INSTALL_PATH/G4Data

# Download and extract Geant4 datasets
RUN wget -4 https://geant4-data.web.cern.ch/geant4-data/datasets/G4NDL.4.6.tar.gz \
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
    for d in $(ls G4*); do tar -zxf $d; done; \
    rm *.tar.gz


# Set working directory for GDCM
WORKDIR $INSTALL_PATH/gdcm
# Download and extract GDCM, then build and install
RUN wget https://github.com/malaterre/GDCM/archive/refs/tags/v3.0.23.tar.gz && \
    tar -zxf v3.0.23.tar.gz && \
    mkdir build && \
    cd build && \
    cmake ../GDCM-3.0.23/ -DGDCM_BUILD_SHARED_LIBS=ON -DGDCM_WRAP_PYTHON=OFF -DGDCM_WRAP_CSHARP=OFF -DGDCM_WRAP_JAVA=OFF -DGDCM_WRAP_PHP=OFF -DGDCM_USE_VTK=OFF -DGDCM_BUILD_APPLICATIONS=ON -DGDCM_BUILD_TESTING=OFF -DGDCM_DOCUMENTATION=OFF -DGDCM_BUILD_EXAMPLES=OFF -DGDCM_BUILD_DOCBOOK_MANPAGES=OFF -DCMAKE_INSTALL_PREFIX=$INSTALL_PATH/gdcm/gdcm-install && \
    make -j$(nproc) && \
    make install && \
    rm -rf $INSTALL_PATH/gdcm/GDCM-3.0.23 $INSTALL_PATH/gdcm/v3.0.23.tar.gz $INSTALL_PATH/gdcm/build

# Prepare for Geant4 and TOPAS 3.9 installation
# Download and setup TOPAS 3.9
WORKDIR $INSTALL_PATH/Topas
RUN wget -O topas.zip 'https://drive.google.com/uc?id=1iTG8iuwVyT-rmR7s88AlGWmAQ5h1MH-a&export=download' && \
    unzip topas.zip && \
    rm -rf __MACOSX

# Download and install Geant4
WORKDIR $INSTALL_PATH/Geant4
RUN wget https://geant4-data.web.cern.ch/geant4-data/releases/geant4.10.07.p03.tar.gz && \
    tar xfz geant4.10.07.p03.tar.gz && \
    patch -p0 < $INSTALL_PATH/Topas/topas_3_9/patches/geant4_10_07_p03.patch && \
    mkdir build && \
    cd build && \
    cmake ../geant4.10.07.p03 -DGEANT4_INSTALL_DATA=OFF -DGEANT4_BUILD_MULTITHREADED=ON -DCMAKE_BUILD_TYPE=Release -DGEANT4_USE_OPENGL_X11=ON -DGEANT4_USE_RAYTRACER_X11=ON -DCMAKE_INSTALL_PREFIX=$INSTALL_PATH/Geant4/geant4.10.07.p03-install -DGEANT4_USE_QT=ON && \
    make -j$(nproc) && \
    make install && \
    rm -rf $INSTALL_PATH/Geant4/geant4.10.07.p03.tar.gz $INSTALL_PATH/Geant4/geant4.10.07.p03 $INSTALL_PATH/Geant4/build

# Build and install TOPAS 3.9
WORKDIR $INSTALL_PATH/Topas/
ENV Geant4_DIR=$INSTALL_PATH/Geant4/geant4.10.07.p03-install
ENV GDCM_DIR=$INSTALL_PATH/gdcm/gdcm-install
RUN mkdir build && \
    cd build && \
    cmake ../topas_3_9 -DTOPAS_TYPE=public -DCMAKE_INSTALL_PREFIX=$INSTALL_PATH/Topas/topas-install && \
    make -j$(nproc) && \
    make install && \
    rm -rf $INSTALL_PATH/Topas/topas.zip $INSTALL_PATH/Topas/topas_3_9 $INSTALL_PATH/Topas/build

# Stage 2: Final stage for runtime
FROM debian:bullseye-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y libqt5widgets5 libqt5opengl5 libxmu6 && apt-get clean && rm -rf /var/lib/apt/lists/*

ENV INSTALL_PATH=/opt
# Copy only the necessary binaries/libraries from the builder stage
COPY --from=builder $INSTALL_PATH/gdcm/gdcm-install $INSTALL_PATH/gdcm/gdcm-install
VOLUME /opt/G4Data
COPY --from=builder $INSTALL_PATH/Geant4/geant4.10.07.p03-install $INSTALL_PATH/Geant4/geant4.10.07.p03-install
COPY --from=builder $INSTALL_PATH/Topas/topas-install $INSTALL_PATH/Topas/topas-install

# Set environment variables and default command
ENV PATH=$PATH:$INSTALL_PATH/Topas/topas-install/bin
ENV TOPAS_G4_DATA_DIR=$INSTALL_PATH/G4Data

# TODO: installation of pregdos tools

CMD ["/bin/bash"]
