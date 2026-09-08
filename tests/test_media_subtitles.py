import shutil
import subprocess
import threading

import pytest

from utils import media_validation as mv
from utils.subtitle_processor import SubtitleProcessor, _clip, _read_cues, _serialize, _milliseconds, _format_timecode


@pytest.mark.parametrize('start,end,expected', [
    (1000,2000,[(0,1000,'speech')]), (0,1000,[(500,1000,'speech')]),
    (2000,3000,[(0,500,'speech')]), (2500,3000,[]), (0,500,[]),
])
def test_subtitles_intersect_both_boundaries(start, end, expected):
    assert _clip([(500,2500,'speech')], start, end, 0) == expected


def test_srt_rounding_carries_to_next_minute_and_renumbers(tmp_path):
    assert _format_timecode(_milliseconds('59.9999')) == '00:01:00,000'
    path = tmp_path/'captions.srt'
    path.write_text('\ufeff1\r\n00:00:00,000 --> 00:00:01,000\r\nFala\r\n')
    cues = _read_cues(path)
    text = _serialize(cues + [(1000,2000,'Outra')])
    assert '\n\n2\n' in text
    assert cues == [(0,1000,'Fala')]


def test_same_language_tracks_have_distinct_sidecars_and_one_extraction(media, tmp_path, monkeypatch):
    processor = SubtitleProcessor(str(media['multi']), str(tmp_path), 'joined.mkv',
        [{'start':.5,'end':1.5},{'start':2,'end':2.5}], str(tmp_path), 'extract')
    calls = []
    import utils.subtitle_processor as module
    run = module.subprocess.Popen
    def record(cmd, *args, **kwargs):
        if '-c:s' in cmd: calls.append(cmd)
        return run(cmd, *args, **kwargs)
    monkeypatch.setattr(module.subprocess, 'Popen', record)
    processor.process()
    files = list(tmp_path.glob('joined.por.s*.srt'))
    assert len(files) == 2
    assert len(calls) == 2
    assert files[0].read_bytes() == files[1].read_bytes()
    cues = _read_cues(files[0])
    assert cues[0][0] == 0
    assert max(cue[1] for cue in cues) <= 1500


def test_output_checks_nonempty_not_sufficient(tmp_path):
    partial = tmp_path/'partial.mkv'
    partial.write_bytes(b'not media but nonempty')
    with pytest.raises(Exception): mv.validate_output(str(partial))


def test_valid_output_checks_duration_identity_and_stream_inventory(media, tmp_path):
    out = tmp_path/'out.mp4'
    shutil.copyfile(media['video'], out)
    data = mv.validate_output(str(out), source=str(media['video']), expected_duration=3,
                              expected_streams={'video':1,'audio':1,'subtitle':0})
    assert mv.stream_count(data, 'audio') == 1
    with pytest.raises(ValueError): mv.validate_output(str(out), expected_duration=30)
    with pytest.raises(ValueError): mv.validate_output(str(out), expected_streams={'audio':2})
    with pytest.raises(ValueError): mv.validate_output(str(media['video']), source=str(media['video']))
    link = tmp_path/'link.mp4';link.symlink_to(out)
    with pytest.raises(ValueError): mv.validate_output(str(link))


def test_no_clobber_publication(tmp_path):
    staged, dest = tmp_path/'stage', tmp_path/'dest'
    staged.write_bytes(b'new');dest.write_bytes(b'old')
    with pytest.raises(FileExistsError): mv.publish_output(str(staged), str(dest))
    assert dest.read_bytes() == b'old'
    dest.unlink();mv.publish_output(str(staged), str(dest))
    assert dest.read_bytes() == b'new'


def test_killed_job_leftovers_are_dropped_by_announced_path(tmp_path):
    workspace = tmp_path/'.bvc.abcd1234'
    workspace.mkdir();(workspace/'video.mkv').write_bytes(b'partial')
    reserved = tmp_path/'reserved.mkv';reserved.touch()
    unrelated = tmp_path/'someone-elses.mkv';unrelated.write_bytes(b'content')
    other = tmp_path/'not-ours';other.mkdir()
    mv.discard_job_paths(str(workspace), [str(reserved), str(unrelated)])
    assert not workspace.exists() and not reserved.exists()
    assert unrelated.read_bytes() == b'content'
    mv.discard_job_paths(str(other), [])
    assert other.exists()


def test_changed_original_and_cancelled_job_never_reach_deletion(media, tmp_path, monkeypatch):
    src,out = tmp_path/'src.mp4', tmp_path/'out.mp4'
    shutil.copyfile(media['video'], src);shutil.copyfile(src,out)
    identity = mv.FileIdentity.capture(str(src))
    monkeypatch.setattr(mv,'verify_integrity',lambda *a,**kw:None)
    with src.open('ab') as handle:handle.write(b'changed')
    with pytest.raises(ValueError, match='original changed'):
        mv.remove_original(str(src), identity, [str(out)], threading.Event())
    event=threading.Event();event.set()
    with pytest.raises(InterruptedError):
        mv.remove_original(str(src), mv.FileIdentity.capture(str(src)), [str(out)], event)
    assert src.exists()


def test_removal_requires_a_validated_output(media, tmp_path):
    src,out = tmp_path/'src.mp4', tmp_path/'out.mp4'
    shutil.copyfile(media['video'], src);shutil.copyfile(src,out)
    identity = mv.FileIdentity.capture(str(src))
    with pytest.raises(ValueError, match='No validated output'):
        mv.remove_original(str(src), identity, [], threading.Event())
    assert src.exists()
    mv.remove_original(str(src), identity, [str(out)], threading.Event())
    assert not src.exists() and out.exists()


def test_integrity_check_accepts_a_complete_file(media):
    mv.verify_integrity(str(media['video']), threading.Event())


@pytest.mark.parametrize('container,fraction', [('mp4',0.75), ('mkv',0.60)])
def test_integrity_check_rejects_a_truncated_output(media, tmp_path, container, fraction):
    """A short Matroska file still probes successfully and exits 0."""
    whole = tmp_path/f'whole.{container}'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(media['video']),
                    '-c','copy','-y',str(whole)], check=True, timeout=30)
    frames = int(subprocess.run(['ffprobe','-v','error','-select_streams','v:0',
        '-count_packets','-show_entries','stream=nb_read_packets','-of','csv=p=0',
        str(whole)], check=True, capture_output=True, text=True, timeout=30).stdout)
    cut = tmp_path/f'cut.{container}'
    cut.write_bytes(whole.read_bytes()[:int(whole.stat().st_size * fraction)])
    with pytest.raises(ValueError):
        mv.verify_integrity(str(cut), threading.Event(), expected_frames=frames)


def test_short_or_unmeasured_media_is_decoded_in_full():
    for duration in (None, 0.0, float('inf'), 3.0, mv.FULL_DECODE_SECONDS):
        assert mv.sample_windows(duration) == ((0.0, float('inf')),)


def test_sampled_windows_stay_bounded_for_long_media():
    for duration in (60.0, 3600.0, 36000.0):
        windows = mv.sample_windows(duration)
        assert len(windows) == 5 and sum(length for _, length in windows) == 7.0
        assert windows[0][0] == 0.0 and windows[1][0] == duration - 2.0
        assert all(0.0 <= start <= duration - length for start, length in windows)

@pytest.mark.parametrize('options,limit', [
    (['-ss','00:00:00.500','-t','1'],'1.500'),
    (['-ss','500ms','-to','1.5s'],'1.5'),
    (['-ss','1'],'')])
def test_packet_clip_uses_only_parsed_timestamps(options,limit):
    from utils.subtitle_timing import clipping_options
    end,filters=clipping_options(options)
    assert end==limit and 'noise=amount=0:' in filters and 'setts=pts=' in filters

@pytest.mark.parametrize('value',['nan','inf','-1','00:99:00','1+2',"0');movie=evil(",''])
def test_timestamp_expressions_are_not_accepted(value):
    from utils.subtitle_timing import seconds
    with pytest.raises(ValueError):seconds(value)

@pytest.mark.parametrize('extension',['mkv','mp4'])
def test_embedded_subtitle_crossing_trim_start_and_end_is_clipped(media,tmp_path,run_cli,extension):
    out=tmp_path/f'clipped.{extension}'
    result=run_cli(media['multi'],out,options='-ss 0.5 -t 1 -threads 1')
    assert result.returncode==0,result.stderr[-3000:]
    result=subprocess.run(['ffmpeg','-v','error','-i',str(out),'-map','0:s:0','-c:s','srt','-f','srt','-'],capture_output=True,text=True,check=True)
    assert '00:00:00,000 --> 00:00:01,000' in result.stdout
    assert 'Primeira fala' in result.stdout and 'Segunda fala' not in result.stdout


def test_extracted_subtitle_uses_same_trim_interval(media,tmp_path,run_cli):
    result=run_cli(media['multi'],tmp_path/'extract.mp4',only_extract_subtitles='1',options='-ss 0.5 -t 1')
    assert result.returncode==0,result.stderr[-2000:]
    outputs=list(tmp_path.glob('extract.por.s*.srt'))
    assert len(outputs)==2 and not (tmp_path/'extract.mp4').exists()
    for path in outputs:
        text=path.read_text()
        assert '00:00:00,000 --> 00:00:01,000' in text and 'Segunda fala' not in text
