#!/bin/bash
# Compile every locale/*.po into usr/share/locale/<lang>/LC_MESSAGES/*.mo.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
app="$root/big-video-converter"
for po in "$app"/locale/*.po; do
    lang=$(basename "$po" .po)
    target="$app/usr/share/locale/$lang/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check-format -o "$target/big-video-converter.mo" "$po"
done
echo "compiled $(ls "$app"/locale/*.po | wc -l) catalogues"
