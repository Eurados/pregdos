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
    <spr_table>.txt
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
`s:Ge/Patient/DicomDirectory` and `includeFile`. PregDos therefore runs `dicomexport` with the
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
