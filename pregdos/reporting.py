"""Turn parsed scorer output into what a person reads.

:mod:`pregdos.results` parses TOPAS CSVs and scales them to plan dose -- domain facts.  This
module owns every decision about *presentation*: what a quantity is called, which scorer name
is shown, how rows are grouped and totalled, and the CSV report itself.  The PDF is rendered by
:mod:`pregdos.report_pdf` from the same rows.

Keeping those decisions in one file matters because they move together.  Renaming the neutron
scorer, correcting the quantity TOPAS reports, and marking Gy as physical dose were three
edits to one concern; spread across the parser, the Flask module and two report writers, each
of them was a three-file change.

Nothing here imports Flask.  The run directory and studies root arrive as arguments, so the
whole layer is callable -- and testable -- without an app context.
"""

from __future__ import annotations

import csv
import datetime
import io
import math
from pathlib import Path
from . import results, structure_metrics, studies, versions
from .studies import StudyError


def canonical_quantity(quantity: str, particle: str) -> str:
    """The name of the quantity actually computed, correcting TOPAS's where it is wrong.

    Name correction only -- no reader-facing annotation -- so the result stays a single token
    and a rewritten CSV header is still machine-readable.  See :func:`display_quantity` for the
    presentation layer, and ``pregdos/data/neutron_dose_equivalent.csv`` for why the neutron
    scorer's output is not ambient dose equivalent.
    """
    if quantity == "AmbientDoseEquivalent" and particle == "neutron":
        return "NeutronDoseEquivalent"
    return quantity


# A TOPAS scorer CSV header can carry a structure name copied out of DICOM in latin-1, so the
# rewrite works on bytes: decoding to patch it would risk re-encoding it differently and
# corrupting a name we were only passing through.  Only ASCII tokens PregDos generated are
# touched.
_NEUTRON_FILTER = b'OnlyIncludeParticlesNamed = 1 "neutron"'


def canonicalize_header_bytes(head: bytes) -> bytes:
    """Correct retired names in the header block of a TOPAS scorer CSV.

    The raw CSVs are shipped in the run download, so the names a reader sees there have to
    agree with the report.  Applied on the way out; the files on disk stay exactly as TOPAS
    wrote them, because those are the record of what ran.
    """
    for retired, current in _SCORER_ALIASES.items():
        head = head.replace(b"# Results for scorer: " + retired.encode(),
                            b"# Results for scorer: " + current.encode())
    if _NEUTRON_FILTER in head:
        head = head.replace(b"# AmbientDoseEquivalent (", b"# NeutronDoseEquivalent (")
    return head


def display_quantity(quantity: str, particle: str, unit: str) -> str:
    """The quantity name to print, which is not always the one TOPAS wrote.

    TOPAS labels this scorer's output ``AmbientDoseEquivalent`` because that is the scorer it
    is, but the scorer holds no coefficients of its own -- it folds fluence with whatever
    ``FluenceToDoseConversion*`` table it is handed (``TsScoreAmbientDoseEquivalent.cc:44``).
    PregDos hands it Q(E) coefficients, so the number is a neutron dose equivalent and *not*
    H*(10); see ``pregdos/data/neutron_dose_equivalent.csv``.  Printing TOPAS's name would put
    a quantity in the report that the report does not contain.

    Only a neutron-filtered scorer is renamed.  An ``AmbientDoseEquivalent`` scorer without
    that filter is not one of ours and may well be genuine H*(10), so it keeps TOPAS's name --
    guessing wrong in that direction would invent a quantity rather than correct one.

    Every Gy quantity is additionally marked as physical dose.  This report is read by
    clinicians, and in a proton clinic a bare "Gy" is habitually read as RBE-weighted: the TPS
    prints Gy for what is really Gy(RBE), and PregDos's own RTDOSE export is Gy(RBE) too.  None
    of these values carry an RBE.  The mark goes on the quantity rather than the unit so it is
    stated once per group instead of on every value and every uncertainty.  Sv is left alone --
    it is already a weighted quantity and is not misread this way.
    """
    quantity = canonical_quantity(quantity, particle)
    if unit == "Gy":
        return f"{quantity} (physical dose)"
    return quantity


# Scorer name prefixes PregDos used to generate but has since retired.  Renamed 2026-08-21:
# the "AmB" meant *ambient* dose equivalent, which this scorer never computed -- see
# ``pregdos/data/neutron_dose_equivalent.csv``.  Runs made before the rename still carry the
# old name in their CSVs, and a report is not the place to leave a name for the wrong
# quantity, so it is mapped on the way in.
_SCORER_ALIASES = {
    "AmBDose": "DoseEquivNeutron",
    "AmBDoseNeutron": "DoseEquivNeutron",
}


def canonical_scorer_name(scorer: str) -> str:
    """``scorer`` with any retired prefix replaced by the one PregDos generates today.

    Applied to the parsed row, so grouping, the tables and the CSV report all agree -- and a
    run directory holding output from both sides of a rename still groups as one scorer.
    """
    prefix, sep, rest = scorer.partition("_")
    return _SCORER_ALIASES.get(prefix, prefix) + sep + rest


def display_scorer_name(scorer: str, structure: str) -> str:
    """The scorer name with its redundant ``_<structure>`` suffix removed, for display.

    TOPAS scorer names have to be unique within one input file, so pregdos builds them as
    ``DoseEquivNeutron_Pacemaker`` -- quantity plus target.  In a results table the target
    already has its own column right next to it, so the suffix is read twice:

        DoseGamma_Pacemaker | Pacemaker | DoseToMedium (Gy)

    The full name stays in the TOPAS input and in the CSV report, which are the record of
    what actually ran; only the human-facing tables drop it.

    Everything from the first underscore goes: no prefix in ``topas_scorer._SCORER_NAME``
    contains one, so the first underscore is always the boundary, whatever the structure was
    called.  A scorer with no structure keeps its whole name -- nothing is being repeated
    beside it, and for a grid scorer the ``_Grid`` suffix is all that names the target.

    Retired prefixes are mapped first, so this is safe to call on a raw parsed name as well as
    on one already canonicalized.
    """
    name = canonical_scorer_name(scorer)
    return name.split("_", 1)[0] if structure else name

# Quantities TOPAS reports per unit volume, keyed to the word each one's failure message
# uses for its denominator.  The correction itself is identical for both.
_VOLUME_NORMALIZED = {
    "AmbientDoseEquivalent": "fluence",
    "DoseToWater": "DoseToWater",
}


def group_rows(rows: list) -> list:
    """Group scorer rows by scorer, and total each group over its fields.

    A clinician reads one quantity at a time -- "how much neutron dose did the brainstem
    get, from all fields together" -- so the scorer is the outer key and the field the inner.

    The per-field values are each already scaled to that field's own particle budget, so the
    plan total is their plain sum.  Their uncertainties, however, come from independent Monte
    Carlo runs and therefore add **in quadrature**, not linearly.

    A group containing any unusable row (a NaN Sum, or a multi-bin grid) gets no total: a
    partial sum over fields would understate the dose while looking authoritative.  Nor is a
    group totalled when two rows share a field number -- ``IfOutputFileAlreadyExists =
    "Increment"`` writes a second CSV for a re-run of the *same* field, and adding those
    together would double-count it.
    """
    groups: dict = {}
    for row in rows:
        key = (row["scorer"], row["structure"], row["quantity"], row["unit"])
        groups.setdefault(key, []).append(row)

    out = []
    for (scorer, structure, quantity, unit), members in groups.items():
        members.sort(key=lambda r: (r["field"] is None, r["field"]))
        summable = [r for r in members if r["problem"] is None and r["sum"] is not None]
        complete = len(summable) == len(members)

        fields = [r["field"] for r in members]
        distinct_fields = None not in fields and len(set(fields)) == len(fields)

        total_sum = total_sd = None
        if complete and distinct_fields and len(members) > 1:
            total_sum = sum(r["sum"] for r in summable)
            sds = [r["sd"] for r in summable if r["sd"] is not None]
            total_sd = math.sqrt(sum(sd * sd for sd in sds)) if len(sds) == len(summable) else None

        out.append({
            "scorer": scorer, "structure": structure, "quantity": quantity, "unit": unit,
            # A hand-built row (tests, callers) may carry only the plain name.
            "display_quantity": members[0].get("display_quantity") or quantity,
            # The tables print this next to the Structure column, which would otherwise repeat
            # the scorer name's own suffix.  `scorer` keeps the real TOPAS object name for the
            # CSV report.
            "display_scorer": display_scorer_name(scorer, structure),
            "rows": members, "total_sum": total_sum, "total_sd": total_sd,
            "n_fields": len(members),
        })

    out.sort(key=lambda g: (g["scorer"], g["structure"]))
    return out


def result_rows(run_dir: Path, study: str, root: str | Path):
    """Parsed, plan-scaled scorer rows for one run, ready for the results table.

    ``root`` is the studies root, needed only to find the study's RTPLAN for beam names.
    """
    parsed, warnings = results.collect_results(run_dir)
    metrics, metric_warnings = structure_metrics.ensure_metrics(run_dir)
    warnings.extend(metric_warnings)

    # Field names come from the study's RTPLAN, keyed by the DICOM BeamNumber that
    # dicomexport writes into `_field<NN>` -- so show the name a clinician would recognise
    # next to each field, not just an index.
    plan_fractions = None
    try:
        rtplan = studies.find_rtplan(root, study)
        names = results.beam_names(rtplan)
        plan_fractions = results.planned_fractions(rtplan)
    except StudyError:
        names = {}
    fraction_multiplier = plan_fractions or 1

    rows = []
    for r in parsed:
        scaling = results.scaling_for(r, run_dir)
        total, sd = r.scaled(scaling)
        metric = None
        mass_normalized = False
        volume_normalized = False
        # The branches below rewrite both (EnergyDeposit in MeV becomes DoseToMedium in Gy once
        # mass-normalized).  `display_quantity` is applied at the end, to whatever they settle
        # on, so it sees the real unit -- the branches themselves all test `r.quantity`, the
        # name TOPAS actually wrote.
        quantity = r.quantity
        unit = r.unit
        problem = r.problem
        if r.is_single_bin:
            metric = structure_metrics.structure_metric(metrics, r.structure)
            if r.quantity == "EnergyDeposit":
                converted = structure_metrics.energy_deposit_to_gy(metrics, r.structure, r.unit, total, sd)
                if converted is not None:
                    total, sd = converted
                    quantity = "DoseToMedium"
                    unit = "Gy"
                    mass_normalized = True
                elif problem is None:
                    problem = "structure mass metrics are missing; cannot convert EnergyDeposit to Gy"
            elif r.quantity in _VOLUME_NORMALIZED and r.structure:
                # Both are intensive quantities that TOPAS divides by the whole patient-box
                # volume for a single-bin scorer.  Rescale that denominator to the structure
                # volume (V_patient / V_structure); only EnergyDeposit (energy, no volume
                # division) uses the structure mass instead.
                correction = (
                    structure_metrics.fluence_volume_correction_factor(metrics, r.structure)
                    if r.component == "Patient"
                    else None
                )
                if correction is not None:
                    if total is not None:
                        total *= correction
                    if sd is not None:
                        sd *= correction
                    volume_normalized = True
                elif problem is None:
                    problem = ("structure volume metrics or scorer component are missing; "
                               f"cannot correct {_VOLUME_NORMALIZED[r.quantity]} denominator")
            elif r.quantity == "DoseToMedium" and r.component == "Patient" and r.structure and problem is None:
                problem = ("structure DoseToMedium from TOPAS is not accepted; "
                           "rerun with PregDos EnergyDeposit structure scoring")
        if fraction_multiplier != 1:
            if total is not None:
                total *= fraction_multiplier
            if sd is not None:
                sd *= fraction_multiplier
        number = r.field_number
        rows.append({
            "field": number,
            "field_name": names.get(number, "") if number is not None else "",
            # Retired prefixes are mapped here rather than at each output, so grouping, the
            # tables and the CSV report cannot disagree about what a scorer is called.
            "scorer": canonical_scorer_name(r.scorer),
            # Left empty when the scorer has no structure filter (a grid scorer).  The
            # em-dash placeholder is rendered where it is shown, not stored here: as data it
            # reads as a structure name, and `display_scorer_name` then strips the `_Grid`
            # suffix that is the only thing naming such a scorer's target.
            "structure": r.structure,
            # Two fields, like `scorer` / `display_scorer` above.  `quantity` is the name of
            # what was computed -- TOPAS's, corrected where it is wrong -- and is what code
            # branches on.  `display_quantity` adds the reader-facing annotation on top.
            # Branching on the annotated string is how the DoseToWater note went missing.
            "quantity": canonical_quantity(quantity, r.particle),
            "display_quantity": display_quantity(quantity, r.particle, unit),
            "unit": unit,
            "sum": total,
            "sd": sd,
            "raw_sum": r.raw_sum,
            "problem": problem,
            "scale": scaling.factor * fraction_multiplier if scaling else None,
            "simulated_histories": scaling.simulated_histories if scaling else None,
            "structure_volume_cm3": metric.get("volume_cm3") if metric else None,
            "structure_mass_g": metric.get("mass_g") if metric else None,
            "structure_average_density_g_cm3": metric.get("average_density_g_cm3") if metric else None,
            "structure_mass_normalized": mass_normalized,
            "structure_volume_normalized": volume_normalized,
            "csv_name": r.csv_name,
        })
    return rows, warnings, plan_fractions

def report_provenance() -> dict[str, str]:
    repo_root = Path(__file__).resolve().parent.parent
    return {
        "pregdos": versions.canonical_package_version("pregdos", repo_root),
        "dicomexport": versions.dicomexport_version(),
        "topas": versions.topas_version(),
        "geant4": versions.geant4_version(),
    }


def build_report_csv(run_dir: Path, study: str, run_id: str, root: str | Path) -> str:
    """Every scorer in the run, plan-scaled, as one CSV report.

    Returns the text; the caller decides how to serve it.  The PDF equivalent is
    :func:`pregdos.report_pdf.build_report_pdf`, fed from the same rows.
    """
    rows, warnings, plan_fractions = result_rows(run_dir, study, root)
    generated_at = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    provenance = report_provenance()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["# PregDos Dose Report"])
    writer.writerow(["# Study", study])
    # The study name is only the upload's filename; the RTPLAN UID is what ties this report to
    # an identifiable plan in the TPS.
    writer.writerow(["# RTPLAN UID", results.plan_uid(run_dir) or "unavailable"])
    writer.writerow(["# Run", run_id])
    writer.writerow(["# Generated", generated_at])
    if plan_fractions:
        writer.writerow(["# Fractions", plan_fractions])
    else:
        writer.writerow(["# Fractions", "unavailable"])
    writer.writerow(["# PregDos", provenance.get("pregdos", "")])
    writer.writerow(["# TOPAS", provenance.get("topas", "")])
    writer.writerow(["# dicomexport", provenance.get("dicomexport", "")])
    # Prefix the Geant4 version with "v" so spreadsheets do not coerce e.g. "11.3" into a date.
    geant4 = provenance.get("geant4", "")
    writer.writerow(["# Geant4", f"v{geant4}" if geant4 else ""])
    for warning in warnings:
        writer.writerow(["# Warning", warning])
    writer.writerow(["# Note", "PregDos is under active development and validation is ongoing; "
                     "results should be checked independently."])
    if plan_fractions:
        writer.writerow(["# Note", "Reported values are scaled to total course dose using the planned fraction count."])
    else:
        writer.writerow(["# Note", "Planned fractions were unavailable; reported values use the generated TOPAS plan scale."])
    if any(r.get("quantity") == "DoseToWater" for r in rows):
        writer.writerow(["# Note", "DoseToWater is physical absorbed dose in Gy; the proton RBE of 1.1 "
                         "is not applied to these values (unlike the RTDOSE export)."])
    writer.writerow(["# Note", "Structure EnergyDeposit rows are mass-normalized; structure DoseToWater and "
                     "fluence rows are volume-normalized from the patient-box scorer volume to the structure volume "
                     "(issue #50)."])
    writer.writerow(["# Note", "dose_uncertainty is the 1-sigma Monte-Carlo statistical error "
                     "(sqrt(N)*SD/Sum applied to the scaled dose; N = simulated histories)."])
    writer.writerow(["# Note", "field=ALL rows total a scorer over its fields; their uncertainties add in quadrature."])
    writer.writerow(["scorer", "structure", "quantity", "field", "field_name", "unit",
                     "dose", "dose_uncertainty", "simulated_histories", "scale_factor",
                     "mass_normalized", "volume_normalized", "structure_volume_cm3", "structure_mass_g",
                     "structure_average_density_g_cm3", "status"])
    writer.writerow(["units", "", "", "", "", "Gy or Sv", "Gy or Sv", "Gy or Sv", "1", "1",
                     "", "", "cm3", "g", "g/cm3", ""])
    for group in group_rows(rows):
        for r in group["rows"]:
            writer.writerow([
                r["scorer"], r["structure"], r["display_quantity"],
                "" if r["field"] is None else r["field"], r["field_name"], r["unit"],
                "" if r["sum"] is None else repr(r["sum"]),
                "" if r["sd"] is None else repr(r["sd"]),
                "" if r["simulated_histories"] is None else r["simulated_histories"],
                "" if r["scale"] is None else repr(r["scale"]),
                "yes" if r["structure_mass_normalized"] else "",
                "yes" if r["structure_volume_normalized"] else "",
                "" if r["structure_volume_cm3"] is None else repr(r["structure_volume_cm3"]),
                "" if r["structure_mass_g"] is None else repr(r["structure_mass_g"]),
                "" if r["structure_average_density_g_cm3"] is None else repr(r["structure_average_density_g_cm3"]),
                r["problem"] or "",
            ])
        if group["total_sum"] is not None:
            total_histories = sum(
                r.get("simulated_histories") or 0 for r in group["rows"]
                if r.get("simulated_histories") is not None
            ) or None
            writer.writerow([
                group["scorer"], group["structure"], group["display_quantity"], "ALL", "", group["unit"],
                repr(group["total_sum"]),
                "" if group["total_sd"] is None else repr(group["total_sd"]),
                "" if total_histories is None else total_histories,
                "", "", "", "", "", "", f"sum over {group['n_fields']} fields",
            ])
    return buf.getvalue()
