from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import CLI
from utils.media_validation import probe_media, stream_count, media_duration


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


def test_existing_destination_is_replaced_only_by_a_finished_file(media,tmp_path,run_cli):
    """The CLI overwrites what it was told to write, but the old file stays
    readable until the new one is complete."""
    source=tmp_path/'original.mp4';shutil.copyfile(media['video'],source)
    dest=tmp_path/'out.mkv';dest.write_bytes(b'KEEP')
    result=run_cli(source,dest,options='-t 0.4 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert dest.read_bytes() != b'KEEP'
    assert stream_count(probe_media(str(dest)),'video') == 1
    assert source.exists()
    broken=tmp_path/'kept.mkv';broken.write_bytes(b'KEEP')
    result=run_cli(source,broken,options='-c:v no_such_encoder')
    assert result.returncode != 0
    assert broken.read_bytes() == b'KEEP'


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
    """The GTCRN plugin is optional. Installed, it filters the audio; missing,
    it warns — either way the conversion the user asked for still happens."""
    out=tmp_path/'denoised.mkv'
    result=run_cli(media['video'],out,noise_reduction='1',noise_strength='0.5',
                   audio_handling='reencode',options='-t 0.5 -threads 1')
    assert result.returncode == 0, result.stdout + result.stderr
    if not Path('/usr/lib/ladspa/libgtcrn_ladspa.so').exists():
        assert 'WARNING: Noise reduction requested' in result.stdout
    assert stream_count(probe_media(str(out)),'audio') == 1


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


def test_two_jobs_on_one_destination_leave_a_whole_file(media,tmp_path,cli_env):
    """Each job publishes with a single rename, so the loser is replaced
    wholesale instead of being interleaved into a torn file."""
    out=tmp_path/'shared.mp4'
    env={**cli_env,'output_file':str(out),'options':'-t 0.5 -threads 1'}
    command=['bash',str(CLI),str(media['video'])]
    with (tmp_path/'a.log').open('wb') as a,(tmp_path/'b.log').open('wb') as b:
        first=subprocess.Popen(command,env=env,stdout=a,stderr=a)
        second=subprocess.Popen(command,env=env,stdout=b,stderr=b)
        codes=[first.wait(timeout=30),second.wait(timeout=30)]
    assert codes==[0,0]
    assert stream_count(probe_media(str(out)),'video')==1
    assert not list(tmp_path.glob('.bvc*'))
