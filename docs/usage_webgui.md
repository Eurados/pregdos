# Web GUI Usage

This page walks through a complete PregDos run in the Flask web interface.

For a local example study, use:

```text
res/test_studies/DCPT_headphantom/
```

That directory contains a DICOM CT series, RTSTRUCT, RTPLAN, and RTDOSE files for a small
head phantom. See its local `README.md` for dose-reference details.

## 1. Start PregDos

Start the webserver as described in [Installation](installation.md), then open:

```text
http://localhost:5000
```

The main navigation has:

- **New simulation**: upload and configure a study.
- **Tasks**: view converted/running/completed runs.
- **About**: runtime versions and toolchain checks.

## 2. Upload A Study

Open **New simulation**.

Provide:

- A DICOM study, either as a ZIP or as a folder upload.
- A beam model CSV. For the head phantom example, use one of the bundled DCPT beam models if
  offered by the UI, or upload a matching beam model CSV.
- An SPR/material table. Use one of the bundled tables unless you are testing a specific
  calibration.

PregDos flattens nested uploads into a study directory while keeping the original DICOM files
under that study's `dicom/` subdirectory.

The DICOM study must contain the required RT modalities:

- CT series
- RTSTRUCT
- RTPLAN / RT Ion Plan
- RTDOSE

## 3. Configure Scorers

After upload, the setup page lets you choose scorer options and structures.

For a minimal completed run with the head phantom:

1. Select the target structure, such as `CTV`.
2. Keep the built-in in-field `DoseToWater` scorer if you want an RTDOSE comparison.
3. Add the structure scorers you want to report, such as neutron dose equivalent or gamma dose.
4. Choose the number of histories per field. For a quick smoke test, use a small value; for
   meaningful results, use a production history count appropriate to the study.

PregDos structure scoring is TOPAS-native: it uses RTSTRUCT filtering in TOPAS and a mask
pre-pass on the CT grid. The input data therefore needs valid CT geometry and RTSTRUCT contours
that TOPAS can rasterize. For the normalization details, see [Scorer Normalization](normalization.md).

## 4. Convert To TOPAS

Submit the setup form to generate TOPAS input files.

PregDos creates a new run directory under the uploaded study and runs `dicomexport` with that
run directory as the current working directory. The generated TOPAS inputs contain relative
paths and are meant to be executed from the same directory where they were generated.

After conversion, review the generated field files and submit the run.

## 5. Run TOPAS

When you submit the run, PregDos selects an executor:

- `slurm` when `PREGDOS_EXECUTOR=slurm`, or when `PREGDOS_EXECUTOR=auto` and `sbatch` is
  available.
- `local` when `PREGDOS_EXECUTOR=local`, or when `auto` does not find SLURM.

The run detail page shows the status and progress for each field. Local runs are processed by
a FIFO scheduler. SLURM runs record the submitted job IDs.

Every field writes:

- `<field>.log`
- `<field>.exit_code`
- scorer CSV files
- optional DICOM dose files

The run state is read from files on disk, so it survives webserver restarts.

## 6. Review Results

When the run completes, open **Tasks** and choose **View** for the run.

The scorer result table shows:

- scorer name
- structure
- quantity
- field
- dose
- statistical uncertainty
- unit

Displayed values are human-readable. The downloadable CSV keeps raw numeric values and
provenance.

## 7. Download Outputs

The result page groups downloads under **Downloads**:

- **CSV**: raw numeric results, provenance, histories, and normalization audit fields.
- **PDF**: formatted dose report for review and archiving.
- **RTDOSE**: DICOM dose export for TPS import, when TOPAS DICOM dose files are available.

The **Files** table lists generated TOPAS inputs, logs, scorer CSV files, DICOM dose files, and
metadata files from the run directory.

## 8. Rerun Or Queue Another Run

Use **Rerun** to submit the same generated TOPAS field files again. PregDos clears stale run
outputs before resubmitting so a previous result cannot be mistaken for a new result.

Use **New simulation** to upload another study or create another conversion of the same study.
Each conversion gets a new `run_<timestamp>` directory.
