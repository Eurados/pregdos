# PregDos: Pregnancy Dosimetry Recalculation for Proton Therapy

**PregDos** is an open-source tool developed under the SONORA / PIANOFORTE project, enabling fetus dose estimation in proton therapy of pregnant patients by recalculating dose distributions using realistic phantom CT data and clinically delivered treatment plans.

---

## What PregDos Does

- Converts clinical DICOM RT plans and beam models into TOPAS Monte Carlo input files.
- Allows recalculation of equivalent dose to the fetus using realistic pregnancy phantoms.
- Provides Docker images for clinical environments and a Streamlit-based web interface for ease of use.
- Supports researchers and clinicians in accurate, personalized dose estimation during pregnancy.

---

## For Clinical Users

PregDos can be run with minimal setup:

- Pull and run the prebuilt Docker container on your secure clinical workstation.
- Use the web interface to upload your DICOM plan, select phantom data and the beam model, and run the dose recalculation pipeline.
- Retrieve equivalent dose results and dose distributions for documentation and decision support.

See [Installation](installation.md) and [WebGUI Usage](usage_webgui.md) for details.

---

## For Developers

PregDos is Python-based and modular, making it easy to extend and adapt for research:

- Uses `pydicom`, `numpy`, `scipy`, and `streamlit` as core dependencies.
- Includes:
- CLI tools for batch recalculations.
- Interfaces with TOPAS for Monte Carlo dose simulation.
- Tools for phantom CT fusion with patient scans.
- Provides contribution guidelines, test infrastructure, and auto-versioning.

See [Developer Guide](developer_guide.md) and [CLI Usage](usage_cli.md) for details.

---

## Documentation Contents

- [Installation](installation.md)
- [WebGUI Usage](usage_webgui.md)
- [CLI Usage](usage_cli.md)
- [Developer Guide](developer_guide.md)
- [Beam Model Format](beam_model_format.md)
- [Phantom Data](phantom_data.md)
- [API Reference](api_reference.md)

---

## License

PregDos is licensed under the MIT License for broad clinical and research use.

---

## Project Status

PregDos is under active development in SONORA and currently includes:

- DICOM to TOPAS pipeline operational
- Initial phantom dataset integration
- WebGUI prototype functional

Further enhancements such as dose reporting, advanced phantom fusion, and QA tools are planned.

---


This documentation is automatically built and hosted via ReadTheDocs.
