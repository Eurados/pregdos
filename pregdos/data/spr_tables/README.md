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
reproduce the stopping powers your TPS used. Supplying your site's own HU-to-SPR calibration curve
is [issue #109](https://github.com/Eurados/pregdos/issues/109).

Based on:

> Schneider W, Bortfeld T, Schlegel W. (2000). *Correlation between CT numbers and tissue
> parameters needed for Monte Carlo simulations of clinical dose distributions.*
> Physics in Medicine and Biology, 45(2):459–478.
> DOI: [10.1088/0031-9155/45/2/314](https://doi.org/10.1088/0031-9155/45/2/314)

## Not bundled: the MATA stopping-power-ratio table

A brain-specific **SPR**-to-material table (Permatasari Method C) ships with dicomexport at
`res/spr_tables/SPRtoMaterial__Brain.txt`. It is deliberately **not** offered here.

Its input is stopping-power ratio, not Hounsfield units, and nothing in PregDos converts between
them. Selecting it for an HU CT does not skip the calibration — it silently substitutes
`SPR = 1 + HU/1000`, which overstates cortical bone stopping power by roughly 30% and dense bone by
nearly 40%. Soft tissue survives the coincidence; bone does not.

It becomes usable from an ordinary HU CT once dicomexport can compose it with a site calibration
curve — [dicomexport#98](https://github.com/nbassler/dicomexport/issues/98). Until then it is only
correct for a genuine SPR dataset, passed explicitly on dicomexport's command line.

> Permatasari et al. (2020). *Material assignment for proton range prediction in Monte Carlo
> patient simulations using stopping-power datasets.* Physics in Medicine and Biology.
> DOI: [10.1088/1361-6560/ab9702](https://doi.org/10.1088/1361-6560/ab9702)
