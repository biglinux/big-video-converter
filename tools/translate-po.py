#!/usr/bin/env python3
"""Move untranslated .po entries to JSON and back, so a translator can work on them.

    translate-po.py export LANG.po OUT.json   # {"msgid": "", ...} for empty/fuzzy entries
    translate-po.py apply  LANG.po IN.json    # fill msgstr from the JSON, drop fuzzy flags

The JSON keeps the msgid verbatim as the key, so a translation is applied only
to the exact string it was written for. Placeholders such as {0}, {free:.1f}
or %s must survive unchanged; ``apply`` refuses an entry whose placeholders
differ from the original and reports it.
"""
import json
import re
import sys

import polib

_PLACEHOLDER = re.compile(r"\{[^{}]*\}|%\([^)]*\)[sd]|%[sd]|\\n")


def placeholders(text: str) -> list:
    return sorted(_PLACEHOLDER.findall(text))


def export(po_path: str, out_path: str) -> None:
    po = polib.pofile(po_path, wrapwidth=0)
    entries = {e.msgid: "" for e in po if not e.obsolete and (not e.translated() or "fuzzy" in e.flags) and e.msgid}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, ensure_ascii=False, indent=1)
    print(f"{po_path}: {len(entries)} entries exported")


def apply(po_path: str, in_path: str) -> None:
    po = polib.pofile(po_path, wrapwidth=0)
    with open(in_path, encoding="utf-8") as handle:
        translations = json.load(handle)
    applied = skipped = 0
    for entry in po:
        if entry.obsolete or entry.msgid not in translations:
            continue
        text = translations[entry.msgid]
        if not isinstance(text, str) or not text.strip():
            skipped += 1
            continue
        if placeholders(text) != placeholders(entry.msgid):
            print(f"  placeholder mismatch, kept untranslated: {entry.msgid!r} -> {text!r}")
            skipped += 1
            continue
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
