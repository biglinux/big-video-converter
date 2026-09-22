"""Remove the destructive UI-side reset of saved cleaning preferences."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIDEBAR = (
    ROOT
    / "big-video-converter/usr/share/big-video-converter/sidebar_builder.py"
)

source = SIDEBAR.read_text(encoding="utf-8")
lines = source.splitlines(keepends=True)
out: list[str] = []
removed = False
index = 0
while index < len(lines):
    current = lines[index]
    if (
        current.lstrip().startswith("if not audio_will_reencode:")
        and index + 1 < len(lines)
        and "noise_reduction_switch.set_active(False)" in lines[index + 1]
    ):
        indent = current[: len(current) - len(current.lstrip())]
        out.append(
            indent
            + "# Availability changes without erasing saved cleaning preferences.\n"
        )
        removed = True
        index += 2
        continue
    out.append(current)
    index += 1

updated = "".join(out)
if "noise_reduction_switch.set_active(False)" in updated:
    raise SystemExit("destructive preference reset still exists")
if removed:
    SIDEBAR.write_text(updated, encoding="utf-8")
elif "_audio_cleaning_row.set_sensitive(audio_will_reencode)" not in updated:
    raise SystemExit("audio applicability handler was not found")
