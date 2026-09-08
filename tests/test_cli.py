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


@pytest.mark.parametrize('mode', ['existing','same','symlink'])
def test_never_overwrite_unowned_destination(media,tmp_path,run_cli,mode):
    source=tmp_path/'original.mp4';shutil.copyfile(media['video'],source)
    before=source.read_bytes()
    dest=source if mode=='same' else tmp_path/'out.mp4'
    if mode=='existing':dest.write_bytes(b'KEEP')
    if mode=='symlink':dest.symlink_to(source)
    result=run_cli(source,dest)
    assert result.returncode != 0
    assert source.read_bytes() == before
    if mode=='existing':assert dest.read_bytes()==b'KEEP'


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
    assert len(list(tmp_path.glob('extract.por.s*.srt')))==2
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
    assert not list(tmp_path.glob('.bvc.*'))


def test_two_jobs_cannot_clobber_same_destination(media,tmp_path,cli_env):
    out=tmp_path/'shared.mp4'
    env={**cli_env,'output_file':str(out),'options':'-t 0.5 -threads 1'}
    command=['bash',str(CLI),str(media['video'])]
    with (tmp_path/'a.log').open('wb') as a,(tmp_path/'b.log').open('wb') as b:
        first=subprocess.Popen(command,env=env,stdout=a,stderr=a)
        second=subprocess.Popen(command,env=env,stdout=b,stderr=b)
        codes=[first.wait(timeout=30),second.wait(timeout=30)]
    assert sorted(code==0 for code in codes)==[False,True]
    assert stream_count(probe_media(str(out)),'video')==1
