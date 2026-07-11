import pytest

from pregdos import structure_metrics


def test_write_mask_prepass_reuses_generated_topas_paths(tmp_path):
    topas = tmp_path / "topas_field01.txt"
    topas.write_text(
        "# generated\n"
        "includeFile                          = ../HUtoMaterialSchneider.txt\n"
        's:Ge/World/Type                      = "TsBox"\n'
        's:Ge/World/Material                  = "Air"\n'
        "d:Ge/World/HLX                       = 100 mm\n"
        "d:Ge/World/HLY                       = 100 mm\n"
        "d:Ge/World/HLZ                       = 100 mm\n"
        's:Ge/Patient/Parent                  = "World"\n'
        's:Ge/Patient/Type                    = "TsDicomPatient"\n'
        's:Ge/Patient/DicomDirectory          = "../dicom"\n'
        'sv:Ge/Patient/DicomModalityTags      = 1 "CT"\n'
        "d:Rt/Plan/IsoCenterX                 = 0 mm\n"
    )

    name = structure_metrics.write_mask_prepass(topas, ["CTV", "Brain Stem"])

    assert name == "structure_mask_prepass.txt"
    text = (tmp_path / name).read_text()
    assert "includeFile                          = ../HUtoMaterialSchneider.txt" in text
    assert 's:Ge/Patient/DicomDirectory          = "../dicom"' in text
    assert 'b:Sc/PregDosMask_CTV/SetBinToMinusOneIfNotInRTStructure = "True"' in text
    assert 'sv:Sc/PregDosMask_CTV/OnlyIncludeIfInRTStructure = 1 "CTV"' in text
    assert 's:Sc/PregDosMask_Brain_Stem/OutputFile = "structure_mask_Brain_Stem"' in text


def test_energy_deposit_to_gy_uses_structure_mass():
    metrics = {"structures": {"CTV": {"mass_g": 2.0}}}

    dose, sd = structure_metrics.energy_deposit_to_gy(metrics, "CTV", "MeV", 10.0, 1.0)

    assert dose == pytest.approx(10.0 * structure_metrics.MEV_PER_G_TO_GY / 2.0)
    assert sd == pytest.approx(1.0 * structure_metrics.MEV_PER_G_TO_GY / 2.0)


def test_energy_deposit_to_gy_requires_mev_and_mass():
    metrics = {"structures": {"CTV": {"mass_g": 2.0}}}

    assert structure_metrics.energy_deposit_to_gy(metrics, "CTV", "Gy", 10.0, None) is None
    assert structure_metrics.energy_deposit_to_gy(metrics, "Missing", "MeV", 10.0, None) is None


def test_fluence_volume_correction_factor_is_patient_over_structure_volume():
    metrics = {
        "patient": {"volume_cm3": 1000.0},
        "structures": {"BrainStem": {"volume_cm3": 25.0}},
    }

    assert structure_metrics.fluence_volume_correction_factor(metrics, "BrainStem") == pytest.approx(40.0)


def test_ensure_metrics_reports_failed_prepass(tmp_path):
    (tmp_path / "structure_mask_prepass.exit_code").write_text("1\n")

    metrics, warnings = structure_metrics.ensure_metrics(tmp_path)

    assert metrics is None
    assert warnings and "pre-pass failed" in warnings[0]
