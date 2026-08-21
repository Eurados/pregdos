"""Tests for the presentation layer: naming, grouping and the CSV report.

The parser's tests live in ``test_results.py``; these cover what a reader is shown, which is a
separate decision from what TOPAS wrote.
"""

from pregdos import reporting


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
