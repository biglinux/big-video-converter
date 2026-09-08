"""Local regression tests; media is generated, never taken from a user's videos."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'big-video-converter/usr/share/big-video-converter'
CLI = ROOT / 'big-video-converter/usr/bin/big-video-converter'
sys.path.insert(0, str(APP))
# Encoders must not create hundreds of threads on a shared CI runner.
if hasattr(os, 'sched_getaffinity'):
    available = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, available[:4])


@contextmanager
def _fail_on_callback_exceptions():
    """Turn Python errors from C-invoked callbacks into test failures.

    PyGObject sends these errors to sys.excepthook rather than propagating
    them through the Python call that drives the GLib main context.
    Always restore the hook, including when a fixture or assertion fails.
    """
    previous = sys.excepthook
    errors = []

    def record(exc_type, value, tb):
        errors.append(''.join(traceback.format_exception(exc_type, value, tb)))

    sys.excepthook = record
    try:
        yield
    finally:
        sys.excepthook = previous
        if errors:
            pytest.fail('Unhandled callback exception(s):\n' + '\n'.join(errors), pytrace=False)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    with _fail_on_callback_exceptions():
        return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    with _fail_on_callback_exceptions():
        return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    with _fail_on_callback_exceptions():
        return (yield)


def media_command(*args):
    return subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *map(str, args)],
                          check=True, capture_output=True, timeout=30)


@pytest.fixture(scope='session')
def media(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and FFprobe are required for media integration tests')
    root = tmp_path_factory.mktemp('synthetic-media')
    video = root / 'source.mp4'
    media_command('-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=25',
                  '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                  '-t', '3', '-c:v', 'libx264', '-threads', '1', '-g', '25',
                  '-c:a', 'aac', '-y', video)
    no_audio = root / 'silent.mkv'
    media_command('-i', video, '-map', '0:v:0', '-c', 'copy', '-y', no_audio)
    srt = root / 'source.srt'
    srt.write_text('1\n00:00:00,200 --> 00:00:01,600\nPrimeira fala\n\n'
                   '2\n00:00:01,500 --> 00:00:02,900\nSegunda fala\n', encoding='utf-8')
    multi = root / 'multi.mkv'
    media_command('-i', video, '-i', srt,
                  '-map', '0:v:0', '-map', '0:a:0', '-map', '0:a:0',
                  '-map', '1:s:0', '-map', '1:s:0', '-c', 'copy',
                  '-metadata:s:a:0', 'language=por', '-metadata:s:a:1', 'language=eng',
                  '-metadata:s:s:0', 'language=por', '-metadata:s:s:1', 'language=por',
                  '-disposition:s:0', 'default', '-disposition:s:1', 'forced', '-y', multi)
    return {'root': root, 'video': video, 'silent': no_audio, 'multi': multi, 'srt': srt}


@pytest.fixture
def cli_env(tmp_path):
    return {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'LC_ALL': 'C.UTF-8',
            'TMPDIR': str(tmp_path), 'gpu': 'software', 'force_software': '1',
            'preset': 'ultrafast', 'video_encoder': 'h264', 'video_quality': 'default',
            'subtitle_extract': 'embedded', 'audio_handling': 'copy',
            'options': '-threads 1 -filter_threads 1',
            'ffmpeg_executable': shutil.which('ffmpeg'),
            'ffprobe_executable': shutil.which('ffprobe')}


@pytest.fixture
def run_cli(cli_env):
    def run(source, destination=None, *, timeout=40, **settings):
        env = {**cli_env, **{k: str(v) for k, v in settings.items()}}
        if destination is not None:
            env['output_file'] = str(destination)
        return subprocess.run(['bash', str(CLI), str(source)], env=env,
                              capture_output=True, text=True, timeout=timeout, cwd=cli_env["HOME"])
    return run
