#!/usr/bin/env python3
"""Make the staged patch serialization-safe, then apply it."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "tools/apply_progress_outcomes_r3_v3.py"
V4 = ROOT / "tools/apply_progress_outcomes_r3_v4.py"

text = V3.read_text(encoding="utf-8")
nl = chr(10)
escaped_nl = chr(92) + "n"
plain = "    def _cancel_completion_summary(self):" + nl
typed = "    def _cancel_completion_summary(self) -> None:" + nl
count = text.count(plain)
if count != 2:
    raise RuntimeError(f"v3 generator: expected two completion signatures, found {count}")
text = text.replace(plain, typed)

start_marker = nl.join(
    ["replace_once(", "    PROGRESS,", "    '''    def reset(self):"]
)
end_marker = nl.join(["", ")", "", "replace_once(", "    TESTS,"])
start = text.find(start_marker)
if start < 0:
    raise RuntimeError("v3 generator: reset replacement start marker not found")
end = text.find(end_marker, start)
if end < 0:
    raise RuntimeError("v3 generator: reset replacement end marker not found")
end += len(nl + ")")
new_reset_block = nl.join(
    [
        "replace_once(",
        "    PROGRESS,",
        f'    "    def reset(self) -> None:{escaped_nl}"',
        f'    "        self._cancel_completion_summary(){escaped_nl}",',
        f'    "    def reset(self) -> None:{escaped_nl}"',
        f'    "        self._cancel_completion_summary(){escaped_nl}"',
        f'    "        self._cancel_overall_progress_update(){escaped_nl}",',
        ")",
    ]
)
text = text[:start] + new_reset_block + text[end:]
V3.write_text(text, encoding="utf-8")

subprocess.run([sys.executable, str(V4)], cwd=ROOT, check=True)
print("Applied serialization-safe typed progress patch")
