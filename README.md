# PREGDOS
A Tool for calculating dose to fetus in proton therapy.
Still under development.

## Developer notes
### Getting started:

- Clone the repository.
- Use VSCode, and open the repository folder
- open the pregdos/main.py file and setup a venv in the terminal
- run `pip install -e .` Say yes to install all options, when prompted.

You are then ready to convert dicom files to topas input scripts. They are partial, and do only address beam generation.
Example:
```bash
PYTHONPATH=. python3 pregdos/main.py -f1 -v -b=res/beam_models/DCPT_beam_model__v2.csv res/plans/temp_160MeV_10x10.dcm foobar.txt
```
which will produce the field foobar_field1.txt


## Acknowledgements
This work is part of the SONORA project, which has received funding from the European Union’s EURATOM research and innovation programme under grant agreement No 101061037 (PIANOFORTE – European Partnership for Radiation Protection Research).
