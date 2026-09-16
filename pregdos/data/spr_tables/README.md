Tables converting a CT's voxel values into materials for TOPAS.

PregDos bundles one, and it converts **Hounsfield units** — which is what a conventional CT holds,
and also what a dual-energy scan holds once it has been reconstructed monoenergetically.

**If you supply your own table, it must expect the quantity your images actually hold.** PregDos
cannot check this for you. TOPAS's Schneider converter maps an integer to a material and is
indifferent to what the integer means, so the wrong table produces a plausible, wrong dose and
nothing objects. DICOM does not settle it either: `RescaleType` reads `HU` even on stopping-power
maps. Name your tables by the quantity they expect and keep track:

| Prefix | Expects voxels to hold | For |
| --- | --- | --- |
| `HUtoMaterial*` | Hounsfield units | a conventional CT, or a monoenergetic DECT reconstruction |
| `SPRtoMaterial*` | stopping-power ratio, encoded `(SPR - 1) x 1000` | an externally derived SPR dataset |

## Files

### HUtoMaterialSchneider.txt

Generic **Hounsfield-unit**-to-material conversion using the Schneider method, sourced from the
[OpenTOPAS documentation](https://opentopas.readthedocs.io/en/stable/examples-docs/Patient/HUtoMaterialSchneider.html).
A reasonable default for most patient geometries.

Note what "generic" costs: this is some other institute's scanner calibration, so it does not
reproduce the stopping powers your TPS used. Supply a table built from your own site's
calibration if that matters for what you are measuring.

Based on:

> Schneider W, Bortfeld T, Schlegel W. (2000). *Correlation between CT numbers and tissue
> parameters needed for Monte Carlo simulations of clinical dose distributions.*
> Physics in Medicine and Biology, 45(2):459–478.
> DOI: [10.1088/0031-9155/45/2/314](https://doi.org/10.1088/0031-9155/45/2/314)
