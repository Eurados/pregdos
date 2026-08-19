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


def test_prepass_structures_keeps_mask_file_with_its_scorer(tmp_path):
    (tmp_path / "structure_mask_prepass.txt").write_text(
        's:Sc/PregDosMask_BrainStem/Quantity = "StepCount"\n'
        'sv:Sc/PregDosMask_BrainStem/OnlyIncludeIfInRTStructure = 1 "BrainStem"\n'
        's:Sc/PregDosMask_BrainStem/OutputFile = "structure_mask_BrainStem"\n'
        's:Sc/PregDosMask_CTV/Quantity = "StepCount"\n'
        'sv:Sc/PregDosMask_CTV/OnlyIncludeIfInRTStructure = 1 "CTV"\n'
        's:Sc/PregDosMask_CTV/OutputFile = "structure_mask_CTV"\n'
    )

    assert structure_metrics._prepass_structures(tmp_path) == [
        ("BrainStem", "BrainStem"),
        ("CTV", "CTV"),
    ]


def _prepass_with_masks(tmp_path, structures=("CTV", "fetus")):
    """A run directory as it looks the moment the pre-pass exits: inputs plus masks."""
    lines = []
    for name in structures:
        lines += [
            f's:Sc/PregDosMask_{name}/Quantity = "StepCount"',
            f'sv:Sc/PregDosMask_{name}/OnlyIncludeIfInRTStructure = 1 "{name}"',
            f's:Sc/PregDosMask_{name}/OutputFile = "structure_mask_{name}"',
        ]
    (tmp_path / "structure_mask_prepass.txt").write_text("\n".join(lines) + "\n")
    for name in structures:
        (tmp_path / f"structure_mask_{name}.bin").write_bytes(b"\x00" * 64)
        (tmp_path / f"structure_mask_{name}.binheader").write_text("header\n")


def test_discard_masks_removes_every_prepass_mask(tmp_path):
    """The masks cost 8 bytes per CT voxel and nothing reads them after the metrics."""
    _prepass_with_masks(tmp_path)

    structure_metrics._discard_masks(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["structure_mask_prepass.txt"]


def test_discard_masks_leaves_everything_else_alone(tmp_path):
    _prepass_with_masks(tmp_path, structures=("CTV",))
    (tmp_path / "topas_field01.txt").write_text("# field")
    (tmp_path / "structure_metrics.json").write_text("{}")

    structure_metrics._discard_masks(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "structure_mask_prepass.txt",
        "structure_metrics.json",
        "topas_field01.txt",
    ]


def test_discard_masks_tolerates_a_mask_that_is_already_gone(tmp_path):
    """A rerun may have cleared them first; that is not an error."""
    _prepass_with_masks(tmp_path, structures=("CTV",))
    (tmp_path / "structure_mask_CTV.bin").unlink()

    structure_metrics._discard_masks(tmp_path)   # must not raise

    assert not (tmp_path / "structure_mask_CTV.binheader").exists()


def test_compute_metrics_writes_the_json_then_drops_the_masks(tmp_path, monkeypatch):
    """End to end: the numbers survive in the JSON, the gigabytes do not."""
    import numpy as np

    _prepass_with_masks(tmp_path, structures=("CTV",))
    prepass = tmp_path / "structure_mask_prepass.txt"
    prepass.write_text(
        "includeFile = ../spr.txt\n"
        's:Ge/Patient/DicomDirectory = "../dicom"\n' + prepass.read_text()
    )
    # One CT voxel outside the structure (-1) and three inside.
    (tmp_path / "structure_mask_CTV.bin").write_bytes(
        np.array([-1.0, 0.0, 0.0, 0.0], dtype="<f8").tobytes())

    ct = structure_metrics.CTData(
        hu=np.zeros((1, 2, 2)), voxel_volume_cm3=1.0, dicom_directory=tmp_path)
    monkeypatch.setattr(structure_metrics, "_load_ct", lambda _d: ct)
    monkeypatch.setattr(structure_metrics, "_density_from_hu", lambda hu, _t: np.ones_like(hu))

    payload = structure_metrics.compute_metrics(tmp_path)

    assert payload["structures"]["CTV"]["voxel_count"] == 3
    assert structure_metrics.load_metrics(tmp_path)["structures"]["CTV"]["voxel_count"] == 3
    assert not list(tmp_path.glob("structure_mask_CTV.bin*"))


def test_compute_metrics_keeps_the_masks_when_it_fails(tmp_path, monkeypatch):
    """A mask that does not match the CT is a bug worth inspecting -- do not delete it."""
    import numpy as np

    _prepass_with_masks(tmp_path, structures=("CTV",))
    prepass = tmp_path / "structure_mask_prepass.txt"
    prepass.write_text(
        "includeFile = ../spr.txt\n"
        's:Ge/Patient/DicomDirectory = "../dicom"\n' + prepass.read_text()
    )
    (tmp_path / "structure_mask_CTV.bin").write_bytes(
        np.array([0.0, 0.0], dtype="<f8").tobytes())      # 2 bins, CT has 4

    ct = structure_metrics.CTData(
        hu=np.zeros((1, 2, 2)), voxel_volume_cm3=1.0, dicom_directory=tmp_path)
    monkeypatch.setattr(structure_metrics, "_load_ct", lambda _d: ct)
    monkeypatch.setattr(structure_metrics, "_density_from_hu", lambda hu, _t: np.ones_like(hu))

    with pytest.raises(structure_metrics.StructureMetricsError):
        structure_metrics.compute_metrics(tmp_path)

    assert (tmp_path / "structure_mask_CTV.bin").exists()


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
