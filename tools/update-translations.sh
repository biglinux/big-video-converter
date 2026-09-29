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
xgettext --from-code=UTF-8 --language=Python --keyword=_ --keyword=ngettext:1,2 --keyword=_count_text:1,2 --no-location --no-wrap \
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
        sed -i -e 's/nplurals=INTEGER; plural=EXPRESSION;/nplurals=2; plural=(n != 1);/' \
            -e 's/Language: \\n/Language: en\\n/' \
            -e 's/FULL NAME <EMAIL@ADDRESS>/BigLinux/' \
            -e 's/LANGUAGE <LL@li.org>/English/' -e '/^#, fuzzy$/d' "$po"
    else
        msgmerge --update --no-wrap --backup=none --no-fuzzy-matching "$po" "$pot"
    fi
    # msgfmt --check-header warns about placeholder or missing header fields.
    # The revision date is only filled in when absent, so reruns cause no churn.
    sed -i -e '/^"POT-Creation-Date:/d;/^"PO-Revision-Date: YEAR/d' \
        -e 's/FULL NAME <EMAIL@ADDRESS>/BigLinux/;s/LANGUAGE <LL@li.org>/BigLinux/' "$po"
    grep -q '^"PO-Revision-Date:' "$po" ||
        sed -i "0,/^\"Last-Translator:/s//\"PO-Revision-Date: $(date -u +'%Y-%m-%d %H:%M+0000')\\\\n\"\\n&/" "$po"
    printf '%-6s untranslated: %s\n' "$lang" "$(msgattrib --untranslated "$po" | grep -c '^msgid' || true)"
done
