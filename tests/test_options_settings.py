import json
import math
from pathlib import Path

import pytest

from utils.ffmpeg_options import parse_additional_options, validate_additional_options
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


@pytest.fixture
def settings(tmp_path):
    return SettingsManager('test', dev_mode=True, dev_settings_file=str(tmp_path/'settings.json'))


def write_profile(tmp_path, **values):
    path = tmp_path/'profile.json'
    path.write_text(json.dumps({'_app': 'big-video-converter', '_profile_version': 1, **values}))
    return path


def test_export_import_roundtrip_without_local_or_destructive_options(settings, tmp_path):
    settings.settings.update({'delete-original': True, 'delete-batch-originals': True,
        'gpu-device-index': 5, 'video-codec': 'h265', 'preview-hue': .5})
    path = tmp_path/'profile.json'
    assert settings.export_profile(str(path))
    data = json.loads(path.read_text())
    for key in ['delete-original', 'delete-batch-originals', 'gpu-device-index', 'preview-hue']:
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


@pytest.mark.parametrize('key,value', [
    ('video-codec', 'unknown'), ('output-format-index', 99), ('output-format-index', True),
    ('noise-reduction', 'false'), ('noise-reduction-strength', math.nan),
    ('noise-lookahead', -1), ('hpf-frequency', 0), ('audio-channels', '2;rm'),
    ('audio-channels', '0'), ('audio-bitrate', 'anything'), ('video-resolution', '123x0'),
    ('eq-bands', '0,0'), ('eq-bands', '0,0,0,0,0,0,0,0,0,nan'),
    ('additional-options', 'extra-output.mp4'), ('future-setting', 1),
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
    with pytest.raises(RuntimeError):
        with settings.batch_update():
            settings.set_value('video-codec', 'h265')
            raise RuntimeError('abort')
    assert settings.settings == {}
    with settings.batch_update():
        settings.set_value('video-codec', 'h265')
        with settings.batch_update():
            settings.set_value('audio-codec', 'opus')
        assert not Path(settings.settings_file).exists()
    assert json.loads(Path(settings.settings_file).read_text())['audio-codec'] == 'opus'


def test_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    settings = SettingsManager('test')
    assert settings.settings_file == str(tmp_path/'big-video-converter/settings.json')
