# Study Directory Layout

PregDos stores each uploaded study under a studies root. The studies root is controlled by
`PREGDOS_WORK_DIR`; if unset, PregDos uses `/var/tmp/pregdos` — persistent across reboots,
and auto-reaped after ~30 days so stale runs do not pile up.

## Layout

```text
<studies_root>/
  <study_name>/
    dicom/
      CT...
      RS...
      RN...
      RD...
    <beam_model>.csv
    <material_table>.txt
    run_<YYYYmmdd_HHMMSS>/
      run.json
      topas_field01.txt
      topas_field01.log
      topas_field01.exit_code
      topas_field01_<scorer>.csv
      structure_mask_prepass.txt
      structure_metrics.json
      rtdose_plan_eclipse_import.zip
```

The exact files depend on selected scorers, executor backend, and whether DICOM RTDOSE export
was requested.

With the in-field scorer kept, each field writes a raw TOPAS dose cube, `topas_field<nn>.dcm`, on
the RTDOSE grid. Since dicomexport 2.0 it holds 32 bits per pixel, so the low out-of-field dose is
not rounded to zero; it is about twice the size of the earlier 16-bit cube, e.g. 61 MB per field
for the head phantom's grid. Size study storage accordingly. Runs from before dicomexport 2.0 named
the cube without zero padding (`topas_field1.dcm`); PregDos reads both.

`<material_table>.txt` is a copy of the table chosen at upload. `dicomexport` pastes its
contents into every generated field and into the mask pre-pass, so TOPAS does not read the file
itself. A table that includes other files still needs those files where TOPAS runs.

At submission, `run.json` records the material conversion table's filename and full SHA-256
hash. Both are taken from the generated field, where `dicomexport` recorded them when it pasted
the table, so they describe exactly what TOPAS reads. CSV and PDF reports carry that as one
field, `filename (12345678)`, where the hash is truncated to its first eight characters. Older
runs without this metadata report the table as unavailable.

`run.json` also records what `dicomexport` converted the run with, read at submission from the
header it writes into each field: the dicomexport version, the configuration directory and how it
was found, the beam model, the range-shifter catalog, every included file with its SHA-256 when
pasted, every material override in order, and the spot mode with its batch count. The reports show
these under Provenance, and give the dicomexport version that converted the run rather than the
one installed when the report is downloaded. Runs converted by dicomexport before 2.0 report them
as unavailable.

## Study Names And Run IDs

Study names are sanitized into a single path component. Re-uploading a study with the same name
does not merge with the old one; PregDos allocates a new name such as `study_2`.

Run directories are named `run_<YYYYmmdd_HHMMSS>`. If two conversions start in the same second,
PregDos appends a numeric suffix.

Routes refer to studies and runs by name, never by arbitrary filesystem paths. Path resolution
is centralized in `pregdos/studies.py` and refuses traversal outside the studies root.

## Generate Where You Execute

This invariant is load-bearing:

> TOPAS inputs must be generated in the same run directory where TOPAS will execute them.

`dicomexport` writes paths directly into the generated TOPAS input, including
`s:Ge/Patient/DicomDirectory`. PregDos therefore runs `dicomexport` with the
run directory as its current working directory and passes paths relative to that directory.
Later, PregDos also runs TOPAS from that same directory.

This keeps the generated run relocatable:

- The study directory can be moved or mounted at a different absolute path.
- Docker and local execution see the same relative layout.
- Generated inputs, logs, and scorer outputs stay together.
- A previous run's output cannot be mistaken for a new run's output.

This is the core fix behind issue #41.

## DICOM Is Kept Separate

Uploaded DICOM files live under `dicom/`, not inside a run directory.

TOPAS's DICOM patient geometry reader scans the configured DICOM directory. If generated TOPAS
inputs, logs, or scorer CSV files were placed inside that DICOM directory, TOPAS could try to
read non-DICOM files as DICOM. Keeping `dicom/` separate makes run directories siblings of the
data they reference.

## Structure Scoring Requirements

PregDos structure scoring uses TOPAS RTSTRUCT support directly:

- scorer inputs are attached to the CT `Patient` component;
- structure filtering is expressed with `OnlyIncludeIfInRTStructure`;
- the mask pre-pass uses `SetBinToMinusOneIfNotInRTStructure`;
- structure volume and mass are computed from the RTSTRUCT mask on the CT voxel grid.

The DICOM input therefore needs:

- a valid CT series;
- an RTSTRUCT with contours that refer to that CT geometry;
- an RTPLAN / RT Ion Plan;
- an RTDOSE when RTDOSE export or comparison is needed.

For why some structure results are mass-normalized and others are volume-normalized, see
[Scorer Normalization](normalization.md).
