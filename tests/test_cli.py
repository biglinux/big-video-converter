import re
import shutil
import subprocess
from array import array
from pathlib import Path

import pytest
from conftest import CLI, media_command
from constants import NOISE_MODELS, noise_reduction_filter
from utils.media_validation import media_duration, probe_media, stream_count


@pytest.mark.parametrize('name', [
    'space and apostrophe d\'água [x].mkv', 'double " quote.mkv',
    'literal$(touch marker).mkv', 'literal`touch marker`.mkv',
    'line\nbreak.mkv', '-leading-colon:name.mkv',
])
def test_filenames_are_data(media, tmp_path, run_cli, name):
    source=tmp_path/name;shutil.copyfile(media['silent'], source)
    out=tmp_path/('output '+name)
    result=run_cli(source,out,options='-t 0.4 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.exists() and not (tmp_path/'marker').exists()
    assert source.exists()
    assert len(probe_media(str(out))['streams']) == 1


def test_default_output_in_dotted_directory(media,tmp_path,run_cli):
    folder=tmp_path/'a.b.c';folder.mkdir()
    source=folder/'movie.mkv';shutil.copyfile(media['silent'],source)
    result=run_cli(source, options='-t 0.4 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert (folder/'movie.mp4').exists()
    assert not (tmp_path/'a.b.mp4').exists()


@pytest.mark.parametrize('mode', ['same','symlink'])
def test_output_is_never_the_input(media,tmp_path,run_cli,mode):
    """Encoding over the source would destroy it while still reading it."""
    source=tmp_path/'original.mp4';shutil.copyfile(media['video'],source)
    before=source.read_bytes()
    dest=source if mode=='same' else tmp_path/'link.mp4'
    if mode=='symlink':dest.symlink_to(source)
    result=run_cli(source,dest)
    assert result.returncode != 0
    assert source.read_bytes() == before


def test_existing_destination_is_never_replaced(media,tmp_path,run_cli):
    """A taken name gets a counter, and the script names the file it wrote:
    the GUI validates, reports and deletes the original by that line."""
    source=tmp_path/'original.mp4';shutil.copyfile(media['video'],source)
    dest=tmp_path/'out.mkv';dest.write_bytes(b'KEEP')
    result=run_cli(source,dest,options='-t 0.4 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert dest.read_bytes() == b'KEEP'
    assert f'Output file: {tmp_path}/out_1.mkv' in result.stdout.splitlines()
    assert stream_count(probe_media(str(tmp_path/'out_1.mkv')),'video') == 1
    assert source.exists()
    broken=tmp_path/'kept.mkv';broken.write_bytes(b'KEEP')
    result=run_cli(source,broken,options='-c:v no_such_encoder')
    assert result.returncode != 0
    assert broken.read_bytes() == b'KEEP' and not (tmp_path/'kept_1.mkv').exists()


@pytest.mark.parametrize('copy_mode', ['0','1'])
def test_trim_is_requested_before_the_input(media,tmp_path,run_cli,copy_mode):
    """Trimming after -i makes FFmpeg decode and discard the whole offset, and
    in copy mode drops the opening the user asked to keep."""
    out=tmp_path/f'trimmed{copy_mode}.mkv'
    result=run_cli(media['video'],out,options='-ss 1 -t 1 -threads 1',
                   force_copy_video=copy_mode)
    assert result.returncode == 0, result.stdout + result.stderr
    commands=[line for line in result.stdout.splitlines() if line.startswith('Running command:')]
    assert commands, result.stdout
    for command in commands:
        assert command.index(' -ss ') < command.index(' -i '), command
    assert media_duration(probe_media(str(out))) >= 1.0


def test_successful_run_leaves_no_workspace_behind(media,tmp_path,run_cli):
    out=tmp_path/'clean.mkv'
    result=run_cli(media['video'],out,options='-t 0.4 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.stat().st_size > 0
    assert [path.name for path in tmp_path.iterdir() if path.name.startswith('.bvc')] == []


@pytest.mark.parametrize('extension', ['mp4','mkv','mov'])
def test_embedded_subtitle_and_audio_inventory(media,tmp_path,run_cli,extension):
    out=tmp_path/f'output.{extension}'
    result=run_cli(media['multi'],out,options='-t 2.5 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    info=probe_media(str(out))
    assert {kind:stream_count(info,kind) for kind in ['video','audio','subtitle']} == {
        'video':1,'audio':2,'subtitle':2}
    subs=[s for s in info['streams'] if s['codec_type']=='subtitle']
    assert [s.get('tags',{}).get('language') for s in subs] == ['por','por']
    # FFmpeg 7.1's MOV muxer drops forced flags even with explicit
    # -disposition; MP4 and Matroska preserve them. This is not a codec test.
    if extension != 'mov':
        assert subs[1]['disposition']['forced'] == 1


def test_noise_reduction_never_blocks_the_conversion(media,tmp_path,run_cli):
    """The noise model is optional. Installed, it filters the audio; missing,
    it warns — either way the conversion the user asked for still happens."""
    out=tmp_path/'denoised.mkv'
    result=run_cli(media['video'],out,noise_reduction='1',noise_strength='0.5',
                   audio_handling='reencode',options='-t 0.5 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    if not Path(NOISE_MODELS[0][0]).exists():
        assert 'WARNING: Noise reduction requested' in result.stdout
    assert stream_count(probe_media(str(out)),'audio') == 1


def _installed_noise_models():
    return [pytest.param(model, marks=pytest.mark.skipif(
        not Path(entry[0]).exists(), reason=f'{entry[4]} not installed'))
        for model, entry in enumerate(NOISE_MODELS)]


@pytest.mark.parametrize('model', _installed_noise_models())
def test_noise_reduction_keeps_every_channel_of_surround_audio(media, tmp_path, run_cli, model):
    """The plugins are mono; FFmpeg runs one instance per channel."""
    source = tmp_path / 'surround.mkv'
    media_command('-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=25', '-f', 'lavfi',
                  '-i', 'sine=frequency=440:sample_rate=48000', '-t', '1', '-c:v', 'libx264',
                  '-threads', '1', '-c:a', 'aac', '-ac', '6', '-y', source)
    out = tmp_path / 'denoised.mkv'
    result = run_cli(source, out, noise_reduction='1', noise_model=model,
                     audio_handling='reencode')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Noise reduction enabled' in result.stdout
    audio = [s for s in probe_media(str(out))['streams'] if s['codec_type'] == 'audio']
    assert [s['channels'] for s in audio] == [6]


@pytest.mark.parametrize('model', _installed_noise_models())
def test_the_preview_denoises_with_the_export_chain(media, tmp_path, run_cli, model):
    """The editor's mpv preview builds the chain in Python; the export script
    builds it in shell. What is heard must be what is exported."""
    result = run_cli(media['video'], tmp_path / 'out.mkv', noise_reduction='1',
                     noise_model=model, noise_strength='0.7', audio_handling='reencode',
                     options='-t 0.2 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert noise_reduction_filter(model, 0.7) in result.stdout


def test_noise_strength_is_linear_up_to_each_models_cap():
    """Past 24 dB DFN3 removes no more noise; a squared 0-100 dB curve left
    the top half of the slider doing nothing."""
    assert 'controls=c0=12.00|c6=0,' in noise_reduction_filter(0, 0.5)
    assert 'controls=c0=24.00|c6=0,' in noise_reduction_filter(0, 1.0)
    assert 'controls=c0=24.00,' in noise_reduction_filter(1, 0.5)
    assert 'controls=c0=48.00,' in noise_reduction_filter(1, 3.0)


def _pcm(path, *options):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), *options, '-ac', '1',
                          '-ar', '48000', '-f', 'f32le', '-'], capture_output=True, check=True)
    return array('f', raw.stdout)


@pytest.mark.parametrize('model', _installed_noise_models())
@pytest.mark.parametrize('seconds', [0.3, 2])
def test_noise_reduction_keeps_timing_and_the_whole_audio(tmp_path, model, seconds):
    """At strength 0 the plugin passes its input through, only delayed: the
    chain must give back every sample in place, including a clip shorter than
    its half-second lead-in, and keep a stream's starting timestamp."""
    source = tmp_path / 'tone.wav'
    media_command('-f', 'lavfi', '-i', 'sine=frequency=330:sample_rate=48000',
                  '-af', 'volume=0.5', '-t', str(seconds), '-c:a', 'pcm_f32le', '-y', source)
    chain = noise_reduction_filter(model, 0.0)
    original = _pcm(source)
    filtered = _pcm(source, '-af', chain)
    assert len(filtered) == len(original)
    assert max(abs(a - b) for a, b in zip(original, filtered)) < 1e-4

    shown = subprocess.run(['ffmpeg', '-v', 'info', '-i', str(source), '-af',
                            f'asetpts=PTS+2.5/TB,{chain},ashowinfo', '-f', 'null', '-'],
                           capture_output=True, text=True, check=True).stderr
    start = float(re.search(r'n:0 pts:\S+ pts_time:(\S+)', shown).group(1))
    assert abs(start - 2.5) < 1e-3


def test_webm_with_default_codecs_encodes_legal_ones_once(media, tmp_path, run_cli):
    """H.264 and AAC used to fail after two full encodes."""
    out = tmp_path / 'web.webm'
    result = run_cli(media['video'], out, video_encoder='h264', audio_handling='copy')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('Encode mode:') == 1 and 'Retrying' not in result.stdout
    assert [s['codec_name'] for s in probe_media(str(out))['streams']] == ['vp9', 'opus']


def test_audio_is_retried_only_for_an_audio_failure(media, tmp_path, run_cli):
    """A full disk or a missing encoder ran every stage twice more."""
    failed = run_cli(media['video'], tmp_path / 'broken.mkv', options='-c:v no_such_encoder')
    assert failed.returncode != 0 and 'Retrying' not in failed.stdout
    source = tmp_path / 'wma.mkv'
    media_command('-i', media['video'], '-map', '0', '-c:v', 'copy', '-c:a', 'wmav2', '-y', source)
    out = tmp_path / 'fixed.mp4'
    result = run_cli(source, out, options='-t 1 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Retrying with audio re-encoding' in result.stdout
    assert [s['codec_name'] for s in probe_media(str(out))['streams']] == ['h264', 'aac']


@pytest.mark.parametrize('bitrate', ['', '500000'])
def test_amd_hevc_keeps_bitrate_keyframes_and_tag(media, tmp_path, run_cli, bitrate):
    """AMD's constant-QP HEVC command was rebuilt after the bitrate, GOP and
    hvc1 tag had been applied, dropping all three."""
    from test_probe_and_driver_fallback import _fake_ffmpeg
    wrapper = _fake_ffmpeg(tmp_path, 'no-gpu',
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 1; done')
    result = run_cli(media['video'], tmp_path / 'out.mp4', gpu='amd', force_software='',
                     gpu_smoke_test='0', video_encoder='h265', video_bitrate=bitrate,
                     keyframe_interval='2', ffmpeg_executable=wrapper, options='-t 1 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    vaapi = [line for line in result.stdout.splitlines()
             if line.startswith('Running command:') and 'hevc_vaapi' in line]
    assert vaapi, result.stdout
    for line in vaapi:
        assert ' -g 50 ' in line and ' -tag:v hvc1 ' in line and 'ICQ' not in line
        assert (' -rc_mode VBR -b:v 500000 ' in line) if bitrate else (' -rc_mode CQP ' in line)


def _group_members(pgid):
    members = []
    for stat_file in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = stat_file.read_text().rpartition(')')[2].split()
        except OSError:
            continue
        if int(fields[2]) == pgid:
            members.append(stat_file.parent.name)
    return members


@pytest.mark.parametrize('target', ['group', 'script'])
def test_cancel_during_gpu_check_ends_the_job_at_once(media, tmp_path, cli_env, target):
    """timeout ran in a process group of its own and bash deferred its TERM
    trap until the check finished: an orphan FFmpeg and workspace for 35 s."""
    import os
    import signal
    import time

    from test_probe_and_driver_fallback import _fake_ffmpeg
    wrapper = _fake_ffmpeg(tmp_path, 'hanging-gpu',
        'for arg in "$@"; do [[ $arg == color=c=gray* ]] && exec sleep 60; done')
    env = {**cli_env, 'gpu': 'nvidia', 'force_software': '', 'ffmpeg_executable': str(wrapper),
           'output_file': str(tmp_path / 'out.mkv')}
    log = tmp_path / 'log'
    with log.open('wb') as output:
        job = subprocess.Popen(['bash', str(CLI), str(media['video'])], env=env,
                               stdout=output, stderr=output, start_new_session=True)
    try:
        deadline = time.monotonic() + 20
        while 'Checking GPU encoder' not in log.read_text() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        started = time.monotonic()
        if target == 'group':
            os.killpg(job.pid, signal.SIGTERM)
        else:
            job.send_signal(signal.SIGTERM)
        assert job.wait(timeout=10) == 143
        assert time.monotonic() - started < 7
        time.sleep(0.2)
        assert _group_members(job.pid) == []
        assert not list(tmp_path.glob('.bvc-*'))
    finally:
        if job.poll() is None:
            os.killpg(job.pid, signal.SIGKILL)
            job.wait()


def test_no_audio_does_not_duplicate_video(media,tmp_path,run_cli):
    out=tmp_path/'silent.mp4'
    result=run_cli(media['silent'],out,options='-t 0.5 -threads 1')
    assert result.returncode==0,result.stdout+result.stderr
    assert [s['codec_type'] for s in probe_media(str(out))['streams']]==['video']


def test_only_extract_preserves_original_and_distinguishes_languages(media,tmp_path,run_cli):
    out=tmp_path/'extract.mp4'
    before=media['multi'].read_bytes()
    result=run_cli(media['multi'],out,subtitle_extract='extract',only_extract_subtitles='1')
    assert result.returncode==0,result.stdout+result.stderr
    assert not out.exists()
    assert sorted(path.name for path in tmp_path.glob('extract.por*.srt'))==[
        'extract.por.forced.srt','extract.por.srt']
    assert media['multi'].read_bytes()==before


def test_extra_positional_output_rejected(media,tmp_path,run_cli):
    out=tmp_path/'out.mp4'; extra=tmp_path/'extra.mp4'
    result=run_cli(media['video'],out,options=str(extra))
    assert result.returncode!=0
    assert not out.exists() and not extra.exists()


def test_filters_and_trim_applied(media,tmp_path,run_cli):
    out=tmp_path/'edited.mkv'
    result=run_cli(media['video'],out,video_filter='hflip,scale=64:36',
                   options='-ss 1 -t 0.8 -threads 1',audio_handling='reencode')
    assert result.returncode==0,result.stdout+result.stderr
    data=probe_media(str(out));v=data['streams'][0]
    assert (v['width'],v['height'])==(64,36)
    assert abs(media_duration(data)-.8)<.2


def test_encoding_failure_never_publishes_partial_output(media,tmp_path,run_cli):
    out=tmp_path/'broken.mp4'
    result=run_cli(media['video'],out,options='-c:v no_such_encoder')
    assert result.returncode!=0
    assert not out.exists()
    assert not list(tmp_path.glob('.bvc*'))


def test_two_jobs_on_one_destination_keep_both_files(media,tmp_path,cli_env):
    """The name is claimed exclusively, so the second job to finish takes the
    next free one instead of replacing the first job's file."""
    out=tmp_path/'shared.mp4'
    env={**cli_env,'output_file':str(out),'options':'-t 0.5 -threads 1'}
    command=['bash',str(CLI),str(media['video'])]
    with (tmp_path/'a.log').open('wb') as a,(tmp_path/'b.log').open('wb') as b:
        first=subprocess.Popen(command,env=env,stdout=a,stderr=a)
        second=subprocess.Popen(command,env=env,stdout=b,stderr=b)
        codes=[first.wait(timeout=30),second.wait(timeout=30)]
    assert codes==[0,0]
    for path in (out,tmp_path/'shared_1.mp4'):
        assert stream_count(probe_media(str(path)),'video')==1
    named={line for log in ('a.log','b.log') for line in (tmp_path/log).read_text().splitlines()
           if line.startswith('Output file:')}
    assert named=={f'Output file: {out}',f'Output file: {tmp_path}/shared_1.mp4'}
    assert not list(tmp_path.glob('.bvc*'))


@pytest.mark.parametrize('only_subtitles', ['0', '1'])
def test_existing_sidecar_is_kept_and_the_track_numbered(media, tmp_path, run_cli, only_subtitles):
    """The video was already published when a taken sidecar name used to fail
    the whole job; the track now takes the next number."""
    existing = tmp_path / 'extract.por.srt'
    existing.write_text('Manually corrected subtitles', encoding='utf-8')
    result = run_cli(media['multi'], tmp_path / 'extract.mp4', subtitle_extract='extract',
                     only_extract_subtitles=only_subtitles, options='-t 2.5 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert existing.read_text() == 'Manually corrected subtitles'
    assert sorted(path.name for path in tmp_path.glob('extract.por*.srt')) == [
        'extract.por.forced.srt', 'extract.por.srt', 'extract.por2.srt']
    assert 'Primeira fala' in (tmp_path / 'extract.por2.srt').read_text()
    assert (tmp_path / 'extract.mp4').exists() == (only_subtitles == '0')
    assert not list(tmp_path.glob('.bvc-*'))


def _with_subtitle(media, path, codec):
    media_command('-i', media['video'], '-i', media['srt'], '-map', '0', '-map', '1',
                  '-c:v', 'copy', '-c:a', 'copy', '-c:s', codec, '-y', path)
    return path


@pytest.mark.parametrize('name,codec', [('styled.mkv', 'ass'), ('phone.mp4', 'mov_text')])
def test_every_text_subtitle_codec_is_extracted(media, tmp_path, run_cli, name, codec):
    """Only SubRip used to be extracted: an ASS or mov_text track vanished
    while the job reported no subtitle to keep the original for."""
    source = _with_subtitle(media, tmp_path / name, codec)
    result = run_cli(source, tmp_path / 'out.mkv', subtitle_extract='extract')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Segunda fala' in (tmp_path / 'out.und.srt').read_text()


def test_mov_text_is_stored_as_subrip_in_matroska(media, tmp_path, run_cli):
    source = _with_subtitle(media, tmp_path / 'phone.mp4', 'mov_text')
    out = tmp_path / 'out.mkv'
    result = run_cli(source, out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [s['codec_name'] for s in probe_media(str(out))['streams']
            if s['codec_type'] == 'subtitle'] == ['subrip']


def pgs_source(folder):
    """A video with one picture subtitle (PGS): FFmpeg encodes none from text."""
    import struct

    def segment(kind, payload, pts):
        return b'PG' + struct.pack('>IIBH', pts, pts, kind, len(payload)) + payload

    def display(pts, show):
        pcs = struct.pack('>HHBHBBBB', 128, 72, 0x10, 0 if show else 1, 0x80 if show else 0,
                          0, 0, 1 if show else 0)
        if show:
            pcs += struct.pack('>HBBHH', 0, 0, 0, 10, 10)
        out = segment(0x16, pcs, pts) + segment(0x17, struct.pack('>BBHHHH', 1, 0, 10, 10, 8, 2), pts)
        if show:
            rle = bytes([0, 0x88, 1, 0, 0]) * 2
            out += segment(0x14, bytes([0, 0, 1, 235, 128, 128, 255]), pts)
            out += segment(0x15, struct.pack('>HBB', 0, 0, 0xC0) + (len(rle) + 4).to_bytes(3, 'big')
                           + struct.pack('>HH', 8, 2) + rle, pts)
        return out + segment(0x80, b'', pts)

    sup = folder / 'picture.sup'
    sup.write_bytes(display(18000, True) + display(126000, False))
    source = folder / 'picture.mkv'
    media_command('-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=25', '-f', 'sup', '-i', sup,
                  '-t', '2', '-map', '0', '-map', '1', '-c:v', 'libx264', '-threads', '1',
                  '-c:s', 'copy', '-y', source)
    return source


@pytest.mark.parametrize('mode,extension,kept', [
    ('embedded', 'mp4', 0), ('extract', 'mkv', 0), ('embedded', 'mkv', 1)])
def test_left_out_picture_subtitles_are_announced(tmp_path, run_cli, mode, extension, kept):
    """The GUI keeps the original when it reads the warning."""
    out = tmp_path / f'out.{extension}'
    result = run_cli(pgs_source(tmp_path), out, subtitle_extract=mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ('Warning: skipping subtitle stream' in result.stdout) == (not kept)
    assert stream_count(probe_media(str(out)), 'subtitle') == kept


def _frames(path):
    return int(subprocess.run(
        ['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
         '-show_entries', 'stream=nb_read_frames,r_frame_rate', '-of', 'csv=p=0', str(path)],
        capture_output=True, text=True, check=True).stdout.strip().split(',')[1])


@pytest.mark.parametrize('fps,frames', [('10', 30), ('50', 150)])
def test_frame_rate_drops_or_blends_frames(media, tmp_path, run_cli, fps, frames):
    out = tmp_path / f'{fps}.mkv'
    result = run_cli(media['video'], out, video_fps=fps)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ('fps=' if fps == '10' else 'framerate=fps=') + fps in result.stdout
    assert abs(_frames(out) - frames) <= 2


def test_invalid_frame_rate_is_refused(media, tmp_path, run_cli):
    result = run_cli(media['video'], tmp_path / 'out.mkv', video_fps='24;rm')
    assert result.returncode == 2 and 'Invalid frame rate' in result.stderr


def test_interlaced_source_is_deinterlaced(media, tmp_path, run_cli):
    source = tmp_path / 'interlaced.mkv'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x240:rate=50', '-t', '2', '-vf',
                    'tinterlace=mode=interleave_top,setfield=tff', '-c:v', 'libx264',
                    '-flags', '+ildct+ilme', '-x264opts', 'tff=1', '-threads', '1', '-y',
                    str(source)], check=True, timeout=30)
    out = tmp_path / 'progressive.mkv'
    result = run_cli(source, out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Interlaced video' in result.stdout and 'bwdif=' in result.stdout
    # idet still calls a few of testsrc2's fine moving lines interlaced; a
    # combed picture is every frame of it.
    assert _interlaced_frames(out) * 5 <= _interlaced_frames(source)


def _interlaced_frames(path):
    idet = subprocess.run(['ffmpeg', '-nostdin', '-i', str(path), '-vf', 'idet', '-f', 'null', '-'],
                          capture_output=True, text=True, timeout=30, check=False).stderr
    multi = [line for line in idet.splitlines() if 'Multi frame detection' in line][-1]
    return sum(int(n) for n in re.findall(r'[TB]FF:\s*(\d+)', multi))


def _has_filter(name):
    listing = subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True, text=True, check=False).stdout
    return any(line.split()[1:2] == [name] for line in listing.splitlines())


def test_stabilization_analyses_first_in_any_folder(media, tmp_path, run_cli):
    if not _has_filter('vidstabdetect'):
        pytest.skip('FFmpeg without vid.stab')
    # The motion file lives in the job's workspace, beside the output: its
    # path goes inside the filter graph and must survive being parsed there.
    folder = tmp_path / "a,b:c'd[e];f"
    folder.mkdir()
    out = folder / 'steady.mkv'
    result = run_cli(media['video'], out, video_stabilize='1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Stabilization analysis' in result.stdout and 'vidstabtransform=' in result.stdout
    assert _frames(out) == _frames(media['video'])


def test_effect_file_path_survives_the_filter_graph(media, tmp_path, run_cli):
    from utils.video_settings import effect_file_filter

    folder = tmp_path / "look ,:'[];\\ ç"
    folder.mkdir()
    lut = folder / 'warm.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + ''.join(
        f'{r} {g} {b}\n' for b in (0, 1) for g in (0, 1) for r in (0, 1)), encoding='utf-8')
    out = tmp_path / 'look.mkv'
    result = run_cli(media['video'], out, video_filter=f'hflip,{effect_file_filter(str(lut))}')
    assert result.returncode == 0, result.stdout + result.stderr


def _untagged_pq(tmp_path):
    """10-bit PQ picture whose file describes no colours at all, like a DVB
    recording: the transfer is in the samples only."""
    source = tmp_path / 'untagged-pq.mkv'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x90:rate=25', '-t', '1', '-vf',
                    ('zscale=t=smpte2084:p=bt2020:m=bt2020nc:tin=bt709:pin=bt709:min=bt709:npl=100,format=yuv420p10le,'
                     'setparams=color_primaries=unknown:color_trc=unknown:colorspace=unknown'),
                    '-c:v', 'libx265', '-x265-params', 'log-level=error', '-y', str(source)],
                   check=True, timeout=60)
    return source


def _colour(path):
    return subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                           'stream=pix_fmt,color_transfer', '-of', 'csv=p=0', str(path)],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_user_declared_hdr_is_tone_mapped_or_kept_tagged(tmp_path, run_cli):
    source = _untagged_pq(tmp_path)
    plain = run_cli(source, tmp_path / 'plain.mkv', video_encoder='h264')
    assert plain.returncode == 0 and 'Detected HDR' not in plain.stdout
    mapped = run_cli(source, tmp_path / 'sdr.mkv', video_encoder='h264', source_hdr='pq')
    assert mapped.returncode == 0, mapped.stdout + mapped.stderr
    assert 'set by the user' in mapped.stdout and 'tonemap=' in mapped.stdout
    assert _colour(tmp_path / 'sdr.mkv') == 'yuv420p,bt709'
    kept = run_cli(source, tmp_path / 'hdr.mkv', video_encoder='h265', source_hdr='pq')
    assert kept.returncode == 0, kept.stdout + kept.stderr
    assert _colour(tmp_path / 'hdr.mkv') == 'yuv420p10le,smpte2084'


def test_invalid_source_hdr_is_refused(media, tmp_path, run_cli):
    result = run_cli(media['video'], tmp_path / 'out.mkv', source_hdr='dolby')
    assert result.returncode == 2 and 'Invalid source_hdr' in result.stderr
