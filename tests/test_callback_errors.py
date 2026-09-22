"""Verify that native callback errors cannot produce a green pytest result."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET

import pytest


@pytest.mark.parametrize('phase', ['setup', 'call', 'teardown'])
def test_callback_exception_fails_its_pytest_phase(tmp_path, phase):
    # Exercise the real project hooks in a separate pytest process. Its
    # intentional failure must be captured here, not escape to the main suite.
    shutil.copyfile(Path(__file__).with_name('conftest.py'), tmp_path / 'conftest.py')
    (tmp_path / 'test_probe.py').write_text(textwrap.dedent('''
        import os
        import time
        import pytest
        from gi.repository import GLib

        def dispatch_failure():
            called = []
            def callback():
                called.append(True)
                raise RuntimeError('NATIVE_CALLBACK_FAILURE_SENTINEL')
            context = GLib.MainContext.new()
            source = GLib.idle_source_new()
            source.set_callback(lambda *args: callback())
            source.attach(context)
            try:
                deadline = time.monotonic() + 2
                while not called and time.monotonic() < deadline:
                    context.iteration(False)
                    time.sleep(0.001)
                assert called, 'Native callback never executed'
            finally:
                source.destroy()

        @pytest.fixture
        def resource():
            if os.environ['BVC_PROBE_PHASE'] == 'setup':
                dispatch_failure()
            yield
            if os.environ['BVC_PROBE_PHASE'] == 'teardown':
                dispatch_failure()

        def test_probe(resource):
            if os.environ['BVC_PROBE_PHASE'] == 'call':
                dispatch_failure()
    '''), encoding='utf-8')
    env = os.environ.copy()
    # The child should load the copied hooks and pytest's built-ins only.
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', BVC_PROBE_PHASE=phase)
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', 'test_probe.py', '--junitxml=probe.xml'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20, check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert 'Unhandled callback exception(s)' in output
    assert 'NATIVE_CALLBACK_FAILURE_SENTINEL' in output
    suites = ET.parse(tmp_path / 'probe.xml').getroot().iter('testsuite')
    counts = {key: 0 for key in ('tests', 'failures', 'errors', 'skipped')}
    for suite in suites:
        for key in counts:
            counts[key] += int(suite.get(key, 0))
    assert counts['tests'] >= 1 and counts['skipped'] == 0
    assert counts['failures'] == (1 if phase == 'call' else 0)
    assert counts['errors'] == (0 if phase == 'call' else 1)
