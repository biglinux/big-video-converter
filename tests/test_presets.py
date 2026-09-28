"""User presets: the TOML contract, the bundled files, the script and the AI prompt."""
import os
import shlex
import subprocess

import pytest
from conftest import APP
from utils import presets
from utils.media_validation import probe_media

BUNDLED = APP / 'presets'

MINIMAL = '''
format = 1
[preset]
name = "Tiny test"
tags = ["test"]
[video]
codec = "h264"
quality = "low"
speed = "ultrafast"
fps = 10
pixel_format = "yuv420p"
[audio]
mode = "reencode"
codec = "aac"
bitrate = "48k"
channels = 1
sample_rate = 22050
[container]
format = "mkv"
[ffmpeg]
output_options = "-g 5"
[encoder.libx264]
args = ["-tune", "animation", "-crf", "35"]
[encoder.h264_nvenc]
args = ["-cq", "40"]
'''


@pytest.fixture
def user_presets(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    return tmp_path / 'config' / 'big-video-converter' / 'presets'


class TestContract:
    def test_fenced_answer_from_a_chat_bot_is_accepted(self):
        data = presets.parse_preset_text('Here you go:\n```toml\n' + MINIMAL + '```\nEnjoy!')
        preset = presets.validate_preset(data)
        assert preset.name == 'Tiny test' and preset.id == 'tiny-test'
        assert preset.encoders['libx264'] == ['-tune', 'animation', '-crf', '35']

    @pytest.mark.parametrize('text,fragment', [
        ('format = 2\n[preset]\nname="x"', 'format 2'),
        ('[preset]\nname="x"\n[extra]\na=1', 'Unknown section'),
        ('[preset]\nname="x"\n[video]\ncodec="mpeg2"', 'codec must be one of'),
        ('[preset]\nname="x"\n[video]\ncodec="h264"\nbitrate="5M"', 'unknown key'),
        ('[preset]\nname="x"\n[video]\nresolution="1920"', 'resolution'),
        ('[preset]\nname="x"\n[audio]\nchannels=0', 'channels'),
        ('[preset]\nname="x"\n[encoder.libx264]\nargs=["-i","evil.mp4"]', 'not allowed'),
        ('[preset]\nname="x"\n[encoder.libx264]\nargs=["-crf"]', 'could not be parsed'),
        ('[preset]\nname="x"\n[encoder.libx264]\nargs=["-vf","scale=1:1;rm"]', 'not allowed'),
        ('[preset]\nname="x"\n[video]\ncodec="copy"\n[encoder.libx264]\nargs=["-crf","1"]', 'copies the video'),
        ('[preset]\nname=""', 'name is required'),
        ('[preset]\nname="x"\n[ffmpeg]\noutput_options="out.mp4"', 'not allowed'),
        ('[preset]\nname="x"\n[video]\nfps=250', 'between 1 and 240'),
    ])
    def test_rejections_name_the_problem(self, text, fragment):
        with pytest.raises(presets.PresetError, match=fragment):
            presets.validate_preset(presets.parse_preset_text(text))

    def test_frame_rate_limit_matches_the_script(self):
        from utils.settings_manager import SettingsManager
        preset = presets.validate_preset(presets.parse_preset_text('[preset]\nname="x"\n[video]\nfps=240'))
        SettingsManager._validate_profile_value('video-fps', presets.preset_fps(preset))

    def test_deeply_nested_toml_is_a_preset_error(self, user_presets):
        deep = 'a = ' + '[' * 5000 + ']' * 5000
        with pytest.raises(presets.PresetError, match='TOML'):
            presets.parse_preset_text(deep)
        user_presets.mkdir(parents=True)
        (user_presets / 'deep.toml').write_text(deep)
        assert all(p.id != 'deep' for p in presets.list_presets())
        assert [path for path, _reason in presets.broken_presets()] == [str(user_presets / 'deep.toml')]

    def test_not_toml_is_reported(self):
        with pytest.raises(presets.PresetError, match='TOML'):
            presets.parse_preset_text('this is: not = toml [')

    def test_environment_and_options_follow_the_script_contract(self):
        preset = presets.validate_preset(presets.parse_preset_text(MINIMAL))
        env = presets.preset_environment(preset)
        assert env['video_encoder'] == 'h264' and env['video_quality'] == 'low' and env['preset'] == 'ultrafast'
        assert env['audio_handling'] == 'reencode' and env['audio_bitrate'] == '48k' and env['audio_channels'] == '1'
        assert env['output_format'] == 'mkv'
        # The frame rate reaches the script as video_fps, which the GUI's
        # frame rate choice also sets, so -r cannot override it behind it.
        assert shlex.split(env['preset_options']) == ['-ar', '22050', '-g', '5']
        assert env['video_fps'] == '10' and presets.preset_settings(preset)['video-fps'] == '10'
        assert presets.encoder_args(preset, 'libx264')[:2] == ['-pix_fmt', 'yuv420p']
        assert presets.encoder_args(preset, 'h264_vaapi') == []
        settings = presets.preset_settings(preset)
        assert settings['video-codec'] == 'h264' and settings['force-copy-video'] is False
        assert settings['output-format-index'] == 1 and settings['audio-channels'] == '1'

    def test_fractional_frame_rate_is_exact(self):
        preset = presets.validate_preset(presets.parse_preset_text(
            '[preset]\nname="ntsc"\n[video]\ncodec="h264"\nfps=29.97'))
        assert presets.preset_environment(preset)['video_fps'] == '30000/1001'
        assert presets.preset_settings(preset)['video-fps'] == '30000/1001'

    def test_copy_preset_maps_to_copy_mode(self):
        preset = presets.validate_preset(presets.parse_preset_text('[preset]\nname="c"\n[video]\ncodec="copy"\nresolution="640x480"'))
        assert presets.preset_environment(preset) == {'force_copy_video': '1'}
        assert presets.preset_settings(preset)['force-copy-video'] is True
        assert presets.preset_settings(preset)['video-resolution'] == ''


RISKY = MINIMAL.replace('output_options = "-g 5"',
                       'output_options = "-g 5 -vf vidstabdetect=result=.bashrc"')


class TestFileAccess:
    def test_filters_older_builds_refused_now_validate_and_are_reported(self):
        preset = presets.validate_preset(presets.parse_preset_text(
            '[preset]\nname="x"\n[ffmpeg]\ninput_options="-hwaccel vaapi"\n'
            'output_options="-vf subtitles=s.srt,lut3d=file=/abs/x.cube"\n'
            '[encoder.libx264]\nargs=["-af","ladspa=file=/tmp/x.so","-x264-params","pass=1"]'))
        assert presets.preset_file_access(preset) == [
            'subtitles=s.srt', 'lut3d=file=/abs/x.cube', 'ladspa=file=/tmp/x.so', 'pass=1']

    def test_plain_preset_reaches_nothing(self):
        assert presets.preset_file_access(presets.validate_preset(presets.parse_preset_text(MINIMAL))) == []

    def test_a_file_in_the_presets_folder_is_the_users_own(self, user_presets):
        user_presets.mkdir(parents=True)
        (user_presets / 'mine.toml').write_text(RISKY)
        assert 'mine' in {p.id for p in presets.list_presets()}


@pytest.fixture
def presets_window(user_presets, tmp_path):
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        pytest.skip('Requires an X11 or Wayland display')
    import gi
    gi.require_version('Adw', '1')
    from types import SimpleNamespace

    from gi.repository import Adw
    from ui.presets_dialog import PresetsDialog
    from utils.settings_manager import SettingsManager
    Adw.init()
    window = Adw.Window()
    window.present()
    app = SimpleNamespace(settings_manager=SettingsManager('test', True, str(tmp_path / 's.json')))
    dialog = PresetsDialog(window, app)
    dialog.present()
    yield window, dialog
    dialog.dialog.force_close()
    window.destroy()
    _pump(.1)


def _pump(seconds):
    import time

    from gi.repository import GLib
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(.005)


def _answer(window, label):
    """Press a response button of the alert on top of the window."""
    from gi.repository import Adw, Gtk
    alert = window.get_visible_dialog()
    assert isinstance(alert, Adw.AlertDialog)
    stack = [alert]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Button) and widget.get_label() == label:
            widget.emit('clicked')
            _pump(.5)
            return alert
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    raise AssertionError(f'No {label} button')


class TestImportConfirmation:
    def test_cancel_saves_nothing_and_import_anyway_saves(self, presets_window, user_presets):
        window, dialog = presets_window
        assert dialog.import_text(RISKY) is None
        _pump(.3)
        alert = window.get_visible_dialog()
        assert alert.get_heading() == 'This preset can read or write files'
        assert 'vidstabdetect=result=.bashrc' in alert.get_body()
        assert alert.get_default_response() == 'cancel'
        _answer(window, 'Cancel')
        assert window.get_visible_dialog() is dialog.dialog
        assert not list(user_presets.glob('*.toml'))
        dialog.import_text(RISKY)
        _pump(.3)
        _answer(window, 'Import anyway')
        assert [p.name for p in user_presets.glob('*.toml')] == ['tiny-test.toml']
        assert 'tiny-test' in {p.id for p in dialog.presets}

    def test_plain_preset_imports_without_asking(self, presets_window, user_presets):
        window, dialog = presets_window
        preset = dialog.import_text(MINIMAL)
        _pump(.2)
        assert preset is not None and preset.id == 'tiny-test'
        assert window.get_visible_dialog() is dialog.dialog


class TestBundled:
    def test_every_bundled_preset_is_valid_with_a_unique_id(self):
        files = sorted(BUNDLED.glob('*.toml'))
        assert len(files) >= 6
        loaded = [presets.load_preset(str(f), bundled=True) for f in files]
        assert all(presets.preset_file_access(p) == [] for p in loaded)
        ids = [p.id for p in loaded]
        assert len(set(ids)) == len(ids)
        assert {'youtube-1080p', 'instagram-reels', 'x-twitter', 'whatsapp', 'davinci-resolve'} <= set(ids)

    def test_listing_hides_a_bundled_preset_behind_a_user_file_of_the_same_id(self, user_presets):
        presets.save_user_preset('[preset]\nname="Mine"\n[video]\ncodec="vp9"', preset_id='whatsapp', replace=True)
        listed = {p.id: p for p in presets.list_presets()}
        assert listed['whatsapp'].name == 'Mine' and not listed['whatsapp'].bundled
        assert listed['youtube-1080p'].bundled


class TestUserFiles:
    def test_save_never_overwrites_and_keeps_the_text(self, user_presets):
        first = presets.save_user_preset(MINIMAL)
        second = presets.save_user_preset(MINIMAL)
        assert first.id == 'tiny-test' and second.id == 'tiny-test-2'
        assert '[encoder.libx264]' in (user_presets / 'tiny-test.toml').read_text()

    def test_fence_is_stripped_before_saving(self, user_presets):
        preset = presets.save_user_preset('```toml\n' + MINIMAL + '\n```')
        text = (user_presets / 'tiny-test.toml').read_text()
        assert '```' not in text and preset.name == 'Tiny test'

    def test_duplicate_makes_an_editable_copy_and_bundled_cannot_be_deleted(self, user_presets):
        bundled = next(p for p in presets.list_presets() if p.id == 'whatsapp')
        copy = presets.duplicate_preset(bundled)
        assert copy.name == 'WhatsApp (copy)' and not copy.bundled and copy.id == 'whatsapp-copy'
        assert copy.encoders == bundled.encoders
        with pytest.raises(presets.PresetError):
            presets.delete_user_preset(bundled)
        presets.delete_user_preset(copy)
        assert not os.path.exists(copy.path)

    def test_import_never_takes_the_name_of_a_broken_file(self, user_presets):
        user_presets.mkdir(parents=True)
        broken = user_presets / 'tiny-test.toml'
        broken.write_text('[preset]\nname = "Tiny test"\n# hand edit in progress\n[video\n')
        saved = presets.save_user_preset(MINIMAL)
        assert saved.id == 'tiny-test-2'
        assert 'hand edit in progress' in broken.read_text()

    def test_export_copies_atomically_and_onto_itself_changes_nothing(self, user_presets, tmp_path):
        preset = presets.save_user_preset(MINIMAL)
        with open(preset.path) as file:
            before = file.read()
        presets.export_preset(preset, preset.path)
        with open(preset.path) as file:
            assert file.read() == before
        target = tmp_path / 'out.toml'
        target.write_text('old')
        presets.export_preset(preset, str(target))
        assert target.read_text() == before

    def test_broken_user_file_is_reported_not_fatal(self, user_presets):
        user_presets.mkdir(parents=True)
        (user_presets / 'broken.toml').write_text('[preset]\nname = "x"\n[video]\ncodec = "nope"\n')
        assert all(p.id != 'broken' for p in presets.list_presets())
        problems = presets.broken_presets()
        assert len(problems) == 1 and 'codec' in problems[0][1]


class TestAiPrompt:
    def test_prompt_carries_request_format_language_and_machine_encoders(self, monkeypatch):
        monkeypatch.setenv('LC_ALL', 'pt_BR.UTF-8')
        prompt = presets.build_ai_prompt('Vídeo vertical para TikTok', available_encoders={'libx264', 'h264_vaapi', 'libmp3lame'},
                                         ffmpeg_version='n9.0.1')
        assert 'Reply in this language: pt_BR (Portuguese)' in prompt
        assert 'Vídeo vertical para TikTok' in prompt
        assert '[encoder.libx264]' in prompt and 'format = 1' in prompt
        assert 'available on this machine right now: h264_vaapi, libx264.' in prompt
        assert '-crf' in prompt and 'n9.0.1' in prompt

    def test_language_falls_back_to_english_for_the_c_locale(self, monkeypatch):
        for var in ('LC_ALL', 'LC_MESSAGES', 'LANG', 'LANGUAGE'):
            monkeypatch.setenv(var, 'C')
        monkeypatch.setattr(presets.locale, 'getlocale', lambda: (None, None))
        assert presets.system_language() == 'en_US (English)'


class TestScript:
    def test_preset_shapes_the_conversion(self, media, tmp_path, run_cli, user_presets):
        preset = presets.save_user_preset(MINIMAL)
        out = tmp_path / 'out.mkv'
        result = run_cli(media['video'], out, preset_file=preset.path, force_software='', video_encoder='',
                         video_quality='', preset='', audio_handling='', options='-threads 1', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Preset: ' in result.stdout
        command = next(line for line in result.stdout.splitlines() if line.startswith('Running command:'))
        assert '-pix_fmt yuv420p -tune animation -crf 35' in command, command
        assert '-cq 40' not in command, 'arguments for another encoder must not leak'
        assert '-ar 22050 -g 5' in command and 'fps=10' in command
        streams = probe_media(str(out))['streams']
        audio = next(s for s in streams if s['codec_type'] == 'audio')
        assert audio['channels'] == 1 and audio['sample_rate'] == '22050'
        assert next(s for s in streams if s['codec_type'] == 'video')['r_frame_rate'] == '10/1'

    def test_explicit_variables_win_over_the_preset(self, media, tmp_path, run_cli, user_presets):
        preset = presets.save_user_preset(MINIMAL)
        result = run_cli(media['silent'], tmp_path / 'out.mkv', preset_file=preset.path,
                         video_quality='veryhigh', options='-t 0.4 -threads 1')
        assert result.returncode == 0, result.stdout + result.stderr
        command = next(line for line in result.stdout.splitlines() if line.startswith('Running command:'))
        assert '-crf 18' in command and '-crf 35' in command
        assert command.index('-crf 18') < command.index('-crf 35'), 'preset encoder args come last and win'

    def test_invalid_preset_stops_before_ffmpeg(self, media, tmp_path, run_cli):
        bad = tmp_path / 'bad.toml'
        bad.write_text('[preset]\nname="x"\n[encoder.libx264]\nargs=["-i","/etc/passwd"]\n')
        result = run_cli(media['silent'], tmp_path / 'out.mp4', preset_file=str(bad))
        assert result.returncode == 2
        assert 'Invalid preset' in result.stderr and 'Running command' not in result.stdout

    def test_helper_cli_speaks_nul_separated(self, user_presets):
        preset = presets.save_user_preset(MINIMAL)
        env = subprocess.run(['python3', str(APP / 'utils' / 'presets.py'), '--env', preset.path],
                             capture_output=True, timeout=20, check=False).stdout
        pairs = dict(item.split(b'=', 1) for item in env.split(b'\0') if item)
        assert pairs[b'video_encoder'] == b'h264' and pairs[b'output_format'] == b'mkv'
        args = subprocess.run(['python3', str(APP / 'utils' / 'presets.py'), '--encoder-args', preset.path, 'h264_nvenc'],
                              capture_output=True, timeout=20, check=False).stdout
        assert args.split(b'\0')[:-1] == [b'-cq', b'40']


class TestTranslatableTexts:
    def test_bundled_strings_file_is_in_step_with_the_toml_files(self):
        """presets/bundled_strings.py feeds gettext; it must list every current text."""
        generated = presets.bundled_strings_source()
        assert (BUNDLED / 'bundled_strings.py').read_text() == generated
        for preset in presets.list_presets(include_bundled=True):
            if preset.bundled:
                assert repr(preset.name) in generated and repr(preset.description) in generated

    def test_user_presets_are_shown_as_written(self, user_presets):
        preset = presets.save_user_preset('[preset]\nname="Meu preset"\ndescription="Descrição"\ntags=["meu"]')
        assert preset.display_name == 'Meu preset' and preset.display_tags == ['meu']
