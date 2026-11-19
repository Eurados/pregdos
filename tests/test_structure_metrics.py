import pytest
import threading

from pregdos import structure_metrics


@pytest.mark.parametrize("value, expected", [
    # A Windows run generates backslash separators (os.path.relpath on nt).  Parsing these
    # with shlex would eat the separator as an escape and silently lose the table.
    (r"..\HUtoMaterialSchneider.txt", r"..\HUtoMaterialSchneider.txt"),
    (r'"..\HUtoMaterial Site.txt"', r"..\HUtoMaterial Site.txt"),
    (r'"..\HUtoMaterial Site.txt"  # selected', r"..\HUtoMaterial Site.txt"),
    # POSIX, the common case, with and without quoting and a trailing comment.
    ("../HUtoMaterialSchneider.txt", "../HUtoMaterialSchneider.txt"),
    ('"../HUtoMaterialSchneider.txt"', "../HUtoMaterialSchneider.txt"),
    ("../HUtoMaterialSchneider.txt  # selected", "../HUtoMaterialSchneider.txt"),
])
def test_quoted_or_raw_keeps_windows_separators_and_drops_comments(value, expected):
    assert structure_metrics.quoted_or_raw(value) == expected


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


def _pasted(path: str, content: str) -> str:
    """What dicomexport >= 2.0 writes for ``-S path``: the file's bytes, then one LF."""
    import hashlib
    sha = hashlib.sha256(content.encode()).hexdigest()
    return f"# BEGIN INCLUDE_INLINE: {path} sha256={sha}\n{content}\n# END INCLUDE_INLINE: {path}\n"


_GEOMETRY = (
    's:Ge/World/Type                      = "TsBox"\n'
    's:Ge/Patient/Type                    = "TsDicomPatient"\n'
    's:Ge/Patient/DicomDirectory          = "../dicom"\n'
)


def test_write_mask_prepass_copies_the_pasted_table_exactly_once(tmp_path):
    """TOPAS stops on a parameter defined twice in one file.  A line inside the table -- here
    an includeFile of its own -- must not be copied a second time as a parameter, and the
    other pasted files (materials, free code) stay out of the mask pass."""
    table = 'includeFile = nested.txt\ns:Ge/Patient/ImagingToMaterialConverter = "Schneider"\n'
    free_code = 's:So/Extra/Type = "Beam"\n'
    topas = tmp_path / "topas_field01.txt"
    topas.write_text(
        "# generated\n" + _pasted("../table.txt", table) + _pasted("../extra.txt", free_code) + _GEOMETRY
    )

    text = (tmp_path / structure_metrics.write_mask_prepass(topas, ["CTV"])).read_text()

    assert text.count("# BEGIN INCLUDE_INLINE: ../table.txt") == 1
    assert text.count("includeFile = nested.txt") == 1
    assert text.count("ImagingToMaterialConverter") == 1
    assert "extra.txt" not in text and "So/Extra" not in text
    assert 's:Ge/Patient/DicomDirectory          = "../dicom"' in text


def test_compute_metrics_reads_densities_from_the_pasted_table(tmp_path, monkeypatch):
    """The study's copy of the table is not needed: the pre-pass carries it."""
    from pathlib import Path

    import dicomexport
    import numpy as np

    shipped = Path(dicomexport.__file__).parent / "defaults/ct_to_material/HUtoMaterialSchneider.txt"
    _prepass_with_masks(tmp_path, structures=("CTV",))
    prepass = tmp_path / "structure_mask_prepass.txt"
    prepass.write_text(
        _pasted("../gone.txt", shipped.read_text()) + _GEOMETRY + prepass.read_text()
    )
    (tmp_path / "structure_mask_CTV.bin").write_bytes(
        np.array([-1.0, 0.0, 0.0, 0.0], dtype="<f8").tobytes())
    hu = np.array([[[-1000.0, 0.0], [40.0, 1000.0]]])
    monkeypatch.setattr(structure_metrics, "_load_ct", lambda _d: structure_metrics.CTData(
        hu=hu, voxel_volume_cm3=1.0, dicom_directory=tmp_path))

    payload = structure_metrics.compute_metrics(tmp_path)

    expected = structure_metrics._density_from_hu(hu, shipped)
    assert payload["structures"]["CTV"]["mass_g"] == pytest.approx(expected.ravel()[1:].sum())
    assert payload["material_table"] == "../gone.txt"
    assert len(payload["material_table_sha256"]) == 64


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


def test_compute_metrics_writes_the_json_and_keeps_the_masks(tmp_path, monkeypatch):
    """The masks stay: they cost only disk, and the run directory is reaped on retention.

    Deleting them once looked like a cheap win and instead lost a structure's metrics for
    good when two page renders computed at the same time."""
    import numpy as np

    _prepass_with_masks(tmp_path, structures=("CTV",))
    prepass = tmp_path / "structure_mask_prepass.txt"
    prepass.write_text(
        "includeFile = ../material.txt\n"
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
    reloaded = structure_metrics.load_metrics(tmp_path)
    assert reloaded is not None
    assert reloaded["structures"]["CTV"]["voxel_count"] == 3
    assert (tmp_path / "structure_mask_CTV.bin").exists()


def _patch_ct(monkeypatch, tmp_path, shape=(1, 2, 2)):
    import numpy as np
    ct = structure_metrics.CTData(
        hu=np.zeros(shape), voxel_volume_cm3=1.0, dicom_directory=tmp_path)
    monkeypatch.setattr(structure_metrics, "_load_ct", lambda _d: ct)
    monkeypatch.setattr(structure_metrics, "_density_from_hu", lambda hu, _t: np.ones_like(hu))


def test_ensure_metrics_recomputes_an_incomplete_cache_while_masks_remain(tmp_path, monkeypatch):
    import numpy as np

    _prepass_with_masks(tmp_path, structures=("CTV",))
    prepass = tmp_path / "structure_mask_prepass.txt"
    prepass.write_text(
        "includeFile = ../material.txt\n"
        's:Ge/Patient/DicomDirectory = "../dicom"\n' + prepass.read_text()
    )
    (tmp_path / "structure_mask_CTV.bin").write_bytes(
        np.array([-1.0, 0.0, 0.0, 0.0], dtype="<f8").tobytes())
    (tmp_path / "structure_mask_prepass.exit_code").write_text("0\n")
    (tmp_path / structure_metrics.METRICS_FILE).write_text('{"structures": {}}')
    _patch_ct(monkeypatch, tmp_path)

    metrics, warnings = structure_metrics.ensure_metrics(tmp_path)

    assert warnings == []
    assert metrics is not None
    assert metrics["structures"]["CTV"]["voxel_count"] == 3


def test_ensure_metrics_says_what_is_missing_once_the_masks_are_gone(tmp_path):
    _prepass_with_masks(tmp_path, structures=("CTV", "Ovary"))
    for name in ("CTV", "Ovary"):
        (tmp_path / f"structure_mask_{name}.bin").unlink()
    (tmp_path / structure_metrics.METRICS_FILE).write_text('{"structures": {}}')

    metrics, warnings = structure_metrics.ensure_metrics(tmp_path)

    assert metrics == {"structures": {}}
    assert len(warnings) == 1
    assert "CTV" in warnings[0] and "Ovary" in warnings[0] and "pre-pass" in warnings[0]


def test_compute_metrics_rejects_a_mask_that_does_not_match_the_ct(tmp_path, monkeypatch):
    """A mask with the wrong bin count is a bug, not something to score around."""
    import numpy as np

    _prepass_with_masks(tmp_path, structures=("CTV",))
    prepass = tmp_path / "structure_mask_prepass.txt"
    prepass.write_text(
        "includeFile = ../material.txt\n"
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


def _metrics_run_dir(root, monkeypatch, structures=("A_PTV1", "Ovary160")):
    """A finished pre-pass: input, exit code, and one readable mask per structure."""
    import numpy as np

    root.mkdir(parents=True, exist_ok=True)
    _prepass_with_masks(root, structures=structures)
    prepass = root / "structure_mask_prepass.txt"
    prepass.write_text(
        "includeFile = ../material.txt\n"
        's:Ge/Patient/DicomDirectory = "../dicom"\n' + prepass.read_text()
    )
    for name in structures:
        (root / f"structure_mask_{name}.bin").write_bytes(
            np.array([-1.0, 0.0, 0.0, 0.0], dtype="<f8").tobytes())
    (root / "structure_mask_prepass.exit_code").write_text("0\n")
    _patch_ct(monkeypatch, root)
    return root


def test_ensure_metrics_serialises_computation(monkeypatch, tmp_path):
    """Deterministic: while one caller is inside compute_metrics, the other must wait.

    Without mutual exclusion both callers do the whole job, and the slower one rebuilds a
    partial result from masks the faster one has already discarded -- silently dropping a
    structure for the life of the run.
    """
    run_dir = _metrics_run_dir(tmp_path / "run", monkeypatch)

    inside = threading.Event()
    release = threading.Event()
    real_compute = structure_metrics.compute_metrics
    first_call = threading.Lock()
    blocked = []

    def blocking_compute(rd):
        # Only the *first* caller blocks.  If both did, an unlocked second caller would stall
        # here too and the test would pass without any mutual exclusion at all.
        with first_call:
            mine = not blocked
            blocked.append(True)
        if mine:
            inside.set()
            assert release.wait(timeout=5), "second caller never released the first"
        return real_compute(rd)

    monkeypatch.setattr(structure_metrics, "compute_metrics", blocking_compute)

    first = threading.Thread(target=lambda: structure_metrics.ensure_metrics(run_dir))
    first.start()
    assert inside.wait(timeout=5), "first caller never reached compute_metrics"

    second_done = threading.Event()
    threading.Thread(
        target=lambda: (structure_metrics.ensure_metrics(run_dir), second_done.set()),
    ).start()

    # The first caller still holds the lock, so the second cannot have finished.
    assert not second_done.wait(timeout=0.5), "second caller was not serialised"

    release.set()
    first.join(timeout=5)
    assert second_done.wait(timeout=5)
    written = structure_metrics.load_metrics(run_dir)
    assert written is not None
    assert sorted(written["structures"]) == ["A_PTV1", "Ovary160"]


def test_concurrent_ensure_metrics_does_not_lose_a_structure(tmp_path, monkeypatch):
    """The observed regression, exercised over several rounds because it is a race.

    The winner wrote complete metrics and discarded the masks; the loser, already past its
    own file checks, rebuilt a partial result from masks that no longer existed and
    overwrote the good one.
    """
    for round_no in range(8):
        run_dir = _metrics_run_dir(tmp_path / f"run{round_no}", monkeypatch)
        results = []
        barrier = threading.Barrier(2)

        def render():
            barrier.wait()                   # start both inside the window
            results.append(structure_metrics.ensure_metrics(run_dir))

        threads = [threading.Thread(target=render) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for metrics, warnings in results:
            assert warnings == [], f"round {round_no}: {warnings}"
            assert metrics is not None
            assert sorted(metrics["structures"]) == ["A_PTV1", "Ovary160"]
        on_disk = structure_metrics.load_metrics(run_dir)
        assert on_disk is not None
        assert sorted(on_disk["structures"]) == ["A_PTV1", "Ovary160"], f"round {round_no}"


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


def test_a_table_topas_would_read_out_of_bounds_gives_no_mass(tmp_path):
    """#131: rather than a mass that disagrees with the run.  The old parser guessed where the
    correction vector starts; this table's sections start at -1024 without MinImagingValue."""
    import numpy as np

    table = tmp_path / "SPRtoMaterial.txt"
    table.write_text(
        's:Ge/Patient/ImagingToMaterialConverter = "Schneider"\n'
        "iv:Ge/Patient/SchneiderHounsfieldUnitSections = 2 -1024 -1022\n"
        "uv:Ge/Patient/SchneiderDensityOffset = 1 1.0\n"
        "uv:Ge/Patient/SchneiderDensityFactor = 1 0.0\n"
        "uv:Ge/Patient/SchneiderDensityFactorOffset = 1 0.0\n"
        "uv:Ge/Patient/DensityCorrection = 2 1.0 1.0\n")

    with pytest.raises(structure_metrics.StructureMetricsError, match="MinImagingValue = -1024"):
        structure_metrics._density_from_hu(np.zeros(3), table)


# --- Overrides inside scored structures (Codex review of #132) ---------------------------------

def test_overridden_structures_are_masked_alongside_scored_ones(tmp_path):
    topas = tmp_path / "topas_field01.txt"
    topas.write_text('s:Ge/Patient/DicomDirectory = "../dicom"\n')

    structure_metrics.write_mask_prepass(topas, ["CTV"], overridden=["Hip implant", "CTV"])

    text = (tmp_path / "structure_mask_prepass.txt").read_text()
    assert 'OnlyIncludeIfInRTStructure = 1 "CTV"' in text
    assert 'OnlyIncludeIfInRTStructure = 1 "Hip implant"' in text
    assert text.count('OnlyIncludeIfInRTStructure = 1 "CTV"') == 1


def test_overrides_alone_make_no_prepass(tmp_path):
    """The masks only serve to compare with scored structures."""
    topas = tmp_path / "topas_field01.txt"
    topas.write_text('s:Ge/Patient/DicomDirectory = "../dicom"\n')
    assert structure_metrics.write_mask_prepass(topas, [], overridden=["Hip implant"]) is None


def test_metrics_record_how_much_of_a_structure_an_override_covers(tmp_path, monkeypatch):
    import numpy as np

    _prepass_with_masks(tmp_path, structures=("Uterus", "Implant", "Marker"))
    prepass = tmp_path / "structure_mask_prepass.txt"
    prepass.write_text('s:Ge/Patient/DicomDirectory = "../dicom"\nincludeFile = ../t.txt\n' + prepass.read_text())
    (tmp_path / "topas_field01.txt").write_text(
        "# CONFIG_DIR: /cfg\n# MATERIAL_OVERRIDE: Implant=G4_Ti\n# MATERIAL_OVERRIDE: Marker=G4_AIR\n")
    masks = {"Uterus": [0, 0, 0, -1], "Implant": [-1, 0, -1, -1], "Marker": [-1, -1, -1, 0]}
    for name, values in masks.items():
        (tmp_path / f"structure_mask_{name}.bin").write_bytes(np.array(values, dtype="<f8").tobytes())
    _patch_ct(monkeypatch, tmp_path)

    payload = structure_metrics.compute_metrics(tmp_path)

    assert payload["structures"]["Uterus"]["override_overlaps"] == [
        {"override": "Implant", "material": "G4_Ti", "voxel_count": 1, "volume_cm3": 1.0}]
    # An override covers itself entirely; one outside a structure is not listed.
    assert [o["override"] for o in payload["structures"]["Implant"]["override_overlaps"]] == ["Implant"]
    assert [o["override"] for o in payload["structures"]["Marker"]["override_overlaps"]] == ["Marker"]
