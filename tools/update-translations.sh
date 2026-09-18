#!/bin/bash
# Regenerate the gettext template and merge it into every language file.
#
# Mirrors what the retired CI translator did, minus the machine translation:
# new or changed strings are left empty in each .po and translated by hand or
# with an assistant (see tools/translate-po.py), then compiled with
# tools/compile-translations.sh. Run from anywhere.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
app="$root/big-video-converter"
locale="$app/locale"
pot="$locale/big-video-converter.pot"

mapfile -t sources < <(find "$app/usr/share" -name '*.py' | sort)
xgettext --from-code=UTF-8 --language=Python --keyword=_ --no-location --no-wrap \
    --package-name=big-video-converter --msgid-bugs-address='' \
    --copyright-holder='BigLinux' -o "$pot" "${sources[@]}"
# Bash gettext strings ($"..."), if the script ever carries any.
bash --dump-po-strings "$app/usr/bin/big-video-converter" > "$locale/.bash.pot" || true
if grep -q '^msgid ".' "$locale/.bash.pot"; then
    msgcat --no-wrap --use-first -o "$pot.merged" "$pot" "$locale/.bash.pot"
    mv "$pot.merged" "$pot"
fi
rm -f "$locale/.bash.pot"
sed -i '/^"POT-Creation-Date:/d' "$pot"

for po in "$locale"/*.po; do
    lang=$(basename "$po" .po)
    if [[ $lang == en ]]; then
        msgen --no-wrap -o "$po" "$pot"
    else
        msgmerge --update --no-wrap --backup=none --no-fuzzy-matching "$po" "$pot"
    fi
    sed -i '/^"POT-Creation-Date:/d;/^"PO-Revision-Date:/d' "$po"
    printf '%-6s untranslated: %s\n' "$lang" "$(msgattrib --untranslated "$po" | grep -c '^msgid' || true)"
done
