"""Issue #27 and broken GPU drivers, proven with generated media and fake ffmpeg wrappers.

Background: a camera MOV (video, two audio tracks, a tmcd timecode track)
failed with "Missing argument for option 'ac:a:0'" and exit code 127. ffprobe 9
prints the MOV's stream group alongside the streams, so every CSV answer had a
blank row and a duplicated video row; the string-built command of the day split
at the newline and ran the stray channel count as a shell command. The same
release had no answer for a GPU driver that hangs: no fallback stage ever ran.
"""
import os
import shutil
import signal
import stat
import subprocess
import time

import pytest
from conftest import CLI, media_command
from utils.media_validation import probe_media

REAL_FFMPEG = shutil.which('ffmpeg')


def _streams(path, kind=None):
    streams = probe_media(str(path))['streams']
    return [s for s in streams if kind is None or s['codec_type'] == kind]


@pytest.fixture(scope='session')
def timecode_mov(tmp_path_factory):
    """Two mono PCM tracks and a tmcd track: what a camera writes."""
    path = tmp_path_factory.mktemp('mov') / 'camera.mov'
    media_command('-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=25',
                  '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                  '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
                  '-map', '0:v', '-map', '1:a', '-map', '2:a',
                  '-c:v', 'mpeg4', '-c:a', 'pcm_s16le', '-timecode', '01:00:00:00',
                  '-t', '1', '-y', path)
    assert _streams(path, 'data'), 'the sample must carry a timecode track'
    return path


def _fake_ffmpeg(tmp_path, name, body):
    """A wrapper that behaves like ffmpeg except for one scripted defect."""
    wrapper = tmp_path / name
    wrapper.write_text('#!/bin/bash\n' + body + f'\nexec {REAL_FFMPEG} "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


class TestFfprobeStreamGroups:
    def test_probe_helpers_ignore_group_rows(self, timecode_mov):
        """The Bash helpers, run for real against the MOV."""
        text = CLI.read_text()
        start = text.index('probe_rows() {')
        end = text.index('\n}\n', text.index('probe_value() {')) + 2
        script = (f'ffprobe_executable={shutil.which("ffprobe")}\ninput_file={str(timecode_mov)!r}\n'
                  + text[start:end] + '\n'
                  'probe_rows a stream=index:stream_tags=language; echo ---\n'
                  'probe_value a:0 stream=channels; echo ---\n'
                  'probe_value v:0 stream=pix_fmt; echo ---\n'
                  'probe_rows s stream=index,codec_name; echo ---\n')
        out = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=20, check=False)
        rows, channels, pix_fmt, subtitles, _ = out.stdout.split('---\n')
        assert rows.split() == ['1', '2'], out.stdout
        assert channels.strip() == '1'
        assert pix_fmt.strip() == 'yuv420p'
        assert subtitles.strip() == ''

    def test_probe_rows_keeps_real_subtitle_rows(self, media):
        text = CLI.read_text()
        start = text.index('probe_rows() {')
        end = text.index('\n}\n', text.index('probe_value() {')) + 2
        script = (f'ffprobe_executable={shutil.which("ffprobe")}\ninput_file={str(media["multi"])!r}\n'
                  + text[start:end] + '\nprobe_rows s stream=index,codec_name\n')
        out = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=20, check=False)
        assert out.stdout.split() == ['3,subrip', '4,subrip'], out.stdout

    def test_reencode_keeps_channel_count_and_drops_timecode_track(self, timecode_mov, tmp_path, run_cli):
        out = tmp_path / 'camera.mp4'
        result = run_cli(timecode_mov, out, audio_handling='reencode', audio_codec='aac', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Detected 8-bit video (yuv420p)' in result.stdout
        assert '-ac:a:0 1 ' in result.stdout and '-ac:a:1 1 ' in result.stdout
        assert [s['channels'] for s in _streams(out, 'audio')] == [1, 1]
        assert not _streams(out, 'data'), 'MP4 output must not carry the tmcd track'

    def test_timecode_track_can_be_kept(self, timecode_mov, tmp_path, run_cli):
        out = tmp_path / 'camera.mp4'
        result = run_cli(timecode_mov, out, keep_timecode=1, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _streams(out, 'data')

    def test_copy_maps_each_audio_track_once(self, timecode_mov, tmp_path, run_cli):
        out = tmp_path / 'camera.mkv'
        result = run_cli(timecode_mov, out, audio_handling='copy', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        audio = _streams(out, 'audio')
        assert [s['codec_name'] for s in audio] == ['pcm_s16le', 'pcm_s16le']
        assert result.stdout.count('-map 0:1 ') == 1 and result.stdout.count('-map 0:2 ') == 1


class TestStallWatchdog:
    def test_silent_encoder_is_killed_and_the_job_fails(self, media, tmp_path, run_cli):
        """Only the encode (the call carrying -progress) hangs; probes are real."""
        wrapper = _fake_ffmpeg(tmp_path, 'hanging-ffmpeg',
                               'for a in "$@"; do [[ $a == -progress ]] && exec sleep 613; done')
        started = time.monotonic()
        result = run_cli(media['silent'], tmp_path / 'out.mp4', stall_timeout=2,
                         ffmpeg_executable=str(wrapper), timeout=60)
        assert result.returncode == 124, result.stdout + result.stderr
        assert 'Stall watchdog' in result.stderr
        assert time.monotonic() - started < 30
        assert 'Audio stream copy may have failed' not in result.stdout
        assert not (tmp_path / 'out.mp4').exists()
        assert not list(tmp_path.glob('.bvc-*'))
        time.sleep(0.5)
        assert subprocess.run(['pgrep', '-f', 'sleep 613'], capture_output=True, check=False).returncode != 0

    def test_disabled_watchdog_adds_no_progress_option(self, media, tmp_path, run_cli):
        result = run_cli(media['silent'], tmp_path / 'out.mp4', stall_timeout=0, options='-t 0.4 -threads 1')
        assert result.returncode == 0, result.stdout + result.stderr
        assert '-progress' not in result.stdout

    def test_sigterm_ends_the_script_and_its_ffmpeg(self, media, tmp_path, cli_env):
        """The GUI cancels with SIGTERM to the process group; nothing may survive it."""
        wrapper = _fake_ffmpeg(tmp_path, 'hanging-ffmpeg',
                               'for a in "$@"; do [[ $a == -progress ]] && exec sleep 617; done')
        env = {**cli_env, 'ffmpeg_executable': str(wrapper), 'output_file': str(tmp_path / 'out.mp4')}
        process = subprocess.Popen(['bash', str(CLI), str(media['silent'])], env=env, cwd=cli_env['HOME'],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   start_new_session=True)
        for line in process.stdout:
            if line.startswith('Running command:') and '-progress' in line:
                break
        else:
            pytest.fail('the encode never started')
        time.sleep(0.3)
        os.killpg(process.pid, signal.SIGTERM)
        process.stdout.read()
        assert process.wait(timeout=15) == 143
        time.sleep(0.5)
        assert subprocess.run(['pgrep', '-f', 'sleep 617'], capture_output=True, check=False).returncode != 0
        assert not list(tmp_path.glob('.bvc-*'))


class TestGpuSmokeTest:
    @pytest.mark.parametrize('defect', [
        'for a in "$@"; do [[ $a == -init_hw_device ]] && { echo "Device creation failed: -22." >&2; exit 1; }; done',
        'for a in "$@"; do [[ $a == -init_hw_device ]] && exec sleep 619; done',
    ], ids=['driver-fails', 'driver-hangs'])
    def test_broken_driver_means_software_encoding(self, media, tmp_path, cli_env, defect):
        wrapper = _fake_ffmpeg(tmp_path, 'broken-gpu-ffmpeg', defect)
        env = {**cli_env, 'gpu': 'amd', 'force_software': '', 'gpu_probe_timeout': '2',
               'ffmpeg_executable': str(wrapper), 'output_file': str(tmp_path / 'out.mp4'),
               'options': '-t 0.4 -threads 1'}
        result = subprocess.run(['bash', str(CLI), str(media['silent'])], env=env, cwd=cli_env['HOME'],
                                capture_output=True, text=True, timeout=90, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Checking GPU encoder h264_vaapi' in result.stdout
        assert 'GPU encoder check failed' in result.stdout
        assert 'Encode mode: Decode GPU' not in result.stdout
        assert 'Encode mode: Decode Software, Encode Software' in result.stdout
        assert _streams(tmp_path / 'out.mp4', 'video')[0]['codec_name'] == 'h264'

    def test_check_can_be_skipped_and_the_stage_chain_still_recovers(self, media, tmp_path, cli_env):
        wrapper = _fake_ffmpeg(tmp_path, 'broken-gpu-ffmpeg',
                               'for a in "$@"; do [[ $a == -init_hw_device ]] && exit 1; done')
        env = {**cli_env, 'gpu': 'amd', 'force_software': '', 'gpu_smoke_test': '0',
               'ffmpeg_executable': str(wrapper), 'output_file': str(tmp_path / 'out.mp4'),
               'options': '-t 0.4 -threads 1'}
        result = subprocess.run(['bash', str(CLI), str(media['silent'])], env=env, cwd=cli_env['HOME'],
                                capture_output=True, text=True, timeout=90, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Checking GPU encoder' not in result.stdout
        assert 'Encode mode: Decode GPU, encode GPU' in result.stdout
        assert 'Encode mode: Decode Software, Encode Software' in result.stdout

    def test_vulkan_scales_in_software_before_the_upload(self, media, tmp_path, cli_env):
        """scale_vulkan garbles 1280x720 and 1920x1080; it must never be used."""
        wrapper = _fake_ffmpeg(tmp_path, 'no-gpu-ffmpeg',
                               'for a in "$@"; do [[ $a == -init_hw_device ]] && exit 1; done')
        env = {**cli_env, 'gpu': 'vulkan', 'force_software': '', 'gpu_smoke_test': '0',
               'video_resolution': '64x36', 'ffmpeg_executable': str(wrapper),
               'output_file': str(tmp_path / 'out.mp4'), 'options': '-t 0.4 -threads 1'}
        result = subprocess.run(['bash', str(CLI), str(media['silent'])], env=env, cwd=cli_env['HOME'],
                                capture_output=True, text=True, timeout=90, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Encode mode: Decode GPU' not in result.stdout
        assert 'scale_vulkan' not in result.stdout
        vulkan_run = next(line for line in result.stdout.splitlines()
                          if line.startswith('Running command:') and 'h264_vulkan' in line)
        assert vulkan_run.index('scale=') < vulkan_run.index('hwupload=derive_device=vulkan')
        assert _streams(tmp_path / 'out.mp4', 'video')[0]['width'] == 64

    def test_exit_255_is_an_error_and_still_falls_back(self, media, tmp_path, cli_env):
        """ffmpeg returns 255 for AVERROR(EPERM); it used to be read as a cancel."""
        wrapper = _fake_ffmpeg(tmp_path, 'eperm-ffmpeg',
                               'for a in "$@"; do [[ $a == -init_hw_device ]] && exit 255; done')
        env = {**cli_env, 'gpu': 'amd', 'force_software': '', 'gpu_smoke_test': '0',
               'ffmpeg_executable': str(wrapper), 'output_file': str(tmp_path / 'out.mp4'),
               'options': '-t 0.4 -threads 1'}
        result = subprocess.run(['bash', str(CLI), str(media['silent'])], env=env, cwd=cli_env['HOME'],
                                capture_output=True, text=True, timeout=90, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'interrupted by user' not in result.stdout
        assert (tmp_path / 'out.mp4').exists()


class TestFileInfoFailures:
    def test_a_failed_probe_is_asked_again(self, media, monkeypatch):
        from utils import file_info

        file_info.clear_probe_cache()
        real_run = subprocess.run
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(command, 10)
            return real_run(command, **kwargs)

        monkeypatch.setattr(file_info.subprocess, 'run', run)
        assert file_info.get_video_dimensions(str(media['video'])) == (None, None)
        answer = file_info.get_video_dimensions(str(media['video']))
        assert answer[0] and answer[1]
        assert file_info.get_video_dimensions(str(media['video'])) == answer
        assert len(calls) == 2
        file_info.clear_probe_cache()

    def test_latin1_metadata_is_read(self, tmp_path, monkeypatch):
        """Tags are copied into ffmpeg's and ffprobe's output as written."""
        from utils import file_info

        (tmp_path / 'stderr').write_bytes(
            b"Input #0, matroska,webm, from 'x.mkv':\n  Metadata:\n    title : Caf\xe9\n"
            b'  Duration: 00:00:01.00, start: 0.000000, bitrate: 1 kb/s\n'
            b'  Stream #0:0: Video: h264, yuv420p, 64x48, 25 fps\n')
        (tmp_path / 'stdout').write_bytes(
            b'{"streams": [{"codec_type": "video", "tags": {"title": "Caf\xe9"}}], "format": {}}')
        tool = tmp_path / 'tool'
        tool.write_text(f'#!/bin/bash\ncat {tmp_path}/stderr >&2\n')
        tool.chmod(0o755)
        monkeypatch.setattr(file_info, 'get_ffmpeg_executable', lambda: str(tool))
        monkeypatch.setattr(file_info, 'get_ffprobe_executable', lambda: str(tool))
        video = tmp_path / 'x.mkv'
        video.write_bytes(b'')

        info = file_info._probe_with_ffmpeg(str(video))
        assert info['streams'][0]['codec_type'] == 'video'
        tool.write_text(f'#!/bin/bash\ncat {tmp_path}/stdout\n')
        info = file_info.get_video_file_info(str(video))
        assert info['streams'][0]['tags']['title'] == 'Caf\ufffd'

    def test_info_dialog_reports_any_failure_and_opens_escaped_folders(self, monkeypatch):
        from utils import file_info

        shown = []
        dialog = file_info.VideoInfoDialog.__new__(file_info.VideoInfoDialog)
        dialog.file_path = '/videos/x.mp4'
        dialog.dialog = None
        dialog._show_error = shown.append

        def fail(_path):
            raise UnicodeDecodeError('utf-8', b'\xe9', 0, 1, 'invalid')

        monkeypatch.setattr(file_info, 'get_video_file_info', fail)
        monkeypatch.setattr(file_info.GLib, 'idle_add', lambda f, *a: f(*a))
        dialog._get_file_info_thread()
        assert shown and 'invalid' in shown[0]

        opened = []
        monkeypatch.setattr(file_info.Gtk, 'show_uri', lambda _parent, uri, _time: opened.append(uri))
        dialog._open_containing_folder('/videos/a#b%c?d')
        assert opened == ['file:///videos/a%23b%25c%3Fd']
