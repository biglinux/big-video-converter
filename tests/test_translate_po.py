import importlib.util
import json
from pathlib import Path

import polib
import pytest

spec = importlib.util.spec_from_file_location(
    "translate_po", Path(__file__).resolve().parents[1] / "tools/translate-po.py"
)
translate_po = importlib.util.module_from_spec(spec)
spec.loader.exec_module(translate_po)

CATALOG = r"""msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

#, fuzzy
msgid "{0} folder could not be read."
msgid_plural "{0} folders could not be read."
msgstr[0] "old"
msgstr[1] "old"
"""


def test_a_plural_entry_takes_every_form(tmp_path):
    po, answers = tmp_path / "pt.po", tmp_path / "pt.json"
    po.write_text(CATALOG, encoding="utf-8")
    translate_po.export(str(po), str(answers))
    exported = json.loads(answers.read_text())
    assert exported == {"{0} folder could not be read.": ["", ""]}

    exported["{0} folder could not be read."] = "{0} pasta não pôde ser lida."
    answers.write_text(json.dumps(exported), encoding="utf-8")
    translate_po.apply(str(po), str(answers))
    entry = polib.pofile(str(po))[0]
    assert "fuzzy" in entry.flags  # a single string is not a plural translation

    exported["{0} folder could not be read."] = [
        "{0} pasta não pôde ser lida.",
        "{0} pastas não puderam ser lidas.",
    ]
    answers.write_text(json.dumps(exported), encoding="utf-8")
    translate_po.apply(str(po), str(answers))
    entry = polib.pofile(str(po))[0]
    assert "fuzzy" not in entry.flags
    assert entry.msgstr_plural == {
        0: "{0} pasta não pôde ser lida.",
        1: "{0} pastas não puderam ser lidas.",
    }


def test_unencodable_translation_is_skipped(tmp_path):
    """json.load accepts a lone surrogate escape, which UTF-8 cannot store."""
    po, answers = tmp_path / "pt.po", tmp_path / "pt.json"
    po.write_text(CATALOG, encoding="utf-8")
    answers.write_text(
        '{"{0} folder could not be read.": ["{0} \\ud83d", "{0} b"]}', encoding="utf-8"
    )
    translate_po.apply(str(po), str(answers))
    entry = polib.pofile(str(po))[0]
    assert "fuzzy" in entry.flags
    assert entry.msgstr_plural == {0: "old", 1: "old"}


@pytest.mark.parametrize("surrogate", ["\ud800", "\udbff", "\udc00", "\udfff"])
@pytest.mark.parametrize("plural", [False, True])
def test_invalid_entry_does_not_block_other_translations(tmp_path, capsys, surrogate, plural):
    po, answers = tmp_path / "pt.po", tmp_path / "pt.json"
    po.write_text(CATALOG, encoding="utf-8")
    catalog = polib.pofile(str(po))
    catalog.append(polib.POEntry(msgid="Hello", msgstr="old hello", flags=["fuzzy"]))
    catalog.append(polib.POEntry(msgid="Smile", msgstr="old smile", flags=["fuzzy"]))
    catalog.save(str(po))
    plural_id = catalog[0].msgid
    translations = {
        plural_id: ["{0} pasta", "{0} pastas"],
        "Hello": "Olá",
        "Smile": "Sorriso \U0001f600",
    }
    invalid_id = plural_id if plural else "Hello"
    # An invalid last form must preserve *all* existing plural forms.
    translations[invalid_id] = ["{0} pasta", "{0} " + surrogate] if plural else surrogate
    answers.write_text(json.dumps(translations), encoding="utf-8")

    translate_po.apply(str(po), str(answers))

    entries = {entry.msgid: entry for entry in polib.pofile(str(po))}
    invalid = entries[invalid_id]
    assert "fuzzy" in invalid.flags
    if plural:
        assert invalid.msgstr_plural == {0: "old", 1: "old"}
        assert entries["Hello"].msgstr == "Olá"
        assert "fuzzy" not in entries["Hello"].flags
    else:
        assert invalid.msgstr == "old hello"
        assert entries[plural_id].msgstr_plural == {0: "{0} pasta", 1: "{0} pastas"}
        assert "fuzzy" not in entries[plural_id].flags
    assert entries["Smile"].msgstr == "Sorriso \U0001f600"
    assert "fuzzy" not in entries["Smile"].flags
    assert "2 applied, 1 skipped" in capsys.readouterr().out
