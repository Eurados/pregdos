import textwrap

from pregdos.topas_scorer import (
    ScorerConfig,
    ScorerEntry,
    ScorerType,
    UserDefinedGrid,
    VolumeType,
    append_scorers,
    scorer_block,
    scorer_config_from_form,
)


# ---------------------------------------------------------------------------
# scorer_block — output syntax checks
# ---------------------------------------------------------------------------

def _make_entry(scorer_type, volume_type=VolumeType.STRUCTURE, structure="fetus"):
    return ScorerEntry(scorer_type=scorer_type, volume_type=volume_type, structure_name=structure)


def test_neutron_scorer_block_structure():
    entry = _make_entry(ScorerType.NEUTRON_DOSE_EQUIV)
    block = scorer_block(entry, "topas_field01")
    assert 's:Sc/AmBDose_fetus/Quantity' in block
    assert '"AmbientDoseEquivalent"' in block
    assert 'OnlyIncludeParticlesNamed' in block
    assert '"neutron"' in block
    assert 'OnlyIncludeIfInRTStructure' in block
    assert '"fetus"' in block
    assert 'FluenceToDoseConversionEnergies' in block
    assert 'FluenceToDoseConversionValues' in block
    assert 'topas_field01_neutron' in block
    assert 'XBins                                   = 1' in block


def test_gamma_scorer_block_structure():
    entry = _make_entry(ScorerType.GAMMA_DOSE, structure="uterus")
    block = scorer_block(entry, "topas_field02")
    assert '"EnergyDeposit"' in block
    assert "ReferencedDicomPatient" not in block
    assert 'OnlyIncludeIfParticleOrAncestorNamed' in block
    assert '"gamma"' in block
    assert '"uterus"' in block
    assert 'topas_field02_gamma' in block


def test_proton_primary_scorer_block():
    entry = _make_entry(ScorerType.PROTON_PRIMARY)
    block = scorer_block(entry, "topas_field01")
    assert '"EnergyDeposit"' in block
    assert 'OnlyIncludeParticlesNamed' in block
    assert '"proton"' in block
    assert 'OnlyIncludeIfParticleOrAncestorNotNamed' in block
    assert 'topas_field01_proton_primary' in block


def test_proton_secondary_scorer_block():
    entry = _make_entry(ScorerType.PROTON_SECONDARY)
    block = scorer_block(entry, "topas_field01")
    assert '"EnergyDeposit"' in block
    assert 'OnlyIncludeParticlesNamed' in block
    assert '"proton"' in block
    # Must use Ancestor*Named (include neutron ancestors), not Not
    assert 'OnlyIncludeIfParticleOrAncestorNamed' in block
    assert 'OnlyIncludeIfParticleOrAncestorNotNamed' not in block
    assert 'topas_field01_proton_secondary' in block


def test_user_grid_scorer_uses_scoring_grid_component():
    grid = UserDefinedGrid(size_x_mm=150, size_y_mm=100, size_z_mm=80, nx=15, ny=10, nz=8)
    entry = _make_entry(ScorerType.GAMMA_DOSE, volume_type=VolumeType.USER_GRID)
    block = scorer_block(entry, "topas_field01", grid=grid)
    assert '"DoseToMedium"' in block
    assert "ReferencedDicomPatient" in block
    assert '"ScoringGrid"' in block
    assert 'OnlyIncludeIfInRTStructure' not in block
    assert 'XBins                                   = 15' in block
    assert 'YBins                                   = 10' in block
    assert 'ZBins                                   = 8' in block


# ---------------------------------------------------------------------------
# append_scorers — file post-processing
# ---------------------------------------------------------------------------

_SCORER_HEADER = (
    "##############################################\n"
    "###       S C O R E R    S E T U P         ###\n"
    "##############################################\n"
)

_TIME_HEADER = (
    "##############################################\n"
    "###  T  I  M  E    F  E  A  T  U  R  E  S  ###\n"
    "##############################################\n"
)

_INFIELD_BLOCK = textwrap.dedent("""\
    s:Sc/Dose/Quantity                   = "DoseToWater"
    b:Sc/Dose/PreCalculateStoppingPowerRatios = "True"
    s:Sc/Dose/Component                  = "Patient/RTDoseGrid"
    s:Sc/Dose/OutputType                 = "DICOM"
    s:Sc/Dose/OutputFile                 = "topas_field01"

""")

_TIME_BODY = textwrap.dedent("""\
    s:Tf/spotWeight/Function = "Step"
    dv:Tf/spotWeight/Values  = 2 1000 2000
""")


def _make_topas_file(tmp_path, name="topas_field01.txt", with_time=True):
    content = "# header\n\n"
    content += _SCORER_HEADER + _INFIELD_BLOCK
    if with_time:
        content += _TIME_HEADER + _TIME_BODY
    p = tmp_path / name
    p.write_text(content)
    return p


def test_append_scorers_noop_when_keep_infield_and_no_scorers(tmp_path):
    p = _make_topas_file(tmp_path)
    original = p.read_text()
    config = ScorerConfig(scorers=[], keep_infield=True)
    append_scorers(str(p), config)
    assert p.read_text() == original


def test_append_scorers_adds_neutron_scorer(tmp_path):
    p = _make_topas_file(tmp_path)
    config = ScorerConfig(
        scorers=[_make_entry(ScorerType.NEUTRON_DOSE_EQUIV)],
        keep_infield=True,
    )
    append_scorers(str(p), config)
    result = p.read_text()
    assert "AmBDose_fetus" in result
    assert "DoseToWater" in result  # in-field scorer preserved
    assert _TIME_BODY in result     # time features preserved


def test_append_scorers_removes_infield_when_requested(tmp_path):
    p = _make_topas_file(tmp_path)
    config = ScorerConfig(
        scorers=[_make_entry(ScorerType.GAMMA_DOSE)],
        keep_infield=False,
    )
    append_scorers(str(p), config)
    result = p.read_text()
    assert "DoseToWater" not in result
    assert "DoseGamma_fetus" in result
    assert _TIME_BODY in result


def test_append_scorers_multiple_scorers(tmp_path):
    p = _make_topas_file(tmp_path)
    config = ScorerConfig(
        scorers=[
            _make_entry(ScorerType.GAMMA_DOSE),
            _make_entry(ScorerType.NEUTRON_DOSE_EQUIV),
            _make_entry(ScorerType.PROTON_PRIMARY),
        ],
        keep_infield=False,
    )
    append_scorers(str(p), config)
    result = p.read_text()
    assert "DoseGamma_fetus" in result
    assert "AmBDose_fetus" in result
    assert "DoseProtonPrimary_fetus" in result


def test_append_scorers_includes_grid_geometry(tmp_path):
    p = _make_topas_file(tmp_path)
    grid = UserDefinedGrid(100, 100, 100, 10, 10, 10)
    config = ScorerConfig(
        scorers=[_make_entry(ScorerType.GAMMA_DOSE, volume_type=VolumeType.USER_GRID)],
        keep_infield=False,
        grid=grid,
    )
    append_scorers(str(p), config)
    result = p.read_text()
    assert "ScoringGrid" in result
    assert '"ScoringGrid"' in result


def test_append_scorers_no_existing_scorer_section(tmp_path):
    p = tmp_path / "topas_field01.txt"
    p.write_text("# minimal file\ns:Ge/World/Type = \"TsBox\"\n")
    config = ScorerConfig(
        scorers=[_make_entry(ScorerType.NEUTRON_DOSE_EQUIV)],
        keep_infield=False,
    )
    append_scorers(str(p), config)
    result = p.read_text()
    assert "AmBDose_fetus" in result


# ---------------------------------------------------------------------------
# scorer_config_from_form
# ---------------------------------------------------------------------------

class FakeForm(dict):
    def get(self, key, default=None):
        v = self[key] if key in self else default
        if isinstance(v, list):
            return v[0] if v else default
        return v

    def getlist(self, key):
        v = self[key] if key in self else None
        if v is None:
            return []
        if isinstance(v, list):
            return v
        return [v]


def test_scorer_config_from_form_no_scorers_keep_infield():
    form = FakeForm({"keep_infield": "1"})
    config = scorer_config_from_form(form)
    assert config.keep_infield is True
    assert config.scorers == []


def test_scorer_config_from_form_selects_neutron_scorer():
    form = FakeForm({
        "keep_infield": "1",
        "score_neutron": "fetus",
    })
    config = scorer_config_from_form(form)
    assert len(config.scorers) == 1
    assert config.scorers[0].scorer_type == ScorerType.NEUTRON_DOSE_EQUIV
    assert config.scorers[0].volume_type == VolumeType.STRUCTURE
    assert config.scorers[0].structure_name == "fetus"


def test_scorer_config_from_form_multiple_structures():
    form = FakeForm({
        "score_gamma": ["fetus", "uterus"],
    })
    config = scorer_config_from_form(form)
    assert len(config.scorers) == 2
    assert all(e.scorer_type == ScorerType.GAMMA_DOSE for e in config.scorers)
    assert {e.structure_name for e in config.scorers} == {"fetus", "uterus"}


def test_scorer_config_from_form_no_grid():
    form = FakeForm({
        "score_gamma": "fetus",
    })
    config = scorer_config_from_form(form)
    assert config.grid is None
