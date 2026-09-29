import json
import math
from pathlib import Path

import pytest
from utils.ffmpeg_options import (
    file_access_settings,
    parse_additional_options,
    validate_additional_options,
)
from utils.settings_manager import SettingsManager


@pytest.mark.parametrize('text,expected', [
    ('', []), ('-ss 1 -t 2 -threads 1', ['-ss','1','-t','2','-threads','1']),
    ('-metadata title="A sample title" -sn', ['-metadata','title=A sample title','-sn']),
    ('-map 0:v:0 -map -0:a:1 -c:v copy', ['-map','0:v:0','-map','-0:a:1','-c:v','copy']),
    ('-itsoffset -0.5 -disposition:a:0 -default', ['-itsoffset','-0.5','-disposition:a:0','-default']),
    ('-vn -an -sn -dn', ['-vn','-an','-sn','-dn']),
])
def test_option_grammar_accepts_known_arity(text, expected):
    assert parse_additional_options(text) == expected
    okay, normalized = validate_additional_options(text)
    assert okay and parse_additional_options(normalized) == expected


@pytest.mark.parametrize('text', [
    'out.mp4', '-y out.mp4', '-i secret.mp4', '-t', '-ss -t 1',
    '-threads 1 out.mp4', '-metadata title=x out.mp4', '-t 1;touch x',
    '-vf "movie=/tmp/a;scale=100:100"', '-metadata title=$(touch_marker)',
    '-filter_complex_script /tmp/filter', '-t "unterminated',
    '-threads 1\0', '-map', '-metadata ""', '-unknown:value 1',
])
def test_option_grammar_rejects_additional_outputs(text):
    with pytest.raises(ValueError):
        parse_additional_options(text)
    assert validate_additional_options(text)[0] is False


REACHES_FILES = [
    '-af ladspa=file=/tmp/evil.so:plugin=x', '-af ladspa=evil', '-vf frei0r=filter_name=x',
    '-af lv2=p=urn:evil', '-vf drawtext=textfile=/etc/passwd', '-lavfi movie=secret.mp4',
    '-filter_complex amovie=private.wav', '-vf sendcmd=f=cmds,scale=1:1',
    "-vf scale=1:1,'mo''vie'=secret.mp4", '-filter:a volume=1,azmq',
    '-vf [in]subtitles=f=secret.srt', '-vf drawtext=fontsize=9:/text=/etc/passwd',
    '-vf vidstabdetect=result=.bashrc', '-vf vmafmotion=stats_file=s.log',
    '-vf vidstabtransform=input=t.trf', '-vf drawtext=fontfile=../../f.ttf:text=a',
    '-vf drawtext=f.ttf', '-af sofalizer=sofa=h.sofa', '-vf removelogo=f=logo.png',
    '-vf find_rect=object=o.pgm', '-vf cover_rect=cover=c.jpg',
    '-vf libplacebo=custom_shader_path=s.glsl', '-vf lensfun=db_path=db',
    '-vf libvmaf=log_path=v.log', '-vf libvmaf=model_path=m.json',
    '-vf curves=psfile=c.acv', '-vf curves=plot=c.plt',
    '-af loudnorm=stats_file=l.json', '-vf deshake=filename=d.log', '-vf hue=h=/etc/x',
    '-vf scale@a=1:1,metadata=mode=print:file=m.txt', '-vf [in]scale=1:1[a],movie=x',
    '-af firequalizer=dumpfile=f', '-vf vidstabdetect', '-vf lut3d=file=/abs/x.cube',
    '-vf subtitles=subs.srt', '-vf frei0r=glow', '-lavfi movie=clip.mp4',
    """-vf "movie='file:/etc/passwd'" """, '-vf movie=http://x', """-vf "hue=h='http://x'" """,
    '-x264-params stats=/tmp/x.log', '-x264-params keyint=60:pass=1',
    '-x264opts qpfile=frames.txt', '-x264-params dump_yuv=out.yuv',
    '-x264-params cqmfile=m.cfg', '-x265-params csv=out.csv:csv-log-level=2',
    '-x265-params analysis-save=a.dat', '-x265-params "analysis-load=a.dat"',
    '-x265-params zonefile=z.txt', '-x265-params recon=r.yuv',
    "-x265-params 'Stats'=x", '-x265-params:v:0 crf=20:lambda-file=l.txt',
    '-svtav1-params fgs-table=grain.tbl', '-svtav1-params tune=0:stat-file=s',
    '-x264-params keyint=../x',
]


@pytest.mark.parametrize('text', REACHES_FILES)
def test_any_filter_is_accepted_and_file_access_is_reported(text):
    """Users customise conversions with every filter FFmpeg has; imported text
    naming files, libraries or the network is only confirmed, never refused."""
    parse_additional_options(text)
    assert file_access_settings(text)


def test_file_access_names_the_setting_itself():
    assert file_access_settings('-vf scale=640:-2,vidstabdetect=result=.bashrc,hflip') == [
        'vidstabdetect=result=.bashrc']
    assert file_access_settings("-vf \"movie='file:/etc/passwd',scale=1:1\"") == ["movie='file:/etc/passwd'"]
    assert file_access_settings('-x264-params keyint=60:pass=1') == ['pass=1']


@pytest.mark.parametrize('text', ['-vsync 0', '-init_hw_device vaapi=x:/dev/sda'])
def test_options_outside_the_grammar_stay_rejected(text):
    with pytest.raises(ValueError):
        parse_additional_options(text)


@pytest.mark.parametrize('text', [
    '-vf scale=iw/2:-2', '-vf fps=30000/1001', '-vf setpts=PTS/2', '-vf eq=brightness=0.1',
    '-vf scale=iw/2:-2,setpts=PTS/1.5,fps=30000/1001',
    '-vf "drawtext=text=\'Hello, world\':fontsize=20:x=10:y=10"',
    '-vf yadif,hqdn3d,unsharp,eq=contrast=1.1', '-vf hwupload,scale_vaapi=w=1280:h=720',
    '-filter_complex [0:v]scale=640:-2[v]', '-vf curves=preset=vintage', '-vf paletteuse',
    '-af loudnorm=I=-16:TP=-1.5,aresample=48000', '-hwaccel vaapi',
    '-vf scale=640:-2,hflip,drawtext=text=file -af highpass=f=200,volume=2',
    '-svtav1-params film-grain=8:tune=0', '-x264-params profile=high',
    '-x264-params keyint=60:bframes=3:no-scenecut=1 -x265-params aq-mode=3:psy-rd=2.0',
])
def test_ordinary_options_do_not_ask(text):
    parse_additional_options(text)
    assert file_access_settings(text) == []


@pytest.fixture
def settings(tmp_path):
    return SettingsManager('test', dev_mode=True, dev_settings_file=str(tmp_path/'settings.json'))


def write_profile(tmp_path, **values):
    path = tmp_path/'profile.json'
    path.write_text(json.dumps({'_app': 'big-video-converter', '_profile_version': 1, **values}))
    return path


def test_export_import_roundtrip_without_local_or_destructive_options(settings, tmp_path):
    settings.settings.update({'delete-original': True,
        'gpu-device-index': 5, 'video-codec': 'h265', 'preview-hue': .5})
    path = tmp_path/'profile.json'
    assert settings.export_profile(str(path))
    data = json.loads(path.read_text())
    for key in ['delete-original', 'gpu-device-index', 'preview-hue']:
        assert key not in data
    other = SettingsManager('test', True, str(tmp_path/'other.json'))
    assert other.import_profile(str(path))
    assert other.get_string('video-codec') == 'h265'
    assert not other.get_boolean('delete-original')


def test_legacy_profile_cannot_enable_deletion(settings, tmp_path):
    path = write_profile(tmp_path, **{'delete-original': True, 'gpu-device-index': 8,
                                    'video-codec': 'h265'})
    assert settings.import_profile(str(path))
    assert not settings.get_boolean('delete-original')
    assert settings.get_value('gpu-device-index') == 0
    assert settings.get_string('video-codec') == 'h265'


def test_import_ignores_a_setting_this_build_does_not_know(settings, tmp_path):
    """A profile from a newer build still imports the keys both versions share."""
    path = write_profile(tmp_path, **{'audio-codec': 'opus', 'future-setting': 1})
    assert settings.import_profile(str(path))
    assert settings.get_string('audio-codec') == 'opus'
    assert 'future-setting' not in settings.settings


def test_profile_that_reaches_files_is_read_before_anything_is_committed(settings, tmp_path, monkeypatch):
    """The GUI asks between read_profile and apply_profile; Cancel leaves no trace."""
    options = '-vf vidstabdetect=result=.bashrc'
    before = settings.settings.copy()
    updates = settings.read_profile(str(write_profile(tmp_path, **{'additional-options': options})))
    assert file_access_settings(updates['additional-options']) == ['vidstabdetect=result=.bashrc']
    assert settings.settings == before and not Path(settings.settings_file).exists()
    assert settings.apply_profile(updates)
    assert settings.get_string('additional-options') == options
    monkeypatch.setattr(settings, 'save_to_disk', lambda: pytest.fail('unchanged profile was written'))
    assert settings.apply_profile(updates)


def test_filters_refused_by_older_builds_are_accepted_again(tmp_path):
    options = '-vf subtitles=subs.srt,lut3d=file=/abs/x.cube -af ass=a.ass'
    path = tmp_path/'settings.json'
    path.write_text(json.dumps({'additional-options': options}))
    settings = SettingsManager('test', True, str(path))
    assert settings.get_string('additional-options') == options
    assert validate_additional_options(options)[0]


def test_import_maps_a_noise_model_the_plugin_lacks_to_the_default(settings, tmp_path):
    """Older builds accepted model 2; there are only models 0 and 1."""
    assert settings.import_profile(str(write_profile(tmp_path, **{'noise-model': 2})))
    assert settings.get_value('noise-model') == 0


@pytest.mark.parametrize('key,value', [
    ('video-codec', 'unknown'), ('output-format-index', 99), ('output-format-index', True),
    ('noise-reduction', 'false'), ('noise-reduction-strength', math.nan),
    ('noise-model', -1), ('hpf-frequency', 0), ('audio-channels', '2;rm'),
    ('audio-channels', '0'), ('audio-bitrate', 'anything'), ('video-resolution', '123x0'),
    ('eq-bands', '0,0'), ('eq-bands', '0,0,0,0,0,0,0,0,0,nan'),
    ('additional-options', 'extra-output.mp4'),
    ('_profile_version', 2), ('_profile_version', True), ('_app', 'other'),
])
def test_import_invalid_is_transactional(settings, tmp_path, key, value):
    settings.set_value('video-codec', 'h264')
    before_disk = Path(settings.settings_file).read_bytes()
    before_memory = settings.settings.copy()
    path = write_profile(tmp_path, **{'audio-codec': 'opus', key: value})
    assert not settings.import_profile(str(path))
    assert settings.settings == before_memory
    assert Path(settings.settings_file).read_bytes() == before_disk


def test_failed_persistence_rolls_back_import(settings, tmp_path, monkeypatch):
    before = settings.settings.copy()
    monkeypatch.setattr(settings, 'save_to_disk', lambda: False)
    assert not settings.import_profile(str(write_profile(tmp_path, **{'video-codec': 'h265'})))
    assert settings.settings == before


def test_corrupt_primary_does_not_destroy_known_good_backup(tmp_path):
    path = tmp_path/'settings.json'
    path.write_text('{invalid')
    backup = path.with_suffix('.json.bak')
    backup.write_text('{"video-codec":"h265"}')
    settings = SettingsManager('test', True, str(path))
    assert settings.get_string('video-codec') == 'h265'
    assert json.loads(backup.read_text()) == {'video-codec':'h265'}
    assert json.loads(path.read_text()) == {'video-codec':'h265'}


def test_nested_suspension_and_batch_rollback(settings):
    with settings.suspend_writes():
        with settings.suspend_writes():
            settings.set_value('video-codec', 'h265')
        settings.set_value('audio-codec', 'opus')
    assert settings.settings == {}
    with pytest.raises(RuntimeError), settings.batch_update():
        settings.set_value('video-codec', 'h265')
        raise RuntimeError('abort')
    assert settings.settings == {}
    with settings.batch_update():
        settings.set_value('video-codec', 'h265')
        with settings.batch_update():
            settings.set_value('audio-codec', 'opus')
        assert not Path(settings.settings_file).exists()
    assert json.loads(Path(settings.settings_file).read_text())['audio-codec'] == 'opus'


@pytest.mark.parametrize('old,new', [
    ('-vsync 0 -threads 2', '-fps_mode passthrough -threads 2'),
    ('-vsync 1', '-fps_mode cfr'), ('-vsync vfr', '-fps_mode vfr'), ('-vsync -1', '-fps_mode auto'),
])
def test_old_vsync_option_is_read_as_fps_mode(tmp_path, old, new):
    """FFmpeg 7 removed -vsync, so the options grammar refuses it."""
    path = tmp_path/'settings.json'
    path.write_text(json.dumps({'additional-options': old}))
    settings = SettingsManager('test', True, str(path))
    assert settings.get_string('additional-options') == new
    parse_additional_options(new)


def test_out_of_range_number_falls_back_to_the_default(tmp_path):
    path = tmp_path/'settings.json'
    path.write_text('{"window-width": 1e400}')
    settings = SettingsManager('test', True, str(path))
    assert settings.get_value('window-width') == 1200


def test_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    settings = SettingsManager('test')
    assert settings.settings_file == str(tmp_path/'big-video-converter/settings.json')
