    #!/bin/bash'

    export QT_QPA_PLATFORM_PLUGIN_PATH=$HOME/Applications/TOPAS/OpenTOPAS-install/Frameworks
    export TOPAS_G4_DATA_DIR=/opt/G4DATA
    export LD_LIBRARY_PATH=$HOME/Applications/TOPAS/OpenTOPAS-install/lib:$LD_LIBRARY_PATH
    export LD_LIBRARY_PATH=$HOME/Applications/GEANT4/geant4-install/lib:$LD_LIBRARY_PATH


    export PATH=$HOME/Applications/TOPAS/OpenTOPAS-install/bin/topas:$PATH