from pregdos import provenance

SHA_TABLE = "7a4ce0977ccae901a16755dc4895c9a27fed9631605b26594b6551d5451c3a9b"
SHA_MATERIALS = "e49e842bd5a176c478aefcd6bf2a3c19f41b1d71171fc160df3aab37894fdaf3"

# As dicomexport 2.0.0 writes it, including the blank line inside the header.
FIELD = f"""\
# Topas input file for field 1
# SPOT_MODE: sources
# SPOTS: 659
# RUNS: 10
# EVENTS_PER_RUN: 15
#

# Generated 2026-11-18 19:03:00 by user 'pregdos'
# using dicomexport 2.0.0
# CONFIG_DIR: /etc/dicomexport (--config-dir)
# BEAM_MODEL: ../DCPT_beam_model__v5.csv
# RANGE_SHIFTER_CATALOG: /etc/dicomexport/range_shifters.csv
# INCLUDE_INLINE: ../HUtoMaterialSchneider.txt sha256={SHA_TABLE}
# INCLUDE_INLINE: /etc/dicomexport/materials.txt sha256={SHA_MATERIALS}
# MATERIAL_OVERRIDE: Hip implant=ImplantSteel
# MATERIAL_OVERRIDE: BODY=G4_WATER
#
# BEGIN INCLUDE_INLINE: ../HUtoMaterialSchneider.txt sha256={SHA_TABLE}
# A pasted file's own comments are not dicomexport's header:
# MATERIAL_OVERRIDE: Decoy=Lead
# INCLUDE: /decoy.txt
s:Ge/Patient/ImagingToMaterialConverter = "Schneider"

# END INCLUDE_INLINE: ../HUtoMaterialSchneider.txt
s:Ge/World/Material = "Air"
"""


def test_the_header_is_read_and_pasted_files_are_not():
    record = provenance.parse(FIELD)
    assert record.dicomexport == "2.0.0"
    assert record.config_dir == "/etc/dicomexport (--config-dir)"
    assert record.beam_model == "../DCPT_beam_model__v5.csv"
    assert record.range_shifter_catalog == "/etc/dicomexport/range_shifters.csv"
    assert [(i.path, i.sha256) for i in record.includes] == [
        ("../HUtoMaterialSchneider.txt", SHA_TABLE), ("/etc/dicomexport/materials.txt", SHA_MATERIALS)]
    assert record.material_overrides == ["Hip implant=ImplantSteel", "BODY=G4_WATER"]
    assert (record.spot_mode, record.runs) == ("sources", 10)


def test_an_included_file_is_labelled_by_name_and_hash_prefix():
    assert provenance.Include("/etc/dicomexport/materials.txt", SHA_MATERIALS).label == "materials.txt (e49e842b)"
    assert provenance.Include(r"..\table.txt").label == "table.txt"


def test_the_layout_names_batches_and_a_single_batch():
    assert provenance.ConversionRecord(spot_mode="sources", runs=10).layout == "sources, 10 batches"
    assert provenance.ConversionRecord(spot_mode="sources", runs=1).layout == "sources, single batch (no error bars)"
    assert provenance.ConversionRecord(spot_mode="timefeature", runs=659).layout == "timefeature"


def test_a_field_from_dicomexport_1_has_no_record():
    assert provenance.parse("# Topas input file for field 1\n# using dicomexport 1.5.1\nincludeFile = ../t.txt\n") is None


def test_the_record_survives_run_json():
    record = provenance.parse(FIELD)
    assert provenance.ConversionRecord.from_json(record.to_json()) == record


def test_a_damaged_record_reads_as_none_or_as_far_as_it_goes():
    """run.json is read on every page render; nothing in it may raise."""
    assert provenance.ConversionRecord.from_json("garbage") is None
    record = provenance.ConversionRecord.from_json(
        {"beam_model": 3, "includes": [{"path": "a.txt", "sha256": None}, "junk"], "runs": True})
    assert record.beam_model == "" and record.runs == 0
    assert [(i.path, i.sha256) for i in record.includes] == [("a.txt", "")]
