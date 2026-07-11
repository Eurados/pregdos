# DCPT Head Phantom

This study contains an RT ion plan (`RN...dcm`) with three proton fields and five planned fractions.

The RTSTRUCT contains a `CTV` target structure, but no `PTV` structure.

## RTPLAN Dose Reference

The plan dose reference is a target dose reference, not a CTV/PTV mean-dose value:

- `NumberOfFractionsPlanned`: `5`
- `TargetPrescriptionDose`: `8.639 Gy(RBE)` (`7.854 Gy` physical dose)
- Per-fraction target dose: `1.728 Gy(RBE)` (`1.571 Gy` physical dose)

The fraction group stores the following per-beam dose-reference values:

- Beam 1 / Field 1: `0.6894 Gy(RBE)` (`0.6267 Gy` physical dose)
- Beam 2 / Field 2: `0.7106 Gy(RBE)` (`0.6460 Gy` physical dose)
- Beam 3 / Field 3: `0.3277 Gy(RBE)` (`0.2979 Gy` physical dose)

The three beam values sum to `1.728 Gy(RBE)`, matching one fraction. Over five fractions this gives `8.639 Gy(RBE)`.

Important: these `BeamDose` values are DICOM RTPLAN dose-reference values. They should not be interpreted as voxel-averaged CTV dose or PTV dose. Use the RTDOSE files and an explicit structure mask when comparing structure mean doses.

## Eclipse Beam RTDOSE Scaling

The three Eclipse RTDOSE files are `DoseSummationType = BEAM`; each file references one beam. The stored dose values are total biological dose over all five fractions for that beam. The DICOM unit is `GY`, but for this proton plan these Eclipse values are `Gy(RBE)` with the clinical RBE factor of 1.1 already applied.

TOPAS/PregDos scores physical dose in `Gy`. To compare TOPAS to Eclipse RTDOSE values from this study, divide the Eclipse `Gy(RBE)` values by `1.1`.

At the RTPLAN dose-reference point `(0, -170.2, -2.122) mm`, the RTDOSE `Gy(RBE)` values are exactly five times the RTPLAN `BeamDose` values:

| Beam | RTPLAN `BeamDose` per fraction (Gy(RBE)) | Eclipse RTDOSE at dose-reference point (Gy(RBE)) | RTDOSE / 5 (Gy(RBE)) | RTDOSE / 1.1 (Gy) | RTDOSE / 5 / 1.1 (Gy) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.6894 | 3.447 | 0.6894 | 3.134 | 0.6267 |
| 2 | 0.7106 | 3.553 | 0.7106 | 3.230 | 0.6460 |
| 3 | 0.3277 | 1.639 | 0.3277 | 1.490 | 0.2979 |
| Sum | 1.7277 | 8.639 | 1.7277 | 7.854 | 1.5706 |

The values above are point-dose checks at the RTPLAN dose-reference point. They are not directly comparable to CTV structure mean doses.

Example single-point samples from the Eclipse-exported RTDOSE fixture files in this test study, evaluated at CTV contour-derived coordinates:

| Point | Patient coordinate (mm) | Field | RD total (Gy(RBE)) | RD / 5 (Gy(RBE)) | RD / 1.1 (Gy) | RD / 5 / 1.1 (Gy) |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| CTV contour centroid | `(14.72, -204.5, 21.50)` | 1 | 2.635 | 0.5270 | 2.395 | 0.4791 |
|  |  | 2 | 3.416 | 0.6832 | 3.105 | 0.6211 |
|  |  | 3 | 2.770 | 0.5540 | 2.518 | 0.5036 |
|  |  | All fields | 8.821 | 1.764 | 8.019 | 1.604 |
| CTV mid-slice contour centroid | `(13.35, -205.1, 20.00)` | 1 | 2.823 | 0.5646 | 2.566 | 0.5133 |
|  |  | 2 | 3.474 | 0.6948 | 3.158 | 0.6316 |
|  |  | 3 | 2.800 | 0.5600 | 2.545 | 0.5091 |
|  |  | All fields | 9.097 | 1.819 | 8.270 | 1.654 |
| CTV bounding-box center | `(12.69, -205.0, 23.00)` | 1 | 2.591 | 0.5182 | 2.355 | 0.4711 |
|  |  | 2 | 3.325 | 0.6650 | 3.023 | 0.6045 |
|  |  | 3 | 2.706 | 0.5412 | 2.460 | 0.4920 |
|  |  | All fields | 8.622 | 1.724 | 7.838 | 1.568 |
