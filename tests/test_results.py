"""Tests for scorer CSV parsing and plan scaling (issue #32).

The fixtures in ``tests/data/`` are verbatim output from a real OpenTOPAS 4.2.3 run of
``res/test_studies/DCPT_headphantom`` (field 1, nstat=20000).  Parsing ground truth beats
parsing a CSV we invented -- the original issue assumed an ``X, Y, Z, Sum, Standard_Deviation``
layout that structure-average scorers do not produce.
"""

import math
from pathlib import Path

import pytest

from pregdos import results
from pregdos.results import ResultsError, parse_plan_scaling, parse_scorer_csv

DATA = Path(__file__).parent / "data"
NEUTRON = DATA / "topas_field01_neutron_BrainStem.csv"
PROTON = DATA / "topas_field01_proton_primary_CTV.csv"
TOPAS_INPUT = DATA / "topas_field01.txt"


# --- real scorer CSVs ---

def test_parses_real_neutron_csv():
    r = parse_scorer_csv(NEUTRON)
    assert r.scorer == "AmBDose_BrainStem"
    assert r.quantity == "AmbientDoseEquivalent"
    assert r.unit == "Sv"
    assert r.structure == "BrainStem"
    assert r.parameter_file == "topas_field01.txt"
    assert r.topas_version == "4.2.p3"
    assert r.columns == ["Sum", "Standard_Deviation"]
    assert r.raw_sum == pytest.approx(1.049973996636311e-11)
    assert r.raw_standard_deviation == pytest.approx(5.038408614663083e-15)
    assert r.usable and r.problem is None


def test_parses_real_proton_csv():
    r = parse_scorer_csv(PROTON)
    assert r.scorer == "DoseProtonPrimary_CTV"
    assert r.quantity == "DoseToMedium"
    assert r.unit == "Gy"
    assert r.structure == "CTV"
    assert r.raw_sum == pytest.approx(1.355829448712598e-09)


def test_structure_mode_has_no_index_columns():
    """The issue assumed `X, Y, Z, Sum, SD`.  A 1x1x1 scorer emits only the reported columns."""
    r = parse_scorer_csv(NEUTRON)
    assert r.is_single_bin
    index, values = r.rows[0]
    assert index == ()
    assert set(values) == {"Sum", "Standard_Deviation"}


def test_multi_bin_rows_carry_bin_indices(tmp_path):
    """A grid scorer prepends X, Y, Z.  Column count is derived, never assumed."""
    p = tmp_path / "grid.csv"
    p.write_text(
        "# TOPAS Version: 4.2.p3\n"
        "# Results for scorer: DoseGrid\n"
        "# DoseToMedium ( Gy ) : Sum   Standard_Deviation   \n"
        "0, 0, 0, 1.5e-9, 2.0e-12\n"
        "0, 0, 1, 2.5e-9, 3.0e-12\n"
    )
    r = parse_scorer_csv(p)
    assert not r.is_single_bin
    assert r.rows[0][0] == (0, 0, 0)
    assert r.rows[1][1]["Sum"] == pytest.approx(2.5e-9)
    # a grid is not summarised as one dose
    assert r.raw_sum is None and r.problem is None


def test_unfiltered_scorer_has_no_structure(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("# Results for scorer: All\n# DoseToMedium ( Gy ) : Sum   \n1.0e-9\n")
    assert parse_scorer_csv(p).structure == ""


_MINIMAL = "# Results for scorer: A\n# AmbientDoseEquivalent ( Sv ) : Sum   \n1.0e-11\n"


def test_increment_suffix_is_recorded(tmp_path):
    """`IfOutputFileAlreadyExists = Increment` yields `..._1.csv` on a repeated run."""
    (tmp_path / "topas_field01_neutron_Fetus.csv").write_text(_MINIMAL)
    p = tmp_path / "topas_field01_neutron_Fetus_1.csv"
    p.write_text(_MINIMAL)
    assert parse_scorer_csv(p).run_index == 1


def test_structure_name_ending_in_a_number_is_not_an_increment(tmp_path):
    """A structure legitimately named `PTV_2` must not be read as a re-run of `PTV`."""
    p = tmp_path / "topas_field01_gamma_PTV_2.csv"
    p.write_text(_MINIMAL)
    assert parse_scorer_csv(p).run_index is None    # no `..._gamma_PTV.csv` sibling exists


# --- field number and beam name ---

def test_field_number_comes_from_the_parameter_file():
    """dicomexport names its output by DICOM BeamNumber, so the filename maps onto the plan."""
    assert parse_scorer_csv(NEUTRON).field_number == 1


def test_field_number_is_none_without_a_parameter_file(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("# Results for scorer: A\n# DoseToMedium ( Gy ) : Sum   \n1.0\n")
    assert parse_scorer_csv(p).field_number is None


def _rtplan(tmp_path, beams, sequence="IonBeamSequence", fractions=None):
    """Write a minimal but well-formed RTPLAN carrying BeamNumber -> BeamName.

    The file gets a real File Meta header, as any clinical RTPLAN would -- a headerless
    dataset is rejected by pydicom, and `beam_names()` is meant to reject it too.
    """
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset

    sop_class = pydicom.uid.RTIonPlanStorage
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
    ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.Modality = "RTPLAN"

    seq = []
    for number, name in beams:
        beam = Dataset()
        beam.BeamNumber = number
        beam.BeamName = name
        seq.append(beam)
    setattr(ds, sequence, seq)
    if fractions is not None:
        group = Dataset()
        group.FractionGroupNumber = 1
        group.NumberOfFractionsPlanned = fractions
        ds.FractionGroupSequence = [group]

    path = tmp_path / "RN.plan.dcm"
    ds.save_as(str(path), enforce_file_format=True)
    return path


def test_beam_names_from_ion_plan(tmp_path):
    """Proton plans are RT Ion Plan and use IonBeamSequence."""
    path = _rtplan(tmp_path, [(1, "Field 1"), (2, "RPO"), (3, "LAO")])
    assert results.beam_names(path) == {1: "Field 1", 2: "RPO", 3: "LAO"}


def test_beam_names_from_photon_plan(tmp_path):
    path = _rtplan(tmp_path, [(7, "AP")], sequence="BeamSequence")
    assert results.beam_names(path) == {7: "AP"}


def test_beam_numbers_need_not_match_plan_order(tmp_path):
    """The whole point of the lookup: beam 2 may be listed first, or numbered arbitrarily."""
    path = _rtplan(tmp_path, [(5, "Posterior"), (2, "Anterior")])
    names = results.beam_names(path)
    assert names[5] == "Posterior" and names[2] == "Anterior"


def test_beam_names_missing_plan_is_empty(tmp_path):
    assert results.beam_names(None) == {}
    assert results.beam_names(tmp_path / "absent.dcm") == {}


def test_beam_names_unreadable_plan_is_empty(tmp_path):
    """A corrupt RTPLAN must not take down the results page."""
    bad = tmp_path / "RN.bad.dcm"
    bad.write_text("not dicom")
    assert results.beam_names(bad) == {}


def test_planned_fractions_from_fraction_group(tmp_path):
    path = _rtplan(tmp_path, [(1, "Field 1")], fractions=5)
    assert results.planned_fractions(path) == 5


def test_planned_fractions_missing_or_unreadable_is_none(tmp_path):
    assert results.planned_fractions(None) is None
    assert results.planned_fractions(tmp_path / "absent.dcm") is None
    assert results.planned_fractions(_rtplan(tmp_path, [(1, "Field 1")])) is None


def test_results_are_grouped_by_field(tmp_path):
    for src in (NEUTRON, PROTON):
        (tmp_path / src.name).write_bytes(src.read_bytes())
    # a second field's scorer, which must sort after field 1
    (tmp_path / "topas_field02_gamma_CTV.csv").write_text(
        "# Parameter File: topas_field02.txt\n"
        "# Results for scorer: DoseGamma_CTV\n"
        "# DoseToMedium ( Gy ) : Sum   \n1.0e-9\n"
    )
    found, _ = results.collect_results(tmp_path)
    assert [r.field_number for r in found] == [1, 1, 2]


# --- NaN / bad input ---

def test_nan_sum_is_flagged_as_a_version_problem(tmp_path):
    """A NaN Sum means OpenTOPAS < 4.2.3 corrupted the scorer merge (#49).  It is not a dose,
    and it must never be rendered as one."""
    p = tmp_path / "old.csv"
    p.write_text(
        "# TOPAS Version: 3.9\n"
        "# Results for scorer: AmBDose_Fetus\n"
        "# AmbientDoseEquivalent ( Sv ) : Sum   Standard_Deviation   \n"
        "-nan, 4.148248342996396e-15\n"
    )
    r = parse_scorer_csv(p)
    assert math.isnan(r.raw_sum)
    assert not r.usable
    assert "4.2.3" in r.problem and "#49" in r.problem
    assert r.scaled(None) == (None, None)


def test_infinite_sum_is_flagged(tmp_path):
    p = tmp_path / "inf.csv"
    p.write_text("# Results for scorer: A\n# DoseToMedium ( Gy ) : Sum   \ninf\n")
    assert not parse_scorer_csv(p).usable


@pytest.mark.parametrize("body,match", [
    ("# Results for scorer: A\n1.0\n", "no quantity/column header"),
    ("# DoseToMedium ( Gy ) : Sum   \n", "no data rows"),
    ("# DoseToMedium ( Gy ) : Sum   Standard_Deviation   \n1.0\n", "expected at least"),
    ("# DoseToMedium ( Gy ) : Sum   \nnot-a-number\n", "unparseable row"),
])
def test_bad_csv_raises_results_error(tmp_path, body, match):
    p = tmp_path / "bad.csv"
    p.write_text(body)
    with pytest.raises(ResultsError, match=match):
        parse_scorer_csv(p)


def test_missing_file_raises_results_error(tmp_path):
    with pytest.raises(ResultsError, match="cannot read"):
        parse_scorer_csv(tmp_path / "nope.csv")


# --- plan scaling ---

def test_parses_plan_scaling_from_real_header():
    s = parse_plan_scaling(TOPAS_INPUT)
    assert s.particle_scaling == pytest.approx(953179.27)
    assert s.requested_histories == 20000
    assert s.total_particles == pytest.approx(19063585498)
    # TOPAS runs sum(spotWeight), not REQUESTED_HISTORIES: the weights are integers
    assert s.simulated_histories == 19990


def test_scaling_factor_corrects_for_integer_spot_weights():
    s = parse_plan_scaling(TOPAS_INPUT)
    assert s.factor == pytest.approx(953179.27 * 20000 / 19990)
    assert s.factor == pytest.approx(953656.09, rel=1e-6)
    # the naive TOTAL/REQUESTED ratio understates it
    assert s.factor > s.particle_scaling
    assert s.rounding_correction == pytest.approx(20000 / 19990)


def test_scaling_falls_back_when_spot_weights_absent(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("# REQUESTED_HISTORIES: 1000\n# PARTICLE_SCALING: 42.0\n")
    s = parse_plan_scaling(p)
    assert s.simulated_histories == 0
    assert s.factor == 42.0          # no correction available; do not divide by zero
    assert s.rounding_correction == 1.0


@pytest.mark.parametrize("text", [
    "",                                            # no header at all
    "# TOTAL_NUMBER_OF_PARTICLES: 5\n",            # incomplete
    "# REQUESTED_HISTORIES: abc\n# PARTICLE_SCALING: 1\n",
])
def test_unscalable_header_returns_none(tmp_path, text):
    p = tmp_path / "t.txt"
    p.write_text(text)
    assert parse_plan_scaling(p) is None


def test_missing_topas_input_returns_none(tmp_path):
    assert parse_plan_scaling(tmp_path / "absent.txt") is None


def test_scaled_dose_uses_the_corrected_factor():
    r = parse_scorer_csv(PROTON)
    s = parse_plan_scaling(TOPAS_INPUT)
    dose, _ = r.scaled(s)
    assert dose == pytest.approx(1.355829448712598e-09 * s.factor)
    # sanity: a per-field CTV dose in the mGy range at these (deliberately low) statistics
    assert 1e-4 < dose < 1e-2


def test_uncertainty_is_the_statistical_error_not_the_raw_sd():
    """The reported error is √N·SD/Sum applied to the dose, not SD×factor.  TOPAS's
    Standard_Deviation is the per-history spread and is √N too small as an error on the sum."""
    import math

    r = parse_scorer_csv(PROTON)
    s = parse_plan_scaling(TOPAS_INPUT)
    raw_sum = 1.355829448712598e-09
    raw_sd = 1.271278408327433e-13
    n = s.simulated_histories                       # 19990 for this fixture

    rel = r.relative_uncertainty(s)
    assert rel == pytest.approx(math.sqrt(n) * raw_sd / raw_sum)

    dose, unc = r.scaled(s)
    assert unc == pytest.approx(dose * rel)
    # the relative error is invariant under the plan scaling
    assert unc / dose == pytest.approx(rel)
    # and it is √N larger than naively scaling the raw SD (the old, wrong, behaviour)
    assert unc == pytest.approx(math.sqrt(n) * raw_sd * s.factor)


def test_relative_uncertainty_needs_histories():
    """Without plan scaling there is no N, so no statistical error can be formed."""
    r = parse_scorer_csv(PROTON)
    assert r.relative_uncertainty(None) is None
    _, unc = r.scaled(None)
    assert unc is None


def test_scaled_without_scaling_is_the_raw_value():
    r = parse_scorer_csv(NEUTRON)
    assert r.scaled(None)[0] == pytest.approx(r.raw_sum)


# --- collect_results ---

def test_collect_results_reads_a_run_dir(tmp_path):
    for src in (NEUTRON, PROTON, TOPAS_INPUT):
        (tmp_path / src.name).write_bytes(src.read_bytes())
    found, warnings = results.collect_results(tmp_path)
    assert warnings == []
    assert [r.structure for r in found] == ["BrainStem", "CTV"]   # sorted, stable


def test_collect_results_warns_but_does_not_raise(tmp_path):
    (tmp_path / "good.csv").write_bytes(NEUTRON.read_bytes())
    (tmp_path / "broken.csv").write_text("# nothing useful here\n")
    found, warnings = results.collect_results(tmp_path)
    assert len(found) == 1
    assert len(warnings) == 1 and "broken.csv" in warnings[0]


def test_collect_results_on_empty_dir(tmp_path):
    assert results.collect_results(tmp_path) == ([], [])


def test_scaling_for_uses_the_parameter_file_named_in_the_csv(tmp_path):
    for src in (NEUTRON, TOPAS_INPUT):
        (tmp_path / src.name).write_bytes(src.read_bytes())
    r = parse_scorer_csv(tmp_path / NEUTRON.name)
    s = results.scaling_for(r, tmp_path)
    assert s is not None and s.simulated_histories == 19990


def test_scaling_for_missing_parameter_file(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("# Results for scorer: A\n# DoseToMedium ( Gy ) : Sum   \n1.0\n")
    assert results.scaling_for(parse_scorer_csv(p), tmp_path) is None


def test_humanize_dose_uses_si_prefix_and_shared_scale():
    d = results.humanize_dose(0.008636, 0.00024, "Sv")
    # value and uncertainty share the milli prefix so the pair reads together.
    assert d == {"value": "8.636", "sd": "0.24", "pct": "2.8", "unit": "mSv"}


def test_humanize_dose_steps_down_to_micro():
    d = results.humanize_dose(0.0001626, 1.6e-5, "Gy")
    assert d["value"] == "162.6"
    assert d["unit"] == "µGy"


def test_humanize_dose_keeps_engineering_mantissa_for_fraction_dose():
    # 0.2547 Gy has no integer part, so engineering notation renders it in milligray.
    d = results.humanize_dose(0.2547, 0.0015, "Gy")
    assert d["value"] == "254.7"
    assert d["unit"] == "mGy"


def test_humanize_dose_handles_missing_value_and_uncertainty():
    assert results.humanize_dose(None, None, "Gy")["value"] is None
    no_sd = results.humanize_dose(0.005, None, "Gy")
    assert no_sd["value"] == "5" and no_sd["unit"] == "mGy"
    assert no_sd["sd"] is None and no_sd["pct"] is None


def test_humanize_dose_zero_value_takes_prefix_from_uncertainty():
    d = results.humanize_dose(0.0, 0.00024, "Sv")
    assert d["value"] == "0" and d["unit"] == "µSv"  # 0.00024 Sv = 240 µSv sets the scale
