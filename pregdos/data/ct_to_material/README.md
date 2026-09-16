# CT-number-to-material tables

PregDos takes a **TOPAS Schneider table indexed by CT number (Hounsfield units)**,
which assigns material composition and density to the CT voxels. Supply a finished
table prepared for your site's imaging protocol and calibration. Producing it from
site calibration curves and material data is an external step.

The table must expect the quantity your images actually hold. Structural validation
belongs in dicomexport: consistent Schneider vectors and valid array indices can be
checked, but those checks do not establish the physical meaning of the input values
or whether the calibration matches the scan. DICOM labels alone cannot establish
that match either: `RescaleType` can read `HU` even on a derived stopping-power map.
The user is responsible for selecting the appropriate table.

Keep the `*toMaterial*` filename convention: `HUtoMaterial*` identifies a table whose
input is Hounsfield units; `SPRtoMaterial*` identifies one whose input is stopping-power
ratio. The required input here is CT number.

The chosen table is copied into the study directory. Each run's generated
`topas_field*.txt` references it through `includeFile`. Preserve the complete study
directory, including that unchanged table, to preserve the run's inputs.

## Bundled example: HUtoMaterialSchneider.txt

The bundled file is preserved unchanged from the
[OpenTOPAS example](https://opentopas.readthedocs.io/en/stable/examples-docs/Patient/HUtoMaterialSchneider.html).
It illustrates the required format; it is not a calibration for your scanner or TPS.

It contains 13 elements, 26 material boundaries defining 25 compositions, and 25
weight vectors that each sum to 1. Its 3996 density corrections cover HU -1000 through
2995, matching the first density-section boundary and TOPAS's default
`MinImagingValue=-1000`.

The file describes `DensityCorrection` as a correction for the relative stopping
power of Geant4 and the XiO planning system. TOPAS applies it as a **density
multiplier**. These XiO-specific values should not be inherited as an Eclipse or
site calibration. Use unity corrections only when the other density parameters
already express your intended density curve; a generated table may intentionally
encode that curve in `DensityCorrection` itself.

Some representative values from the bundled file are:

| HU | Density correction | Resulting density (g/cm³) |
| --- | --- | --- |
| -1000 | 9.35212 | 0.01132 |
| -999 | 5.55269 | 0.01244 |
| -950 | 1.12840 | 0.05946 |
| 0 | 0.97075 | 0.98822 |
| 1200 | 1.03851 | 1.79392 |

At HU -1000, the composition is air (75.5% nitrogen, 23.2% oxygen, 1.3% argon),
but the correction multiplies the base density of 0.00121 g/cm³ by about 9.4.
For a uniform 50 cm path at this value, the mass thickness is approximately
0.57 g/cm² instead of 0.06 g/cm². PregDos's `patient.mass_g` sums the entire CT
grid, including any surrounding air voxels; each structure mass sums only its mask.

Density has five downward steps across the tabulated range. The largest is about
0.022 g/cm³ at HU -120; three very small drops near the upper end are not at section
boundaries. Values at HU 2995 and above map to 100% titanium at approximately
4.55 g/cm³, an implant assumption that must be assessed for the intended use.

The Schneider method is described in:

> Schneider W, Bortfeld T, Schlegel W. (2000). *Correlation between CT numbers and tissue
> parameters needed for Monte Carlo simulations of clinical dose distributions.*
> Physics in Medicine and Biology, 45(2):459–478.
> DOI: [10.1088/0031-9155/45/2/314](https://doi.org/10.1088/0031-9155/45/2/314)
