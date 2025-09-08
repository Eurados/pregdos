# SLURM Installation Notes

Ubuntu 24.04 LTS        g++ (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0
Debian 13 trixie        g++ (Debian 14.2.0-19) 14.2.0

sudo docker volume create pregdos-opt

```bash
sudo docker run --rm -it --name pregdos-debian \
    -h pregdos-debian \
    -e DEBIAN_FRONTEND=noninteractive \
    -v "$PWD":/work \
    -v pregdos-opt:/opt \
    debian:13 bash
```

```bash
docker run --rm -it --name pregdos-ubuntu \
  -h pregdos-ubuntu \
  -e DEBIAN_FRONTEND=noninteractive \
  -v "$PWD":/work \
  -v pregdos-opt:/opt \
  ubuntu:24.04 bash
```

```bash
apt install -y libexpat1-dev libgl1-mesa-dev libglu1-mesa-dev libxt-dev xorg-dev build-essential libharfbuzz-dev

apt install -y cmake git-all wget

apt install -y qtbase5-dev qtchooser qt5-qmake qtbase5-dev-tools
```

Geant4:

```bash
mkdir -p $HOME/Applications/GEANT4
cd $HOME/Applications/GEANT4

INSTALL_PATH=/opt
mkdir -p $INSTALL_PATH/Geant4

wget https://gitlab.cern.ch/geant4/geant4/-/archive/v11.1.3/geant4-v11.1.3.tar.gz
tar -zxf geant4-v11.1.3.tar.gz

G4DATA=/opt/G4DATA
mkdir -p $G4DATA


wget https://cern.ch/geant4-data/datasets/G4NDL.4.7.tar.gz
wget https://cern.ch/geant4-data/datasets/G4EMLOW.8.2.tar.gz
wget https://cern.ch/geant4-data/datasets/G4PhotonEvaporation.5.7.tar.gz
wget https://cern.ch/geant4-data/datasets/G4RadioactiveDecay.5.6.tar.gz
wget https://cern.ch/geant4-data/datasets/G4PARTICLEXS.4.0.tar.gz
wget https://cern.ch/geant4-data/datasets/G4PII.1.3.tar.gz
wget https://cern.ch/geant4-data/datasets/G4RealSurface.2.2.tar.gz
wget https://cern.ch/geant4-data/datasets/G4SAIDDATA.2.0.tar.gz
wget https://cern.ch/geant4-data/datasets/G4ABLA.3.1.tar.gz
wget https://cern.ch/geant4-data/datasets/G4INCL.1.0.tar.gz
wget https://cern.ch/geant4-data/datasets/G4ENSDFSTATE.2.3.tar.gz
wget https://cern.ch/geant4-data/datasets/G4TENDL.1.4.tar.gz
wget ftp://gdo-nuclear.ucllnl.org/LEND_GND1.3/LEND_GND1.3_ENDF.BVII.1.tar.gz

tar -zxf G4NDL.4.7.tar.gz
tar -zxf G4EMLOW.8.2.tar.gz
tar -zxf G4PhotonEvaporation.5.7.tar.gz
tar -zxf G4RadioactiveDecay.5.6.tar.gz
tar -zxf G4PARTICLEXS.4.0.tar.gz
tar -zxf G4PII.1.3.tar.gz
tar -zxf G4RealSurface.2.2.tar.gz
tar -zxf G4SAIDDATA.2.0.tar.gz
tar -zxf G4ABLA.3.1.tar.gz
tar -zxf G4INCL.1.0.tar.gz
tar -zxf G4ENSDFSTATE.2.3.tar.gz
tar -zxf G4TENDL.1.4.tar.gz
tar -zxf LEND_GND1.3_ENDF.BVII.1.tar.gz


cd $HOME/Applications/GEANT4/
rm -rf geant4-install geant4-build
mkdir geant4-{build,install}
cd geant4-build
cmake ../geant4-v11.1.3 -DGEANT4_INSTALL_DATA=OFF -DGEANT4_BUILD_MULTITHREADED=ON -DCMAKE_INSTALL_PREFIX=../geant4-install -DCMAKE_PREFIX_PATH=/usr/lib/qt5 -DGEANT4_USE_QT=ON -DGEANT4_USE_OPENGL_X11=ON -DGEANT4_USE_RAYTRACER_X11=ON
sudo make -j20 install
```

## TOPAS and GDCM

```bash
mkdir $HOME/Applications/TOPAS
cd $HOME/Applications/TOPAS
git clone https://github.com/OpenTOPAS/OpenTOPAS.git

mkdir $HOME/Applications/GDCM
cd $HOME/Applications/TOPAS/OpenTOPAS
mv gdcm-2.6.8.tar.gz ../../GDCM
cd ../../GDCM
tar -zxf gdcm-2.6.8.tar.gz

rm -rf gdcm-install gdcm-build
mkdir gdcm-{build,install}
cd gdcm-build
cmake ../gdcm-2.6.8 -DGDCM_BUILD_SHARED_LIBS=ON -DGDCM_BUILD_DOCBOOK_MANPAGES:BOOL=OFF  -DCMAKE_INSTALL_PREFIX=../gdcm-install
sudo make -j20 install

cd $HOME/Applications/TOPAS
rm -rf OpenTOPAS-install OpenTOPAS-build
mkdir OpenTOPAS-{build,install}
cd OpenTOPAS-build
export Geant4_DIR=$HOME/Applications/GEANT4/geant4-install
export GDCM_DIR=$HOME/Applications/GDCM/gdcm-install
cmake ../OpenTOPAS -DCMAKE_INSTALL_PREFIX=../OpenTOPAS-install
sudo make -j20 install


    export QT_QPA_PLATFORM_PLUGIN_PATH=$HOME/Applications/TOPAS/OpenTOPAS-install/Frameworks

export TOPAS_G4_DATA_DIR=/opt/G4DATA
```

# MUNGE and SLURM


```bash
apt-get install -y slurm-wlm slurmctld slurmd munge libmunge2 curl ca-certificates

```

munged does not like to work from /var/lib/munge, due to some docker filesystem overlay shenenigans. Therefore this has to be moved to a tempfs dir:

```bash
install -d -m 0755 -o munge -g munge /var/run/munge
su -s /bin/bash munge -c "munged --foreground --verbose" &
```

install -d -o slurm -g slurm -m 0755 /var/spool/slurm/ctld
install -d -o slurm -g slurm -m 0755 /var/spool/slurm/d
install -d -o slurm -g slurm -m 0755 /var/log/slurm
install -d -o slurm -g slurm -m 0755 /var/run/slurm