# PREGDOS
A Tool for calculating dose to fetus in proton therapy.
Still under development.

## Developer notes
GUI:
- upload study
- chose beam model (optional: upload new .csv model?)
- convert to topas
- run topas for every field
- post-process
- display dose and effective dose for structures

Docker container:
- Geant4 + topas installation
- SLURM installation, ready configures for running topas
- pull fixed release from https://github.com/nbassler/dicomexport
- webserver (flask?)

Workflow:
- in the beginning commits to main are ok
- once stuff begins working, only by reviewed and tested PRs.


### Getting started:


## Acknowledgements
This work is part of the SONORA project, which has received funding from the European Union’s EURATOM research and innovation programme under grant agreement No 101061037 (PIANOFORTE – European Partnership for Radiation Protection Research).
