# PREGDOS
A Tool for calculating dose to fetus in proton therapy.
Still under development.

## Developer notes
### Getting started:

- Clone the repository.
- Use VSCode, and open the repository folder
- open the pregdos/main.py file and setup a venv in the terminal
- run `pip install -e .` Say yes to install all options, when prompted.

You are then ready to convert dicom files to topas input scripts.
Example:

The test directory `res/test_studies/DCPT_headphantom/`has a set of CT files, a RS structure file, a RN plan file with 3 fields in it.
You need also so specify a beam model, optionally also at what distance it is defined in mm.
Finally you need to point to a Stopping power ratio to material table.

```bash
PYTHONPATH=. python3 pregdos/main.py -v -b=res/beam_models/DCPT_beam_model__v2.csv -p 500.0 -s=res/spr_tables/SPRtoMaterial__Brain.txt res/test_studies/DCPT_headphantom/
```
which will produce three topas files, ready to run:

```
(.venv) bassler@altair:~/Projects/pregdos$ PYTHONPATH=. python3 pregdos/main.py -v -b=res/beam_models/DCPT_beam_model__v2.csv -p 500.0 -s
res/spr_tables/SPRtoMaterial__Brain.txt res/test_studies/DCPT_headphantom/
INFO:pregdos.import_rtstruct:Using RTSTRUCT file: RS.1.2.246.352.205.5439556202947041733.367077883804944283.dcm
INFO:pregdos.import_rtstruct:Imported RTSTRUCT: DCPT_headphantom with 11 ROIs
WARNING:__main__:Multiple DICOM RTDOSE files found, using the first one.
INFO:pregdos.export_study_topas:Wrote Topas geometry file for field 1: /home/bassler/Projects/pregdos/topas_field1.txt
INFO:pregdos.export_study_topas:Wrote Topas geometry file for field 2: /home/bassler/Projects/pregdos/topas_field2.txt
INFO:pregdos.export_study_topas:Wrote Topas geometry file for field 3: /home/bassler/Projects/pregdos/topas_field3.txt
```


## Acknowledgements
This work is part of the SONORA project, which has received funding from the European Union’s EURATOM research and innovation programme under grant agreement No 101061037 (PIANOFORTE – European Partnership for Radiation Protection Research).
