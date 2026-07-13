# Scorer Normalization

Why some structure results are divided by a **mass** and others are rescaled by a **volume**
ratio, and why the two are not interchangeable.

Relevant issue: [#50](https://github.com/Eurados/pregdos/issues/50).

---

## Design choice: score structures on the CT grid

PregDos reports a mean dose per RT structure **on the native CT voxel grid**. It does not build
or overlay a separate RTDOSE dose grid for structure scoring.

That is deliberate:

- **Memory.** The CT voxel grid is loaded anyway, so structure scoring reuses it. Adding a
  clinical RTDOSE grid means allocating and tracking a second full voxel grid, which is exactly
  the pattern that can exhaust memory on large cases.
- **Coverage.** The CT covers the whole patient. Out-of-field structures such as the fetus can
  be scored even when they lie outside the clinical RTDOSE grid.
- **Runtime and geometry.** Scoring happens only in the CT geometry, not in a dose grid layered
  on top of it, which keeps the setup simpler and modestly faster.

The structure workflow is:

1. **Mask pre-pass.** A cheap TOPAS run writes a per-structure binary mask on the native CT
   grid (`SetBinToMinusOneIfNotInRTStructure`).
2. **Metrics.** From that mask plus the CT Hounsfield units and the Schneider HU-to-density
   table already embedded in the TOPAS input, PregDos computes each structure's volume and
   mass.
3. **Renormalization.** The production single-bin scorer result is converted or rescaled using
   those structure metrics, as described below.

All PregDos doses are physical absorbed dose (Gy) or equivalent dose (Sv). PregDos does not
apply proton RBE. A clinical Eclipse proton RTDOSE is commonly stored as
`Gy(RBE) = physical dose-to-water x 1.1`, so compare those separately from physical
`DoseToWater`.

The method has been checked against a full in-field RTDOSE cube on the Eclipse grid: the
cube's CTV mask mean reproduces the mask/metrics route to about 1.4%, and lands within about
8% of the Eclipse RTDOSE CTV mask mean. This is a mean-to-mean comparison, not a comparison
against the hotter single-point centroid.

---

## The root cause: TOPAS's denominator is the whole patient

Every structure scorer PregDos writes is attached to the full CT volume with a single bin
(`pregdos/topas_scorer.py`):

```
s:Sc/<name>/Component = "Patient"
i:Sc/<name>/XBins     = 1
i:Sc/<name>/YBins     = 1
i:Sc/<name>/ZBins     = 1
sv:Sc/<name>/OnlyIncludeIfInRTStructure = 1 "Fetus"
```

`OnlyIncludeIfInRTStructure` suppresses *scoring* outside the ROI, but it does **not** shrink
the bin. The single bin remains the entire patient box. Whatever TOPAS divides by, it divides
by the whole CT grid — never by the contour.

That one fact drives everything below. What the correction has to be depends entirely on
whether the scored quantity is **extensive** (a sum, no denominator) or **intensive** (TOPAS
already divided).

---

## Extensive quantities → normalize by structure **mass**

`EnergyDeposit` — used by the gamma, proton and other absorbed-dose structure scorers — is a
plain sum in MeV. TOPAS applies no denominator at all, so **nothing is diluted**; you simply do
not have a dose yet.

Converting to Gy means dividing by a mass, because absorbed dose *is* energy per unit mass:

$$D_\text{ROI} = \frac{E_\text{ROI}}{m_\text{ROI}}$$

This is `structure_metrics.energy_deposit_to_gy()`:

```python
factor = MEV_PER_G_TO_GY / mass_g
```

The mass is the ROI's real mass — HU → density via the SPR table, summed over the RTSTRUCT
mask from the pre-pass. This is not a correction. It is the physically correct denominator,
being supplied for the first time.

Reported in the CSV as `mass_normalized = yes`.

### Why not score `DoseToMedium` directly and skip this?

Because for a single-bin scorer on `Patient`, TOPAS's `DoseToMedium` denominator is the
patient-box mass, not the ROI mass — the same dilution problem described below. Scoring the
raw energy and supplying the denominator ourselves sidesteps it entirely and is exact. Hence
`absorbed_quantity = "EnergyDeposit" if is_structure else "DoseToMedium"`.

---

## Intensive quantities → rescale by the **volume** ratio

`DoseToWater` and `AmbientDoseEquivalent` (H\*(10)) are intensive: TOPAS *has* already divided
— but over the whole patient box instead of the ROI. The reported value is the ROI's energy
smeared across the entire patient, and is too small by exactly the geometric dilution factor:

$$f = \frac{V_\text{patient}}{V_\text{structure}}$$

This is `structure_metrics.fluence_volume_correction_factor()`. Reported in the CSV as
`volume_normalized = yes`.

The correction is **exact**, not an approximation. No uniformity assumption enters, because the
numerator only ever received ROI contributions:

$$D_\text{TOPAS} = \frac{E_\text{ROI}}{\rho_w V_\text{patient}}
\quad\Longrightarrow\quad
D_\text{TOPAS} \cdot \frac{V_\text{patient}}{V_\text{ROI}} = \frac{E_\text{ROI}}{\rho_w V_\text{ROI}} = D_\text{ROI}$$

### Why volume and not mass — the part that is easy to get wrong

`DoseToWater`'s denominator is the mass of **water** filling the bin, $\rho_w V_\text{bin}$,
and $\rho_w$ is constant. The density therefore *cancels in the ratio* and only the volume
survives. `AmbientDoseEquivalent` is fluence-based (track length per unit volume), so its
denominator is likewise purely geometric.

Applying a mass ratio $M_\text{patient}/M_\text{structure}$ to either would smuggle real tissue
density variation back into a quantity that by construction assumes water. **The mass ratio is
never the right correction for a dose quantity**, which is why no `mass_correction_factor` is
offered — see the note at the bottom of `structure_metrics.py`.

### Why not score `EnergyDeposit` for `DoseToWater` too, for one uniform scheme?

Because dose-to-water is not dose-to-medium, and it cannot be recovered from deposited energy
plus a mass. It requires the water-to-medium mass stopping-power ratio applied step by step,
which is exactly what TOPAS's native `DoseToWater` does internally. That scorer exists so
results can be compared against Eclipse (which reports dose to water), so it must stay native
and take the geometric correction as the only fix available.

---

## Summary

| Scored quantity | Kind | TOPAS denominator | PregDos applies | CSV flag |
|---|---|---|---|---|
| `EnergyDeposit` (gamma, proton, …) | extensive (MeV) | none | ÷ $m_\text{ROI}$ → Gy | `mass_normalized` |
| `DoseToWater` | intensive (Gy) | $\rho_w V_\text{patient}$ | × $V_\text{patient}/V_\text{ROI}$ | `volume_normalized` |
| `AmbientDoseEquivalent` | intensive (Sv) | $V_\text{patient}$ | × $V_\text{patient}/V_\text{ROI}$ | `volume_normalized` |

In one line: **mass** means *"TOPAS gave me raw energy and I am supplying the physically correct
denominator."* **Volume** means *"TOPAS already divided, by a volume-like denominator that was
too big, and I am rescaling it."*

Grid scorers (`Component = "ScoringGrid"`) are unaffected by all of this — they are binned
normally, TOPAS's denominator is the voxel, and neither correction is applied.

---

## The load-bearing invariant

The volume correction is only exact if $V_\text{patient}$ is **the same volume TOPAS used as its
bin denominator** — the full voxelized CT grid, not a body contour and not a nonzero-HU mask.

`compute_metrics()` satisfies this by construction:

```python
patient_voxels = int(ct.hu.size)              # every voxel of the CT array
patient_volume = patient_voxels * ct.voxel_volume_cm3
```

and it loads that CT from the `Ge/Patient/DicomDirectory` read out of the run's own TOPAS input,
so the grid is necessarily the one TOPAS voxelized.

**If anything ever crops or resamples the `Patient` component** — a CT sub-volume selection, a
cropped export, a different grid for the pre-pass than for the run — then $V_\text{patient}$ no
longer matches TOPAS's denominator and *every volume-normalized number is silently wrong* by the
ratio of the two definitions. Nothing in the current pipeline does this, but it is the
assumption to check first if `DoseToWater` or H\*(10) structure results ever look off by a
constant factor.
