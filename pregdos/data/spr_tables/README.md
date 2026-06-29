Hounsfield Unit (HU) to stopping power ratio (SPR) tables, prepared for TOPAS.

## Files

### HUtoMaterialSchneider.txt

Generic HU-to-material conversion file using the Schneider method, sourced from the
[OpenTOPAS documentation](https://opentopas.readthedocs.io/en/stable/examples-docs/Patient/HUtoMaterialSchneider.html).
This is a reasonable default for most patient geometries.

Based on:

> Schneider W, Bortfeld T, Schlegel W. (2000). *Correlation between CT numbers and tissue
> parameters needed for Monte Carlo simulations of clinical dose distributions.*
> Physics in Medicine and Biology, 45(2):459–478.
> DOI: [10.1088/0031-9155/45/2/314](https://doi.org/10.1088/0031-9155/45/2/314))

### SPRtoMaterial__Brain.txt

Brain-specific HU-to-material conversion, generated using Method C from:

> Permatassari et al. (2020). *Material assignment for proton range prediction in Monte Carlo
> patient simulations using stopping-power datasets.* Physics in Medicine and Biology.
> DOI: [10.1088/1361-6560/ab9702](https://doi.org/10.1088/1361-6560/ab9702)
