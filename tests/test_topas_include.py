import hashlib

from pregdos import topas_include


def _block(path: str, content: str) -> str:
    """What dicomexport >= 2.0 writes for ``-S path``: the file's bytes, then one LF."""
    sha = hashlib.sha256(content.encode()).hexdigest()
    return f"# BEGIN INCLUDE_INLINE: {path} sha256={sha}\n{content}\n# END INCLUDE_INLINE: {path}\n"


TABLE = 's:Ge/Patient/ImagingToMaterialConverter = "Schneider"\niv:Gr/Color/PatientTissue25= 3 100 100 100\n'


def test_the_pasted_file_is_returned_exactly():
    """The LF dicomexport adds before END is not part of the file; the SHA-256 proves it."""
    text = "# header\n" + _block("../table.txt", TABLE) + "s:Ge/World/Material = \"Air\"\n"

    (table,) = topas_include.inline_includes(text)

    assert table.path == "../table.txt"
    assert table.content == TABLE
    assert table.sha256 == hashlib.sha256(table.content.encode()).hexdigest()
    assert table.block == _block("../table.txt", TABLE)


def test_a_file_without_a_final_newline_keeps_its_last_line():
    (table,) = topas_include.inline_includes(_block("t.txt", "i:X = 1"))
    assert table.content == "i:X = 1"


def test_an_empty_file_is_still_a_block():
    (empty,) = topas_include.inline_includes(_block("empty.txt", ""))
    assert empty.content == ""


def test_the_table_is_the_first_pasted_file():
    """PregDos passes the table as the first -S; later ones (materials, free code) are not it."""
    text = _block("../table.txt", TABLE) + _block("../materials.txt", "s:Ma/ImplantSteel/X = 1\n")

    assert [i.path for i in topas_include.inline_includes(text)] == ["../table.txt", "../materials.txt"]
    table = topas_include.material_table(text)
    assert table is not None and table.content == TABLE


def test_paths_with_spaces_and_backslashes_survive():
    path = r"..\HUtoMaterial Site.txt"
    table = topas_include.material_table(_block(path, TABLE))
    assert table is not None and table.path == path and table.content == TABLE


def test_a_truncated_block_is_not_a_file():
    text = _block("../table.txt", TABLE).replace("# END INCLUDE_INLINE: ../table.txt\n", "")
    assert topas_include.inline_includes(text) == []


def test_no_block_means_no_table():
    """A field converted by dicomexport 1.x names the table with includeFile instead."""
    assert topas_include.material_table("includeFile = ../table.txt\n") is None


def test_without_inline_includes_keeps_only_dicomexport_s_own_lines():
    inner = "includeFile = nested.txt\n" + TABLE
    text = "# header\n" + _block("../table.txt", inner) + 's:Ge/World/Material = "Air"\n'

    rest = topas_include.without_inline_includes(text)

    assert rest == '# header\ns:Ge/World/Material = "Air"\n'
