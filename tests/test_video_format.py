"""Files that televisions open: HEVC for UHD, real SDR from HDR, honest H.264 levels.

Background: a 4K HDR source converted with the defaults produced an H.264 4K
file, 8-bit, still tagged bt2020/PQ, with level 4.1 in the header. Samsung
hardware players refused it (PLAYER_ERROR_CONNECTION_FAILED) while a PC played
it; the picture was also washed out because the bits were truncated rather than
tone mapped. Every case here is generated with FFmpeg, never taken from a
user's library.
"""
import json
import shutil
import subprocess

import pytest

from conftest import CLI, media_command
from utils.media_validation import probe_media


def _video(path):
    return [s for s in probe_media(str(path))['streams'] if s['codec_type'] == 'video'][0]


def _has_encoder(name):
    out = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
    return f' {name} ' in out


def _has_filter(name):
    out = subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True, text=True).stdout
    return f' {name} ' in out


@pytest.fixture(scope='session')
def hdr_source(tmp_path_factory):
    """A 10-bit HEVC clip tagged as HDR10 (bt2020 primaries, PQ transfer)."""
    if not _has_encoder('libx265'):
        pytest.skip('libx265 is required to build an HDR sample')
    path = tmp_path_factory.mktemp('hdr') / 'hdr.mp4'
    # The VUI colour tags must go through x265 itself; ffmpeg's -color_* output
    # options are not forwarded to this encoder.
    media_command('-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=25', '-t', '1',
                  '-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le',
                  '-x265-params', 'log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited',
                  '-an', '-y', path)
    assert _video(path)['color_transfer'] == 'smpte2084'
    return path


@pytest.fixture(scope='session')
def uhd_source(tmp_path_factory):
    """A short 3840x2160 H.264 clip: the shape of a "4K" file someone wants to keep in 4K."""
    path = tmp_path_factory.mktemp('uhd') / 'uhd.mp4'
    media_command('-f', 'lavfi', '-i', 'testsrc2=size=3840x2160:rate=25', '-t', '0.4',
                  '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2', '-an', '-y', path)
    return path


class TestHdrToSdr:
    def test_h264_output_is_real_sdr(self, hdr_source, tmp_path, run_cli):
        """8-bit target: tone mapped and tagged bt709, not truncated PQ."""
        if not (_has_filter('zscale') and _has_filter('tonemap')):
            pytest.skip('zscale/tonemap filters are required')
        out = tmp_path / 'sdr.mp4'
        result = run_cli(hdr_source, out, video_encoder='h264', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'tone mapping' in result.stdout
        v = _video(out)
        assert v['codec_name'] == 'h264'
        assert v['pix_fmt'] == 'yuv420p'
        assert v.get('color_transfer') == 'bt709'
        assert v.get('color_primaries') == 'bt709'

    def test_h265_output_keeps_hdr_and_10_bits(self, hdr_source, tmp_path, run_cli):
        """10-bit target: HDR is legitimate there, so nothing is mapped away."""
        if not _has_encoder('libx265'):
            pytest.skip('libx265 required')
        out = tmp_path / 'hdr-out.mp4'
        result = run_cli(hdr_source, out, video_encoder='h265', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'tone mapping' not in result.stdout
        v = _video(out)
        assert v['codec_name'] == 'hevc'
        assert v['pix_fmt'] == 'yuv420p10le'
        assert v.get('color_transfer') == 'smpte2084'


class TestUhdCodec:
    def test_default_h264_becomes_h265_above_1080p(self, uhd_source, tmp_path, run_cli):
        if not _has_encoder('libx265'):
            pytest.skip('libx265 required')
        out = tmp_path / 'uhd-out.mp4'
        result = run_cli(uhd_source, out, video_encoder='h264', options='-t 0.2 -threads 2', timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'switching from H.264 to H.265' in result.stdout
        v = _video(out)
        assert v['codec_name'] == 'hevc'
        assert v['width'] == 3840

    def test_strict_keeps_h264_with_an_honest_level(self, uhd_source, tmp_path, run_cli):
        out = tmp_path / 'uhd-h264.mp4'
        result = run_cli(uhd_source, out, video_encoder='h264', video_encoder_strict=1,
                         options='-t 0.2 -threads 2', timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'switching from H.264' not in result.stdout
        v = _video(out)
        assert v['codec_name'] == 'h264'
        assert int(v['level']) >= 51  # 3840x2160 needs at least level 5.1

    def test_downscaled_uhd_stays_h264(self, uhd_source, tmp_path, run_cli):
        """The decision looks at the output size, not the source."""
        out = tmp_path / 'uhd-to-1080.mp4'
        result = run_cli(uhd_source, out, video_encoder='h264', video_resolution='1920x1080',
                         options='-t 0.2 -threads 2', timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'switching from H.264' not in result.stdout
        assert _video(out)['codec_name'] == 'h264'


class TestHevcTag:
    def test_hevc_in_mp4_is_tagged_hvc1(self, media, tmp_path, run_cli):
        if not _has_encoder('libx265'):
            pytest.skip('libx265 required')
        out = tmp_path / 'tagged.mp4'
        result = run_cli(media['silent'], out, video_encoder='h265', options='-t 0.4 -threads 1', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert _video(out)['codec_tag_string'] == 'hvc1'


class TestH264Level:
    """The level table, run through the real Bash function."""

    @staticmethod
    def _level(w, h, num, den=1):
        text = CLI.read_text()
        start = text.index('h264_level_for() {')
        end = text.index('\n}\n', start) + 2
        script = text[start:end] + f'\nh264_level_for "{w}" "{h}" "{num}" "{den}"\n'
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10).stdout.strip()

    @pytest.mark.parametrize('w,h,num,den,expected', [
        (1920, 1080, 24000, 1001, '4.1'),   # what used to be hardcoded, still right here
        (1920, 1080, 60, 1, '4.2'),
        (3840, 1606, 24000, 1001, '5.1'),   # the real file that shipped with 4.1
        (3840, 2160, 60, 1, '5.2'),
        (7680, 4320, 30, 1, '6.0'),
        ('', '', 30, 1, '4.1'),             # unknown geometry: the old default
    ])
    def test_table(self, w, h, num, den, expected):
        assert self._level(w, h, num, den) == expected
