#!/bin/sh
set -eu
screen=${1:?screen}
out=${2:?output.png}
theme=${3:-light}
width=${4:-1280}
height=${5:-760}
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ready=$(mktemp)
rm -f "$ready"
media=${BVC_DEMO_MEDIA_DIR:-"$root/.demo-media"}
export XDG_CONFIG_HOME=$(mktemp -d)
export XDG_CACHE_HOME=$(mktemp -d)
cleanup() {
    test -z "${pid:-}" || kill "$pid" 2>/dev/null || true
    rm -rf "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$ready"
}
trap cleanup EXIT HUP INT TERM
python3 "$root/tools/ui_demo.py" \
    --screen "$screen" --theme "$theme" \
    --width "$width" --height "$height" \
    --media-dir "$media" --ready "$ready" --auto-quit 30 &
pid=$!
for _ in $(seq 1 160); do
    test -f "$ready" && break
    kill -0 "$pid" 2>/dev/null || { wait "$pid"; exit 1; }
    sleep 0.1
done
test -f "$ready" || { echo "demo did not become ready" >&2; exit 1; }
sleep 0.6
mkdir -p "$(dirname -- "$out")"
if command -v gnome-screenshot >/dev/null 2>&1; then
    printf '%s\n' 'capture-backend=gnome-screenshot' >&2
    gnome-screenshot -f "$out"
elif command -v import >/dev/null 2>&1; then
    printf '%s\n' 'capture-backend=imagemagick-import' >&2
    import -window root "$out"
else
    printf '%s\n' 'No supported screenshot backend found' >&2
    exit 1
fi
test -s "$out"
