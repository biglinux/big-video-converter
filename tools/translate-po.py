#!/usr/bin/env python3
"""Move untranslated .po entries to JSON and back, so a translator can work on them.

    translate-po.py export LANG.po OUT.json   # {"msgid": "", ...} for empty/fuzzy entries
    translate-po.py apply  LANG.po IN.json    # fill msgstr from the JSON, drop fuzzy flags

The JSON keeps the msgid verbatim as the key, so a translation is applied only
to the exact string it was written for. A plural entry takes a list with one
string per plural form of the language. Placeholders such as {0}, {free:.1f}
or %s must survive unchanged; ``apply`` refuses an entry whose placeholders
differ from the original and reports it.
"""

import json
import re
import sys

import polib

_PLACEHOLDER = re.compile(r"\{[^{}]*\}|%\([^)]*\)[sd]|%[sd]|\\n")
# json.load accepts a lone surrogate escape, which a UTF-8 catalog cannot store.
_SURROGATE = re.compile("[\ud800-\udfff]")


def placeholders(text: str) -> list:
    return sorted(_PLACEHOLDER.findall(text))


def plural_count(po) -> int:
    match = re.search(r"nplurals\s*=\s*(\d+)", po.metadata.get("Plural-Forms", ""))
    return int(match.group(1)) if match else 2


def export(po_path: str, out_path: str) -> None:
    po = polib.pofile(po_path, wrapwidth=0)
    forms = plural_count(po)
    entries = {
        e.msgid: [""] * forms if e.msgid_plural else ""
        for e in po
        if not e.obsolete and (not e.translated() or "fuzzy" in e.flags) and e.msgid
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, ensure_ascii=False, indent=1)
    print(f"{po_path}: {len(entries)} entries exported")


def apply(po_path: str, in_path: str) -> None:
    po = polib.pofile(po_path, wrapwidth=0)
    with open(in_path, encoding="utf-8") as handle:
        translations = json.load(handle)
    forms = plural_count(po)
    applied = skipped = 0
    for entry in po:
        if entry.obsolete or entry.msgid not in translations:
            continue
        text = translations[entry.msgid]
        texts = text if entry.msgid_plural else [text]
        if (
            not isinstance(texts, list)
            or len(texts) != (forms if entry.msgid_plural else 1)
            or not all(
                isinstance(t, str) and t.strip() and not _SURROGATE.search(t)
                for t in texts
            )
        ):
            skipped += 1
            continue
        original = placeholders(entry.msgid_plural or entry.msgid)
        if any(placeholders(t) != original for t in texts):
            print(
                f"  placeholder mismatch, kept untranslated: {entry.msgid!r} -> {text!r}"
            )
            skipped += 1
            continue
        if entry.msgid_plural:
            entry.msgstr_plural = dict(enumerate(texts))
        else:
            entry.msgstr = text
        if "fuzzy" in entry.flags:
            entry.flags.remove("fuzzy")
        applied += 1
    po.save(po_path)
    print(f"{po_path}: {applied} applied, {skipped} skipped")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("export", "apply"):
        sys.exit(__doc__)
    (export if sys.argv[1] == "export" else apply)(sys.argv[2], sys.argv[3])
