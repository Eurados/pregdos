# Use Debian 13 (trixie) as the base image
FROM debian:13

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV APPDIR=/opt
ENV G4DATA=${APPDIR}/G4DATA
ENV TOPAS_G4_DATA_DIR=${G4DATA}
ENV Geant4_DIR=${APPDIR}/GEANT4/geant4-install
ENV GDCM_DIR=${APPDIR}/GDCM/gdcm-install
ENV QT_QPA_PLATFORM_PLUGIN_PATH=${APPDIR}/TOPAS/OpenTOPAS-install/Frameworks



# Create a non-root user
RUN useradd -m user

# Install dependencies
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
    qtbase5-dev \
    qtchooser \
    qt5-qmake \
    qtbase5-dev-tools \
    nano \
    && rm -rf /var/lib/apt/lists/*

# Create directories
RUN mkdir -p ${APPDIR}/G4DATA \
    && mkdir -p ${APPDIR}/GEANT4 \
    && mkdir -p ${APPDIR}/TOPAS \
    && mkdir -p ${APPDIR}/GDCM

# Download and install Geant4
RUN cd ${APPDIR}/GEANT4 && \
    wget https://gitlab.cern.ch/geant4/geant4/-/archive/v11.1.3/geant4-v11.1.3.tar.gz && \
    tar -zxf geant4-v11.1.3.tar.gz && \
    rm -rf geant4-install geant4-build && \
    mkdir geant4-{build,install} && \
    cd geant4-build && \
    cmake ../geant4-v11.1.3 \
    -DGEANT4_INSTALL_DATA=OFF \
    -DGEANT4_BUILD_MULTITHREADED=ON \
    -DCMAKE_INSTALL_PREFIX=../geant4-install \
    -DCMAKE_PREFIX_PATH=/usr/lib/qt5 \
    -DGEANT4_USE_QT=ON \
    -DGEANT4_USE_OPENGL_X11=ON \
    -DGEANT4_USE_RAYTRACER_X11=ON && \
    make -j20 install

# Download Geant4 datasets
RUN cd ${G4DATA} && \
    wget https://cern.ch/geant4-data/datasets/G4NDL.4.7.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4EMLOW.8.2.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4PhotonEvaporation.5.7.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4RadioactiveDecay.5.6.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4PARTICLEXS.4.0.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4PII.1.3.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4RealSurface.2.2.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4SAIDDATA.2.0.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4ABLA.3.1.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4INCL.1.0.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4ENSDFSTATE.2.3.tar.gz && \
    wget https://cern.ch/geant4-data/datasets/G4TENDL.1.4.tar.gz && \
    wget ftp://gdo-nuclear.ucllnl.org/LEND_GND1.3/LEND_GND1.3_ENDF.BVII.1.tar.gz && \
    for file in *.tar.gz; do tar -zxf "$file"; done && \
    rm -f *.tar.gz

# Clone and install TOPAS
RUN cd ${APPDIR}/TOPAS && \
    git clone https://github.com/OpenTOPAS/OpenTOPAS.git && \
    cd OpenTOPAS && \
    mv gdcm-2.6.8.tar.gz ../../GDCM && \
    cd ../../GDCM && \
    tar -zxf gdcm-2.6.8.tar.gz && \
    rm -rf gdcm-install gdcm-build && \
    mkdir gdcm-{build,install} && \
    cd gdcm-build && \
    cmake ../gdcm-2.6.8 \
    -DGDCM_BUILD_SHARED_LIBS=ON \
    -DGDCM_BUILD_DOCBOOK_MANPAGES:BOOL=OFF \
    -DCMAKE_INSTALL_PREFIX=../gdcm-install && \
    make -j20 install && \
    cd ../TOPAS && \
    rm -rf OpenTOPAS-install OpenTOPAS-build && \
    mkdir OpenTOPAS-{build,install} && \
    cd OpenTOPAS-build && \
    cmake ../OpenTOPAS \
    -DCMAKE_INSTALL_PREFIX=../OpenTOPAS-install && \
    make -j20 install

ENV LD_LIBRARY_PATH=${APPDIR}/TOPAS/OpenTOPAS-install/lib:$LD_LIBRARY_PATH
ENV LD_LIBRARY_PATH=${APPDIR}/GEANT4/geant4-install/lib:$LD_LIBRARY_PATH

# Install Munge and Slurm
RUN apt-get update && \
    apt-get install -y slurm-wlm slurmctld slurmd munge libmunge2 curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Configure Munge
RUN install -d -m 0755 -o munge -g munge /var/run/munge && \
    install -d -m 0700 -o munge -g munge /etc/munge && \
    install -d -m 0700 -o munge -g munge /var/log/munge && \
    usermod -a -G munge slurm

# Configure Slurm
RUN install -d -o slurm -g slurm -m 0755 /var/spool/slurm/ctld && \
    install -d -o slurm -g slurm -m 0755 /var/spool/slurm/d

COPY ../../res/config/slurm.conf /etc/slurm/slurm.conf
COPY ../../res/config/cgroup.conf /etc/slurm/cgroup.conf

USER user

# Set environment variables in bashrc
RUN echo "export Geant4_DIR=$Geant4_DIR" >> ${HOME}/.bashrc && \
    echo "export GDCM_DIR=$GDCM_DIR" >> ${HOME}/.bashrc && \
    echo "export TOPAS_G4_DATA_DIR=$TOPAS_G4_DATA_DIR" >> ${HOME}/.bashrc && \
    echo "export QT_QPA_PLATFORM_PLUGIN_PATH=$QT_QPA_PLATFORM_PLUGIN_PATH" >> ${HOME}/.bashrc

# Set the working directory
WORKDIR ${HOME}
