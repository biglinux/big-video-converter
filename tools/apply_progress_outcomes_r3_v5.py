#!/usr/bin/env python3
"""Align staged replacements with the typed production signatures."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "tools/apply_progress_outcomes_r3_v3.py"
V4 = ROOT / "tools/apply_progress_outcomes_r3_v4.py"

text = V3.read_text(encoding="utf-8")
plain = "    def _cancel_completion_summary(self):\\n"
typed = "    def _cancel_completion_summary(self) -> None:\\n"
count = text.count(plain)
if count != 2:
    raise RuntimeError(f"v3 generator: expected two completion signatures, found {count}")
text = text.replace(plain, typed)

old_reset = '''    ''' + "'''" + '''    def reset(self):
        \"\"\"Reset progress page for new batch\"\"\"
        self._cancel_completion_summary()
''' + "'''" + ''',
    ''' + "'''" + '''    def reset(self):
        \"\"\"Reset progress page for new batch\"\"\"
        self._cancel_completion_summary()
        self._cancel_overall_progress_update()
''' + "'''" + ''',
'''
new_reset = '''    ''' + "'''" + '''    def reset(self) -> None:
        self._cancel_completion_summary()
''' + "'''" + ''',
    ''' + "'''" + '''    def reset(self) -> None:
        self._cancel_completion_summary()
        self._cancel_overall_progress_update()
''' + "'''" + ''',
'''
reset_count = text.count(old_reset)
if reset_count != 1:
    raise RuntimeError(f"v3 generator: expected one reset block, found {reset_count}")
V3.write_text(text.replace(old_reset, new_reset, 1), encoding="utf-8")

subprocess.run([sys.executable, str(V4)], cwd=ROOT, check=True)
print("Applied typed, structurally targeted progress patch")
