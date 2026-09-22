#!/usr/bin/env python3
"""Make the staged progress patch target the constructor unambiguously."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "tools/apply_progress_outcomes_r3_v3.py"

text = V3.read_text(encoding="utf-8")
old = '''replace_once(
    PROGRESS,
    "        self._completion_source_id = None\\n",
    "        self._completion_source_id = None\\n"
    "        self._aggregate_source_id = None\\n",
)
'''
new = '''replace_once(
    PROGRESS,
    "        self.completed_count = 0\\n"
    "        self._completion_source_id = None\\n\\n"
    "    def _setup_css",
    "        self.completed_count = 0\\n"
    "        self._completion_source_id = None\\n"
    "        self._aggregate_source_id = None\\n\\n"
    "    def _setup_css",
)
'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"v3 generator: expected one timer insertion block, found {count}")
V3.write_text(text.replace(old, new, 1), encoding="utf-8")
subprocess.run([sys.executable, str(V3)], cwd=ROOT, check=True)
print("Applied structurally targeted progress patch")
