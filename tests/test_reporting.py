"""Tests for the presentation layer: naming, grouping and the CSV report.

The parser's tests live in ``test_results.py``; these cover what a reader is shown, which is a
separate decision from what TOPAS wrote.
"""

import pytest

from pregdos import executor, report_pdf, reporting, results


# --- display scorer name ---

def test_display_name_drops_the_structure_suffix():
    """The results table prints the scorer beside a Structure column, so the suffix would be
    read twice: `DoseGamma_Pacemaker | Pacemaker | ...`."""
    assert reporting.display_scorer_name("DoseGamma_Pacemaker", "Pacemaker") == "DoseGamma"
    assert reporting.display_scorer_name("DoseEquivNeutron_Fetus", "Fetus") == "DoseEquivNeutron"


def test_display_name_does_not_care_what_the_structure_was_called():
    """No prefix contains an underscore, so the first one is always the boundary -- however
    mangled the structure name got on its way into the scorer name."""
    assert reporting.display_scorer_name("DoseWater_CTV1_68Gy", "CTV1 68Gy") == "DoseWater"
    assert reporting.display_scorer_name("DoseGamma_PTV_boost", "PTV-boost") == "DoseGamma"


def test_display_name_keeps_a_grid_suffix():
    """A grid scorer has no structure, so `_Grid` is the only thing naming its target -- and
    nothing beside it is repeating the name anyway."""
    assert reporting.display_scorer_name("DoseEquivNeutron_Grid", "") == "DoseEquivNeutron_Grid"


def test_a_grid_scorer_keeps_its_suffix_through_grouping():
    """Regression: rows used to store "—" as the structure when there was none, which is
    truthy, so grouping stripped `_Grid` -- the one thing naming a grid scorer's target.  The
    direct-call test above passed throughout, because it never saw the placeholder.
    """
    row = {"scorer": "DoseEquivNeutron_Grid", "structure": "", "quantity": "NeutronDoseEquivalent",
           "display_quantity": "NeutronDoseEquivalent", "unit": "Sv",
           "field": 1, "sum": 1.0, "sd": 0.1, "problem": None}
    (group,) = reporting.group_rows([row])
    assert group["display_scorer"] == "DoseEquivNeutron_Grid"
    assert group["structure"] == ""      # the em-dash is rendered, not stored


def test_display_name_survives_a_name_with_no_suffix():
    assert reporting.display_scorer_name("DoseGamma", "Liver") == "DoseGamma"


def test_every_generated_prefix_survives_its_own_round_trip():
    """The split is only safe while prefixes stay underscore-free; this fails loudly if a
    future scorer type is named e.g. `DoseNeutron_Equivalent`."""
    from pregdos.topas_scorer import _SCORER_NAME

    for prefix in _SCORER_NAME.values():
        assert "_" not in prefix, prefix
        assert reporting.display_scorer_name(f"{prefix}_Pacemaker", "Pacemaker") == prefix


# --- display quantity ---

def test_neutron_scorer_is_not_reported_as_ambient_dose_equivalent():
    """TOPAS labels the output `AmbientDoseEquivalent` because that is the scorer used, but the
    scorer holds no coefficients of its own and PregDos feeds it Q(E).  Printing TOPAS's name
    would put a quantity in the report that the report does not contain."""
    assert reporting.display_quantity("AmbientDoseEquivalent", "neutron", "Sv") == "NeutronDoseEquivalent"


def test_an_unfiltered_ambient_scorer_keeps_topas_name():
    """Not one of ours, so it may well be genuine H*(10); renaming it would invent a quantity
    rather than correct one."""
    assert reporting.display_quantity("AmbientDoseEquivalent", "", "Sv") == "AmbientDoseEquivalent"
    assert reporting.display_quantity("AmbientDoseEquivalent", "photon", "Sv") == "AmbientDoseEquivalent"


def test_every_gy_quantity_is_marked_as_physical_dose():
    """A bare "Gy" is read as RBE-weighted in a proton clinic -- the TPS prints Gy for Gy(RBE),
    and PregDos's own RTDOSE export is Gy(RBE).  None of these values carry an RBE."""
    assert reporting.display_quantity("DoseToWater", "", "Gy") == "DoseToWater (physical dose)"
    assert reporting.display_quantity("DoseToMedium", "proton", "Gy") == "DoseToMedium (physical dose)"


def test_marking_covers_every_gy_row_not_just_the_proton_ones():
    """Marking some and not others would imply the unmarked rows are the RBE-weighted ones --
    worse than not marking at all.  Gamma and DoseToWater are Gy too."""
    for particle in ("proton", "gamma", ""):
        assert reporting.display_quantity("DoseToMedium", particle, "Gy").endswith("(physical dose)")


def test_sv_is_not_marked():
    """Already a weighted quantity, and not misread this way."""
    assert "physical" not in reporting.display_quantity("NeutronDoseEquivalent", "neutron", "Sv")


def test_quantities_in_other_units_are_untouched():
    assert reporting.display_quantity("EnergyDeposit", "proton", "MeV") == "EnergyDeposit"


# --- retired scorer names ---

def test_retired_scorer_prefix_is_mapped_to_the_current_one():
    """Runs made before the 2026-08-21 rename still say `AmBDose` in their CSVs.  "AmB" meant
    ambient dose equivalent, which this scorer never computed, so it must not reach a report."""
    assert reporting.canonical_scorer_name("AmBDose_Pacemaker") == "DoseEquivNeutron_Pacemaker"
    assert reporting.canonical_scorer_name("AmBDoseNeutron_Fetus") == "DoseEquivNeutron_Fetus"
    assert reporting.canonical_scorer_name("AmBDose_Grid") == "DoseEquivNeutron_Grid"


def test_current_scorer_names_pass_through():
    assert reporting.canonical_scorer_name("DoseGamma_Pacemaker") == "DoseGamma_Pacemaker"
    assert reporting.canonical_scorer_name("DoseEquivNeutron") == "DoseEquivNeutron"


def test_display_name_maps_a_retired_prefix_too():
    assert reporting.display_scorer_name("AmBDose_Pacemaker", "Pacemaker") == "DoseEquivNeutron"
    assert reporting.display_scorer_name("AmBDose_Grid", "") == "DoseEquivNeutron_Grid"


def test_aliases_only_cover_names_no_longer_generated():
    """An alias for a *current* prefix would silently rewrite live output; an alias pointing at
    a name nothing generates would rewrite one dead name into another."""
    from pregdos.topas_scorer import _SCORER_NAME

    current = set(_SCORER_NAME.values())
    assert not (current & set(reporting._SCORER_ALIASES)), "alias shadows a generated prefix"
    assert set(reporting._SCORER_ALIASES.values()) <= current, "alias points at a dead name"


# --- display name vs the name code branches on ---

def test_rows_carry_both_the_plain_and_the_annotated_quantity():
    """Regression: the reports annotate Gy quantities as "(physical dose)", and both report
    writers used to branch on that annotated string.  `DoseToWater` never matched
    `DoseToWater (physical dose)`, so the RBE note silently vanished from every report.
    """
    row = {"scorer": "DoseWater_CTV", "structure": "CTV", "quantity": "DoseToWater",
           "display_quantity": "DoseToWater (physical dose)", "unit": "Gy",
           "field": 1, "sum": 1.0, "sd": 0.1, "problem": None}
    (group,) = reporting.group_rows([row])
    assert group["quantity"] == "DoseToWater"                       # branch on this
    assert group["display_quantity"] == "DoseToWater (physical dose)"   # print this


def test_group_falls_back_when_a_row_carries_no_annotation():
    row = {"scorer": "S", "structure": "CTV", "quantity": "DoseToWater", "unit": "Gy",
           "field": 1, "sum": 1.0, "sd": 0.1, "problem": None}
    (group,) = reporting.group_rows([row])
    assert group["display_quantity"] == "DoseToWater"


# --- material table provenance field ---

def test_material_table_field_pairs_the_name_with_a_short_hash():
    info = executor.RunInfo(
        backend=executor.LOCAL, submitted="", material_table="HUtoMaterialSchneider.txt",
        material_table_sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )
    assert reporting._material_table_field(info) == "HUtoMaterialSchneider.txt (ba7816bf)"


@pytest.mark.parametrize("info", [
    None,
    executor.RunInfo(backend=executor.LOCAL, submitted=""),
    executor.RunInfo(backend=executor.LOCAL, submitted="", material_table="table.txt"),
])
def test_material_table_field_is_unavailable_without_both_halves(info):
    """A name with no hash does not identify a table, so it is not half-reported as if it did."""
    assert reporting._material_table_field(info) == "unavailable"


@pytest.mark.parametrize("name_len", [25, 93, 400])
def test_pdf_keeps_the_hash_when_the_filename_is_too_long_to_fit(name_len):
    """The hash is the half that identifies the table, so the filename is what gets elided."""
    pdf = report_pdf.ReportPDF("t", "r")
    pdf.add_page()
    pdf.set_font(pdf._font_family, "", 7.5)
    width = pdf.epw - 32

    clipped = pdf._clip_keeping_suffix(f"{'H' * name_len}.txt (ba7816bf)", width)

    assert clipped.endswith("(ba7816bf)")
    assert pdf.get_string_width(clipped) <= width - 2


# --- plan-scaling warnings (#130) ---

def test_a_field_without_simulated_histories_is_warned_about():
    """Neither SIMULATED_HISTORIES nor Tf/spotWeight: the dose loses its rounding correction
    and its uncertainty.  Say so instead of falling back silently."""
    scaling = results.PlanScaling(particle_scaling=1.0, requested_histories=100, simulated_histories=0)
    assert "neither SIMULATED_HISTORIES nor Tf/spotWeight" in reporting._scaling_warning(scaling)


def test_a_single_batch_run_is_warned_about():
    """One batch: no uncertainty.  The report says why the column is empty."""
    scaling = results.PlanScaling(particle_scaling=1.0, requested_histories=100, simulated_histories=99,
                                  spot_mode="sources", runs=1, events_per_run=15)
    assert "single batch" in reporting._scaling_warning(scaling)


def test_a_complete_header_is_not_warned_about():
    scaling = results.PlanScaling(particle_scaling=1.0, requested_histories=100, simulated_histories=99,
                                  spot_mode="sources", runs=10, events_per_run=15)
    assert reporting._scaling_warning(scaling) is None


# --- A total over fields needs every field (Codex review of #132) ------------------------------

def _row(field, value=1.0):
    return {"scorer": "DoseWater_CTV", "structure": "CTV", "quantity": "DoseToWater", "unit": "Gy",
            "field": field, "sum": value, "sd": 0.1, "problem": None}


def test_a_group_lacking_a_field_of_the_run_gets_no_total():
    """Field 3 wrote nothing: a sum over fields 1 and 2 would look like the whole plan."""
    (group,) = reporting.group_rows([_row(1), _row(2)], expected_fields={1, 2, 3})
    assert group["total_sum"] is None
    assert group["missing_fields"] == [3]


def test_a_group_with_every_field_is_totalled():
    (group,) = reporting.group_rows([_row(1), _row(2), _row(3)], expected_fields={1, 2, 3})
    assert group["total_sum"] == pytest.approx(3.0)
    assert group["missing_fields"] == []


def _finished_sources_run(run_dir, fields=(1, 2, 3), runs=3, short=None):
    """A finished sources-mode run; field ``short`` wrote only some of its batches."""
    import json
    (run_dir / "run.json").write_text(json.dumps({
        "backend": "local", "submitted": "now",
        "fields": [{"topas_file": f"topas_field{n:02d}.txt", "ident": str(n)} for n in fields]}))
    for n in fields:
        (run_dir / f"topas_field{n:02d}.txt").write_text(
            "# REQUESTED_HISTORIES: 20000\n# SIMULATED_HISTORIES: 20000\n# PARTICLE_SCALING: 1.0\n"
            f"# SPOT_MODE: sources\n# SPOTS: 10\n# RUNS: {runs}\n# EVENTS_PER_RUN: 15\n")
        (run_dir / f"topas_field{n:02d}.exit_code").write_text("0\n")
        for k in range(runs - 1 if n == short else runs):
            (run_dir / f"topas_field{n:02d}_dose_to_water_CTV_Run_{k:04d}.csv").write_text(
                f"# Parameter File: topas_field{n:02d}.txt\n# Results for scorer: DoseWater_CTV\n"
                f"# DoseToWater ( Gy ) : Sum   Standard_Deviation\n{1.0 + k}, 5\n")


def test_a_missing_batch_suppresses_the_total_and_is_reported(tmp_path):
    """The reproduction: one missing batch CSV of field 3 silently removed field 3, and the
    report still gave an ALL row over fields 1 and 2."""
    _finished_sources_run(tmp_path, short=3)

    csv_text = reporting.build_report_csv(tmp_path, "study", "run", tmp_path)

    assert ",ALL," in csv_text
    all_row = next(line for line in csv_text.splitlines() if ",ALL," in line)
    assert "no total: no result for field 3" in all_row
    assert "sum over" not in csv_text
    assert "topas_field03.txt has ended, but batch(es) 2 of 3 wrote no result" in csv_text


def test_a_complete_run_is_totalled(tmp_path):
    _finished_sources_run(tmp_path)
    csv_text = reporting.build_report_csv(tmp_path, "study", "run", tmp_path)
    assert "sum over 3 fields" in csv_text and "no total" not in csv_text


@pytest.mark.parametrize("runs, expected", [
    (10, "spread of the 10 batch results"),
    (1, "ran as a single batch"),
])
def test_the_uncertainty_note_follows_the_spot_mode(tmp_path, runs, expected):
    _finished_sources_run(tmp_path, runs=runs)
    (note,) = reporting.uncertainty_notes(tmp_path)
    assert expected in note
    assert "simulated histories" not in note


def test_the_uncertainty_note_for_time_feature_is_per_history(tmp_path):
    (tmp_path / "topas_field01.txt").write_text(
        "# REQUESTED_HISTORIES: 20000\n# SIMULATED_HISTORIES: 19990\n# PARTICLE_SCALING: 1.0\n"
        "# SPOT_MODE: timefeature\n# SPOTS: 10\n# RUNS: 10\n")
    (note,) = reporting.uncertainty_notes(tmp_path)
    assert "N = simulated histories" in note


def test_an_override_inside_a_scored_structure_is_warned_about():
    metric = {"volume_cm3": 50.0, "override_overlaps": [
        {"override": "Implant", "material": "G4_Ti", "voxel_count": 4, "volume_cm3": 2.0}]}
    (warning,) = reporting._override_overlap_warnings("Uterus", metric)
    assert warning.startswith("2 cm³ of Uterus (4 % of it) lie in Implant, overridden with G4_Ti.")
    assert "comes from the CT numbers" in warning


def test_a_structure_overridden_itself_is_warned_about():
    metric = {"volume_cm3": 2.0, "override_overlaps": [
        {"override": "Implant", "material": "G4_Ti", "voxel_count": 4, "volume_cm3": 2.0}]}
    (warning,) = reporting._override_overlap_warnings("Implant", metric)
    assert warning.startswith("Implant is itself overridden with G4_Ti.")


def test_no_overlap_no_warning():
    assert reporting._override_overlap_warnings("Uterus", {"volume_cm3": 50.0}) == []
    assert reporting._override_overlap_warnings("Uterus", None) == []


def test_an_edge_overlap_of_neighbouring_contours_is_not_warned_about():
    """The head phantom's CTV shares 3 voxels (0.0013 %) with the brain stem."""
    metric = {"volume_cm3": 121.4, "override_overlaps": [
        {"override": "BrainStem", "material": "G4_Ti", "voxel_count": 3, "volume_cm3": 0.00154}]}
    assert reporting._override_overlap_warnings("CTV", metric) == []
