# DCPT Head Phantom

This study contains an RT ion plan (`RN...dcm`) with three proton fields and five planned fractions.

The RTSTRUCT contains a `CTV` target structure, but no `PTV` structure.

## RTPLAN Dose Reference

The plan dose reference is a target dose reference, not a CTV/PTV mean-dose value:

- `NumberOfFractionsPlanned`: `5`
- `TargetPrescriptionDose`: `8.639 Gy`
- Per-fraction target dose: `1.728 Gy`

The fraction group stores the following per-beam dose-reference values:

- Beam 1 / Field 1: `0.6894 Gy`
- Beam 2 / Field 2: `0.7106 Gy`
- Beam 3 / Field 3: `0.3277 Gy`

The three beam values sum to `1.728 Gy`, matching one fraction. Over five fractions this gives `8.639 Gy`.

Important: these `BeamDose` values are DICOM RTPLAN dose-reference values. They should not be interpreted as voxel-averaged CTV dose or PTV dose. Use the RTDOSE files and an explicit structure mask when comparing structure mean doses.

## Eclipse Beam RTDOSE Scaling

The three Eclipse RTDOSE files are `DoseSummationType = BEAM`; each file references one beam. The stored dose values are total dose over all five fractions for that beam.

At the RTPLAN dose-reference point `(0, -170.2, -2.122) mm`, the RTDOSE values are exactly five times the RTPLAN `BeamDose` values:

| Beam | RTPLAN `BeamDose` per fraction (Gy) | Eclipse RTDOSE at dose-reference point (Gy) | RTDOSE / 5 (Gy) |
| --- | ---: | ---: | ---: |
| 1 | 0.6894 | 3.447 | 0.6894 |
| 2 | 0.7106 | 3.553 | 0.7106 |
| 3 | 0.3277 | 1.639 | 0.3277 |

Example CTV contour-derived point samples from the same three beam RTDOSE files:

| Point | Patient coordinate (mm) | Beam 1 RD (Gy) | Beam 2 RD (Gy) | Beam 3 RD (Gy) | Sum RD (Gy) | Sum / 5 (Gy) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| CTV contour centroid | `(14.72, -204.5, 21.50)` | 2.635 | 3.416 | 2.770 | 8.821 | 1.764 |
| CTV mid-slice contour centroid | `(13.35, -205.1, 20.00)` | 2.823 | 3.474 | 2.800 | 9.097 | 1.819 |
| CTV bounding-box center | `(12.69, -205.0, 23.00)` | 2.591 | 3.325 | 2.706 | 8.622 | 1.724 |
