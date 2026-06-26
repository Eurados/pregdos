# Description of the beam model format

## Beam model versions

| File | Valid for treatment data | Notes |
|------|--------------------------|-------|
| `DCPT_beam_model__v5.csv` | September 2024 and later | Current model — use this by default; BMODPOS 600 mm |
| `DCPT_beam_model__v2.csv` | Until August 2024 (inclusive) | Earlier model; use for retrospective cases; BMODPOS 500 mm |

## Column format

1) Energy: Nominal (i.e. requested energy) [MeV]
2) E_real: actual energy derived from range measurements [MeV]
3) E_real_sigma: energy spread 1-sigma Gaussian [MeV]
4) protons/MU: number of protons per given monitor Unit (this is proportional to air mass stopping power)
5) beamwidth sigma x [mm]
6) beamwidth sigma y [mm]
7) divergence sigma x' [rad]
8) divergence sigma y' [rad]
9) Covariance cov(x x')
10) Covariance cov(y y')

The beam model position (`BMODPOS`) is specified in the CSV header and is read automatically by dicomexport >= 1.4.0.

### Acknowledgements
- `DCPT_beam_model__v2.csv` was kindly provided by Anne Vestergaard and Peter Lægdsmand from DCPT.
- `DCPT_beam_model__v5.csv` was kindly provided by Simon Norrig from DCPT.
