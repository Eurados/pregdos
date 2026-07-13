# PregDos Documentation

PregDos is a Flask web application for recalculating structure dose from clinical DICOM
proton therapy data with OpenTOPAS.

It uploads a DICOM study, converts each treatment field to TOPAS input with `dicomexport`,
optionally adds structure scorers, runs TOPAS locally or through SLURM, and presents the
scorer results as an HTML table, CSV export, PDF report, and optional DICOM RTDOSE export.

## Contents

- [Installation](installation.md): local and Docker setup.
- [Web GUI Usage](usage_webgui.md): upload, conversion, execution, and report download workflow.
- [Study Layout](study_layout.md): how uploaded studies and generated runs are stored.
- [Scorer Normalization](normalization.md): structure-score normalization and CT-grid scoring details.
- [SLURM Notes](slurm_notes.md): low-level notes for building/debugging a standalone SLURM setup.

## Runtime Requirements

PregDos requires OpenTOPAS 4.2.3 or newer. Older OpenTOPAS/TOPAS builds can corrupt
multithreaded scorer statistics and are blocked when detected.

For local use, TOPAS must be available on the same machine that runs the webserver. In the
combined Docker image, OpenTOPAS and a single-node SLURM controller are bundled into the image.

## Current Status

PregDos is under active development and is not for clinical use.
