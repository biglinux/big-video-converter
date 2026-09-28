"""Local regression tests; media is generated, never taken from a user's videos."""
import atexit
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'big-video-converter/usr/share/big-video-converter'
CLI = ROOT / 'big-video-converter/usr/bin/big-video-converter'
sys.path.insert(0, str(APP))


def _private_session():
    """Give the tests a display and a session bus of their own.

    Windows must never reach the desktop of the person running the tests, and
    xvfb-run alone does not prevent it: GTK prefers Wayland, and without
    WAYLAND_DISPLAY libwayland connects to $XDG_RUNTIME_DIR/wayland-0, the live
    desktop. A GApplication on the live bus would also hand its activation to
    a converter the person has open. Sessions that already run in a private
    runtime directory (a nested KWin harness) are kept as they are.
    """
    runtime = os.environ.get('XDG_RUNTIME_DIR', '')
    if os.environ.get('BVC_TESTS_KEEP_SESSION') or (runtime and runtime != f'/run/user/{os.getuid()}'):
        return
    for name in ('WAYLAND_DISPLAY', 'DISPLAY', 'AT_SPI_BUS_ADDRESS'):
        os.environ.pop(name, None)
    os.environ['GDK_BACKEND'] = 'x11'  # never the Wayland fallback socket
    started = []
    atexit.register(lambda: [os.killpg(p.pid, signal.SIGTERM) for p in started if p.poll() is None])

    if shutil.which('Xvfb'):
        read_end, write_end = os.pipe()
        started.append(subprocess.Popen(
            ['Xvfb', '-displayfd', str(write_end), '-screen', '0', '1440x1000x24', '-nolisten', 'tcp'],
            pass_fds=[write_end], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True))
        os.close(write_end)
        with os.fdopen(read_end) as reader:
            number = reader.readline().strip()
        if number:
            os.environ['DISPLAY'] = f':{number}'

    if not shutil.which('dbus-daemon'):
        return
    # Only AT-SPI may be activated: desktop portals and ksecretd started on a
    # throwaway bus outlive it. Activated services share the daemon's process
    # group, which is terminated at exit with it.
    bus_dir = Path(tempfile.mkdtemp(prefix='bvc-test-bus-'))
    atexit.register(shutil.rmtree, bus_dir, True)
    (bus_dir / 'services').mkdir()
    for service in Path('/usr/share/dbus-1/services').glob('org.a11y.*.service'):
        (bus_dir / 'services' / service.name).symlink_to(service)
    (bus_dir / 'session.conf').write_text(
        '<busconfig><type>session</type>'
        f'<listen>unix:dir={bus_dir}</listen><servicedir>{bus_dir}/services</servicedir>'
        '<policy context="default"><allow send_destination="*" eavesdrop="true"/>'
        '<allow eavesdrop="true"/><allow own="*"/></policy></busconfig>', encoding='utf-8')
    bus = subprocess.Popen(['dbus-daemon', '--nofork', f'--config-file={bus_dir}/session.conf',
                            '--print-address=1'], stdout=subprocess.PIPE, text=True,
                           start_new_session=True)
    started.append(bus)
    os.environ['DBUS_SESSION_BUS_ADDRESS'] = bus.stdout.readline().strip()


_private_session()
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
                              capture_output=True, text=True, timeout=timeout, cwd=cli_env["HOME"], check=False)
    return run
