"""Native GTK/libadwaita regressions; run under dbus-run-session + Xvfb.

These tests create real widgets and open the existing application. They do not
claim physical-GPU, screen-reader usability or AI noise-reduction audio-quality coverage.
"""
import importlib
import os
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')),
    reason='Requires an X11 or Wayland display')
gi = pytest.importorskip('gi')
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from conftest import CLI
from gi.repository import Adw, GLib, Gtk
from utils.signal_connections import SignalConnections


def pump(seconds=.1):
    context=GLib.MainContext.default()
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        for _ in range(100):
            if not context.pending():break
            context.iteration(False)
        time.sleep(.005)


def until(predicate,timeout=10):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        pump(.02)
        if predicate():return
    raise AssertionError('GTK condition timed out')


def widgets(parent):
    yield parent
    child=parent.get_first_child()
    while child is not None:
        yield from widgets(child)
        child=child.get_next_sibling()


@pytest.fixture(scope='module')
def app(tmp_path_factory):
    home=tmp_path_factory.mktemp('gui-home')
    old={k:os.environ.get(k) for k in ('HOME','XDG_CONFIG_HOME','XDG_DATA_HOME')}
    os.environ.update(HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_DATA_HOME=str(home/'data'))
    import constants
    constants.CONVERT_SCRIPT_PATH=str(CLI)
    from main import VideoConverterApp
    application=VideoConverterApp()
    application.settings_manager.save_setting('show-welcome-dialog',False)
    application.register(None);application.activate();pump(.5)
    yield application
    if application.video_edit_page:
        application.video_edit_page.cleanup()
    application.window.destroy();application.quit();pump(.1)
    for k,v in old.items():
        if v is None:os.environ.pop(k,None)
        else:os.environ[k]=v


def test_real_application_opens_without_redesign(app):
    assert app.window.get_title()=='Big Video Converter'
    assert app.window.get_width()>500 and app.window.get_height()>400
    assert isinstance(app.output_format_combo,Adw.ComboRow)
    assert app.conversion_page and app.progress_page and app.video_edit_page


def test_editor_restores_individual_crop_and_contrast_without_changing_other_video(app, monkeypatch):
    from copy import deepcopy

    editor = app.video_edit_page
    values = {
        'first.mp4': {'crop_left': 28, 'crop_right': 28, 'crop_top': 0,
                      'crop_bottom': 0, 'crop_aspect': '1:1', 'contrast': 0.5},
        'second.mp4': {'crop_left': 2, 'crop_right': 4, 'crop_top': 6,
                       'crop_bottom': 8, 'crop_aspect': 'free', 'contrast': -0.2},
    }
    monkeypatch.setattr(app.conversion_page, 'file_metadata', deepcopy(values))
    monkeypatch.setattr(editor, 'mpv_player', None)
    monkeypatch.setattr(editor, 'current_video_path', None)
    for path in ('first.mp4', 'second.mp4', 'first.mp4'):
        editor.current_video_path = path
        editor._load_file_metadata(path)
        assert editor.crop_left == values[path]['crop_left']
        assert editor.crop_right == values[path]['crop_right']
        assert editor.crop_aspect == values[path]['crop_aspect']
        assert editor.contrast == values[path]['contrast']
    assert app.conversion_page.file_metadata == values


def test_crop_choices_apply_to_rotated_odd_and_portrait_sources(app, monkeypatch):
    editor = app.video_edit_page
    monkeypatch.setattr(editor, 'mpv_player', None)
    monkeypatch.setattr(editor, 'current_video_path', 'crop-test.mp4')
    monkeypatch.setattr(app.conversion_page, 'file_metadata', {})
    for width, height in ((127, 73), (72, 128)):
        monkeypatch.setattr(editor, 'video_width', width)
        monkeypatch.setattr(editor, 'video_height', height)
        for rotation in (0, 90, 270):
            monkeypatch.setattr(editor, 'rotation', rotation)
            model = editor.ui.crop_aspect_combo.get_model()
            for index in range(2, model.get_n_items()):
                label = model.get_string(index)
                editor.ui.crop_aspect_combo.set_selected(index)
                editor.on_crop_aspect_changed(editor.ui.crop_aspect_combo)
                w = width - editor.crop_left - editor.crop_right
                h = height - editor.crop_top - editor.crop_bottom
                assert w > 0 and h > 0 and w % 2 == h % 2 == 0
                assert 0 <= editor.crop_left <= editor.crop_right + 1
                assert 0 <= editor.crop_top <= editor.crop_bottom + 1
                if rotation in (90, 270):
                    w, h = h, w
                numerator, denominator = map(float, label.split(':'))
                assert abs(w / h - numerator / denominator) <= 4 / h
                assert app.conversion_page.file_metadata['crop-test.mp4']['crop_aspect'] == label
    editor.ui.crop_aspect_combo.set_selected(1)
    assert (editor.crop_left, editor.crop_right, editor.crop_top, editor.crop_bottom) == (0, 0, 0, 0)


def test_replacing_queue_cancels_pending_row_creation(app, media, tmp_path, monkeypatch):
    from collections import deque
    paths=[]
    for index in range(30):
        path=tmp_path / f'queue-{index}.mp4'
        os.link(media['video'], path)
        paths.append(str(path))
    monkeypatch.setattr(app, 'conversion_queue', deque(paths))
    page=app.conversion_page
    page.update_queue_display()
    assert 0 < len(page.queue_rows) < len(paths)
    app.conversion_queue.clear()
    app.conversion_queue.append(paths[-1])
    page.update_queue_display()
    pump(.15)
    assert [row.file_path for row in page.queue_rows] == [paths[-1]]
    assert page._queue_render_id is None


def test_close_confirmation_preserves_running_job_when_declined(app, monkeypatch):
    monkeypatch.setattr(app, 'active_conversions', [{'job_id': 'unfinished'}])
    stopped = []
    monkeypatch.setattr(app, 'quit', lambda: stopped.append(True))
    app._on_window_close_request(app.window)
    next(w for w in widgets(app._close_dialog)
         if isinstance(w, Gtk.Button) and w.get_label() == 'Keep converting').emit('clicked')
    assert not stopped
    app._on_window_close_request(app.window)
    next(w for w in widgets(app._close_dialog)
         if isinstance(w, Gtk.Button) and w.get_label() == 'Stop and close').emit('clicked')
    assert stopped == [True]


@pytest.mark.parametrize('busy', ['currently_converting','active_conversions','conversions_running','_pending_imports','_quitting'])
def test_view_changes_cannot_reenable_conversion_while_busy(app, monkeypatch, busy):
    with monkeypatch.context() as patch:
        patch.setattr(app, busy, True, raising=False)
        app.header_bar.set_view('editor')
        assert not app.header_bar.convert_current_button.get_sensitive()
        app.header_bar.set_buttons_sensitive(True)
        assert not app.header_bar.convert_current_button.get_sensitive()
        app.header_bar.set_view('queue')
        app.header_bar.update_queue_size(2)
        assert not app.header_bar.convert_button.get_sensitive()
    app.header_bar.set_view('queue')


def test_individual_options_dialog_applies_and_restores_inheritance(app, media, monkeypatch):
    from copy import deepcopy

    from utils.job_options import RESOLUTION_MODES
    from utils.presets import list_presets

    page=app.conversion_page
    source=str(media['video'])
    metadata={source: {'contrast': .25, 'crop_left': 2}, 'other.mp4': {'contrast': -.2}}
    monkeypatch.setattr(page, 'file_metadata', deepcopy(metadata))
    general=deepcopy(app.settings_manager.settings)
    page.on_file_options_by_path(source)
    dialog=app.window.get_visible_dialog()
    profile=next(w for w in widgets(dialog) if isinstance(w, Adw.ComboRow) and w.get_title()=='Profile')
    resolution=next(w for w in widgets(dialog) if isinstance(w, Adw.ComboRow) and w.get_title()=='Resolution')
    profile.set_selected(1)
    resolution.set_selected(RESOLUTION_MODES.index('custom'))
    for label,value in (('Width',641),('Height',361)):
        row=next(w for w in widgets(dialog) if isinstance(w, Adw.ActionRow) and w.get_title()==label)
        row.get_activatable_widget().set_value(value)
    next(w for w in widgets(dialog) if isinstance(w, Gtk.Button) and w.get_label()=='Apply to this video').emit('clicked')
    pump(.1)
    chosen=page.file_metadata[source]
    assert chosen['preset_snapshot']['id']==list_presets()[0].id
    assert (chosen['custom_width'],chosen['custom_height'])==(640,360)
    assert chosen['contrast']==.25 and chosen['crop_left']==2
    assert page.file_metadata['other.mp4']==metadata['other.mp4']
    assert app.settings_manager.settings==general
    page.on_file_options_by_path(source)
    dialog=app.window.get_visible_dialog()
    rows=[w for w in widgets(dialog) if isinstance(w,Adw.ComboRow)]
    profile=next(w for w in rows if w.get_title()=='Profile')
    resolution=next(w for w in rows if w.get_title()=='Resolution')
    assert profile.get_selected()==1 and resolution.get_selected()==RESOLUTION_MODES.index('custom')
    profile.set_selected(0)
    resolution.set_selected(0)
    next(w for w in widgets(dialog) if isinstance(w, Gtk.Button) and w.get_label()=='Apply to this video').emit('clicked')
    pump(.1)
    chosen=page.file_metadata[source]
    assert chosen['preset_snapshot'] is None and chosen['resolution_mode']=='global'
    assert chosen['contrast']==.25 and chosen['crop_left']==2
    assert app.settings_manager.settings==general


@pytest.mark.parametrize("mode", ["single", "split", "join"])
def test_individual_recipe_reaches_real_conversion(app, monkeypatch, media, tmp_path, request, mode):
    from copy import deepcopy

    from test_presets import MINIMAL
    from test_supervisor import App as SupervisorApp
    from utils.conversion import run_with_progress_dialog
    from utils.job_options import snapshot_preset
    from utils.media_validation import probe_media
    from utils.presets import load_preset

    preset_path = tmp_path / 'individual.toml'
    preset_path.write_text(MINIMAL.replace('fps = 10', 'fps = 10\nresolution = "96x54"\ngpu = "software"'))
    snapshot = snapshot_preset(load_preset(str(preset_path)))
    # A saved per-video choice survives removal or editing of the original TOML.
    preset_path.unlink()
    general = {
        'video-codec': 'h264', 'video-resolution': '64x36', 'gpu': 'software',
        'force-copy-video': False, 'preset': 'ultrafast', 'audio-handling': 'copy',
        'output-format-index': 0, 'additional-options': '-threads 1 -filter_threads 1',
    }
    monkeypatch.setattr(app.settings_manager, 'settings', general.copy())
    monkeypatch.setattr(app, 'active_preset', lambda: None)
    page = app.conversion_page
    old_folder_mode = page.folder_combo.get_selected()
    old_folder = page.output_folder_entry.get_text()
    old_delete = page.delete_original_check.get_active()
    def restore_controls():
        page.folder_combo.set_selected(old_folder_mode)
        page.output_folder_entry.set_text(old_folder)
        page.delete_original_check.set_active(old_delete)
    request.addfinalizer(restore_controls)
    source = str(media['video'])
    segments = {} if mode == 'single' else {
        'output_mode': mode, 'trim_segments': [{'start': 0.2, 'end': 1.0}, {'start': 1.5, 'end': 2.5}],
    }
    monkeypatch.setattr(page, 'current_file_path', source, raising=False)
    monkeypatch.setattr(page, 'file_metadata', {source: {
        **segments, 'preset_snapshot': snapshot, 'resolution_mode': 'original',
    }})
    page.folder_combo.set_selected(1)
    page.output_folder_entry.set_text(str(tmp_path))
    page.delete_original_check.set_active(False)
    saved = deepcopy(app.settings_manager.settings)
    contexts = []
    monkeypatch.setattr(page, '_continue_conversion', lambda context: contexts.append(context) or True)
    assert page.force_start_conversion(gpu_override={'type': 'nvidia', 'device': '/dev/missing'})
    individual = contexts[-1]
    assert individual['env_vars']['gpu'] == 'software'
    assert individual['output_ext'] == '.mkv'
    assert individual['env_vars']['video_resolution'] == ''
    assert individual['env_vars']['audio_handling'] == 'reencode'
    page.file_metadata[source] = segments.copy()
    assert page.force_start_conversion()
    inherited = contexts[-1]
    assert inherited['output_ext'] == '.mp4'
    assert inherited['env_vars']['video_resolution'] == '64x36'
    assert inherited['env_vars']['audio_handling'] == 'copy'
    assert app.settings_manager.settings == saved
    edited_folder = tmp_path / 'edited'
    edited_folder.mkdir()
    page.output_folder_entry.set_text(str(edited_folder))
    app.settings_manager.settings['force-copy-video'] = True
    page.file_metadata[source] = {
        **segments, 'resolution_mode': 'original', 'contrast': -1.0, 'saturation': 0.0,
        'crop_aspect': '1:1', 'crop_left': 28, 'crop_right': 28,
    }
    assert page.force_start_conversion()
    app.settings_manager.settings['video-resolution'] = '32x18'
    for index, context in enumerate(contexts):
        supervisor = SupervisorApp()
        if mode == 'single':
            run_with_progress_dialog(
                supervisor, context['cmd'], 'individual recipe', source, False,
                context['env_vars'], preset_source=context['preset_source'], job_id=str(index),
            )
        else:
            from utils.segment_batch import start_segment_batch
            batch_page = SimpleNamespace(app=supervisor, _format_time_ffmpeg=page._format_time_ffmpeg)
            assert start_segment_batch(batch_page, context)
        until(lambda supervisor=supervisor: bool(supervisor.notifications), timeout=40)
        assert supervisor.notifications[0][0], supervisor.progress_page.rows[0].lines
        outputs = ([context['full_output_path']] if mode == 'single' else
                   supervisor.completed_conversions[-1]['output_files'])
        assert len(outputs) == (2 if mode == 'split' else 1)
        for output in outputs:
            streams = probe_media(output)['streams']
            video = next(s for s in streams if s['codec_type'] == 'video')
            audio = next(s for s in streams if s['codec_type'] == 'audio')
            assert (video['width'], video['height']) == [(128, 72), (64, 36), (72, 72)][index]
            if index == 0:
                assert video['r_frame_rate'] == '10/1'
                assert audio['sample_rate'] == '22050' and audio['channels'] == 1
            if index == 2:
                import subprocess

                import numpy as np
                deviations = []
                for path in (source, output):
                    frame = subprocess.run(
                        ['ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1',
                         '-pix_fmt', 'gray', '-f', 'rawvideo', '-'],
                        check=True, capture_output=True, timeout=10,
                    ).stdout
                    deviations.append(np.frombuffer(frame, dtype=np.uint8).std())
                assert deviations[0] > 20
                assert deviations[1] < 2


@pytest.mark.parametrize('module,entry',[
    ('audio_dialog','show_audio_dialog'),('video_encoding_dialog','show_video_encoding_dialog'),
    ('extra_dialog','show_extra_dialog'),('noise_dialog','show_noise_dialog'),
    ('subtitles_dialog','show_subtitles_dialog')])
def test_dialog_disconnects_all_long_lived_subscriptions(app,monkeypatch,module,entry):
    opened=[]
    original=SignalConnections.__init__
    def capture(self,dialog):
        original(self,dialog);opened.append((self,dialog))
    monkeypatch.setattr(SignalConnections,'__init__',capture)
    show=getattr(importlib.import_module('ui.'+module),entry)
    for _ in range(3):
        show(app.window,app);pump(.04)
        tracker,dialog=opened[-1]
        saved=list(tracker._handlers)
        assert saved and all(emitter.handler_is_connected(i) for emitter,i in saved)
        dialog.force_close();until(lambda tracker=tracker:tracker._closed)
        assert not tracker._handlers
        assert all(not emitter.handler_is_connected(i) for emitter,i in saved)


def test_first_manual_eq_change_preserves_other_nine_bands(app,monkeypatch):
    from ui.noise_dialog import show_noise_dialog
    dialogs=[]
    original=SignalConnections.__init__
    def capture(self,dialog):original(self,dialog);dialogs.append(dialog)
    monkeypatch.setattr(SignalConnections,'__init__',capture)
    app.eq_preset_row.set_selected(1)
    app.settings_manager.save_setting('eq-bands','3,3,3,3,3,3,3,3,3,3')
    show_noise_dialog(app.window,app);pump(.05)
    dialog=dialogs[-1]
    bands=[w for w in widgets(dialog) if isinstance(w,Gtk.Scale)
           and w.get_orientation()==Gtk.Orientation.VERTICAL]
    assert len(bands)==10
    before=[w.get_value() for w in bands]
    bands[0].set_value(before[0]+1)
    assert [w.get_value() for w in bands]==[before[0]+1,*before[1:]]
    assert app.eq_preset_row.get_selected()==9
    assert list(map(float,app.settings_manager.load_setting('eq-bands').split(',')))==[before[0]+1,*before[1:]]
    dialog.force_close();pump(.03)


def test_editor_saves_adjustment_before_next_main_loop_tick(app,media):
    page=app.video_edit_page
    page.current_video_path=str(media['video'])
    page.brightness=.1;page.saturation=1;page.hue=0
    page.ui.hue_scale.set_value(.75)
    # Do not pump: persistence cannot depend on a pending timer.
    assert page.app_state.file_metadata[str(media['video'])]['hue']==.75


def test_remove_only_selected_segment_with_duplicate_start(app):
    page=app.video_edit_page
    first={'start':0,'end':1};second={'start':0,'end':2}
    page.trim_segments=[first,second]
    page._on_remove_segment_clicked(None,first)
    assert len(page.trim_segments)==1 and page.trim_segments[0] is second


def test_stale_editor_metadata_and_error_are_ignored(app,monkeypatch):
    processor=app.video_edit_page.processor
    errors=[];monkeypatch.setattr(app,'show_error_dialog',lambda *a:errors.append(a))
    previous=processor._generation
    processor.invalidate()
    assert processor._on_video_info_loaded({},'old-file',previous) is False
    assert processor._on_video_info_error('late error',previous) is False
    assert not errors


def test_mpv_colors_are_sent_coalesced_and_cached_only_after_success():
    from ui.mpv_player import MPVPlayer
    player=MPVPlayer.__new__(MPVPlayer)
    player.cached_hue=0;player.cached_saturation=0;player.cached_brightness=0
    player._pending_colors={};player._color_flush_id=None
    sent=[]
    player.mpv_instance=SimpleNamespace(command_async=lambda *args:sent.append(args))
    # A drag: many values, one write of the last one when the timer fires.
    for value in (.1,.2,.3,1):
        player.set_hue(value)
    assert sent==[] and player._color_flush_id is not None
    GLib.source_remove(player._color_flush_id);player._flush_colors()
    assert sent==[('set','hue','100')] and player.cached_hue==100
    def refuse(*_args):raise RuntimeError('injected property failure')
    player.mpv_instance=SimpleNamespace(command_async=refuse)
    player.set_hue(-.5);GLib.source_remove(player._color_flush_id);player._flush_colors()
    assert player.cached_hue==100


def test_seek_waits_for_a_file_and_position_reads_playback_time():
    """Writing time-pos before a file loaded froze it at 0 for the next video."""
    from ui.mpv_player import MPVPlayer
    player=MPVPlayer.__new__(MPVPlayer)
    sent=[]
    player.mpv_instance=SimpleNamespace(command_async=lambda *args:sent.append(args),
                                        playback_time=12.5,time_pos=0.0)
    player.current_file=None
    assert not player.seek(0) and sent==[]
    player.current_file='/video.mp4'
    assert player.seek(3.25) and sent==[('seek','3.250000','absolute+exact')]
    assert player.get_position()==12.5


def test_crop_is_reapplied_after_preview_filter_clear():
    from ui.mpv_player import MPVPlayer
    player=MPVPlayer.__new__(MPVPlayer)
    class Properties(dict):
        width=128
        height=72
    player.mpv_instance=Properties(existing=True)
    player.crop_left=10;player.crop_right=10;player.crop_top=2;player.crop_bottom=2
    player._crop_applied=False
    # __new__ bypasses initialization: provide the fields used by the real
    # deferred callback rather than leaking an incomplete double to GLib.
    rendered=[]
    player.render_context=object()
    player.video_widget=SimpleNamespace(queue_render=lambda:rendered.append(True))
    player.set_crop(10,10,2,2)
    assert player.mpv_instance['video-crop']=='108x68+10+2'
    assert player._crop_applied
    until(lambda:bool(rendered))
    pump(.12)
    assert rendered==[True]


def test_native_progress_row_updates_and_completes(app,media,tmp_path,cli_env):
    from utils.conversion import run_with_progress_dialog
    completed=[]
    original=app.conversion_completed
    app.conversion_completed=lambda ok,**kwargs:completed.append((ok,kwargs))
    try:
        out=tmp_path/'native-progress.mp4'
        run_with_progress_dialog(app,[str(CLI),str(media['video'])], 'native test',
            str(media['video']),False,{**cli_env,'output_file':str(out)},job_id='native')
        until(lambda:bool(completed),timeout=15)
        assert completed==[(True,{'file_path':str(media['video']),'job_id':'native'})]
        assert out.exists() and app.conversions_running==0
    finally:app.conversion_completed=original


def test_queue_probes_off_the_main_thread_and_converts(app,media,tmp_path,monkeypatch):
    """The real queue: ffprobe pre-flight on a worker, launch and completion on the main loop."""
    import shutil
    import threading

    from utils import file_info
    probe_threads=[]
    original=file_info.warm_probe_cache
    def record(path):probe_threads.append(threading.current_thread().name);original(path)
    monkeypatch.setattr(file_info,'warm_probe_cache',record)
    source=tmp_path/'queued.mkv';shutil.copyfile(media['silent'],source)
    app.settings_manager.save_setting('gpu','software')
    app.settings_manager.save_setting('use-custom-output-folder',False)
    assert app.add_file_to_queue(str(source))
    import gc
    gc.collect()
    until(lambda: app.conversion_page.queue_rows[0].thumbnail_stack.get_visible_child_name() == 'picture')
    app.start_queue_processing()
    until(lambda:probe_threads,timeout=10)
    assert probe_threads and 'MainThread' not in probe_threads
    until(lambda:not app.active_conversions and not app.conversion_queue and not app.currently_converting,timeout=90)
    assert (tmp_path/'queued.mp4').exists()
    assert app.header_bar.convert_button.get_sensitive()


def test_presets_dialog_applies_searches_and_releases(app,tmp_path,monkeypatch):
    """The grid, its search, applying a preset and leaving it by hand."""
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'config'))
    from ui.presets_dialog import (
        build_prompt,
        show_ai_preset_dialog,
        show_presets_dialog,
    )
    dialog=show_presets_dialog(app.window,app);pump(.2)
    ids=[p.id for p in dialog.presets]
    assert 'whatsapp' in ids and 'youtube-1080p' in ids
    dialog.search.set_text('youtube');pump(.05)
    assert all('youtube' in p.id for p in dialog.visible_presets()) and dialog.visible_presets()
    dialog.apply(dialog.presets[ids.index('whatsapp')]);pump(.2)
    assert app.settings_manager.load_setting('active-preset')=='whatsapp'
    assert app.video_codec_combo.get_selected()==1
    assert app.settings_manager.load_setting('video-resolution')=='1280x720'
    assert app.settings_manager.load_setting('audio-bitrate')=='96k'
    assert app._radio_preset.get_active() and 'WhatsApp' in app._presets_row.get_subtitle()
    assert app.conversion_page.app.active_preset().id=='whatsapp'
    app.video_codec_combo.set_selected(2);pump(.1)
    assert app.settings_manager.load_setting('active-preset')==''
    assert app._radio_custom.get_active() and not app._radio_preset.get_active()
    ai=show_ai_preset_dialog(dialog.dialog,app);pump(.1)
    ai.request.get_buffer().set_text('TikTok vertical')
    assert 'TikTok vertical' in build_prompt(ai.request_text())
    saved=ai.save('```toml\nformat = 1\n[preset]\nname = "AI made"\n[video]\ncodec = "av1"\nquality = "high"\n[container]\nformat = "mkv"\n```');pump(.2)
    assert saved is not None and saved.path.startswith(str(tmp_path))
    assert app.settings_manager.load_setting('active-preset')=='ai-made'
    assert app.video_codec_combo.get_selected()==3 and app.settings_manager.load_setting('output-format-index')==1
    assert ai.save('[preset]\nname="x"\n[video]\ncodec="zzz"') is None and 'codec' in ai.status.get_text()
    ai.dialog.force_close();dialog.dialog.force_close();pump(.05)
    app.settings_manager.save_setting('active-preset','');app._apply_profile('universal')


def test_audio_mode_changes_preserve_noise_cleaning_preference(app):
    original_mode = app.audio_handling_combo.get_selected()
    original_noise = app.noise_reduction_switch.get_active()
    try:
        app.audio_handling_combo.set_selected(1)
        app.noise_reduction_switch.set_active(True)
        for mode in (0, 2, 1):
            app.audio_handling_combo.set_selected(mode)
            assert app.noise_reduction_switch.get_active()
            assert app.settings_manager.get_boolean('noise-reduction', False)
            assert app._audio_cleaning_row.get_sensitive() == (mode == 1)
    finally:
        app.audio_handling_combo.set_selected(original_mode)
        app.noise_reduction_switch.set_active(original_noise)


def test_folder_import_is_async_and_redraws_once(app, tmp_path, monkeypatch):
    import threading

    import file_handler

    app.clear_queue()
    pump()
    for index in range(100):
        (tmp_path / f'{index}.mp4').touch()
    (tmp_path / 'not-video.txt').touch()
    scanned_on = []
    redraws = []
    walk = file_handler.os.walk
    update = app.conversion_page.update_queue_display

    def record_walk(*args, **kwargs):
        scanned_on.append(threading.current_thread().name)
        yield from walk(*args, **kwargs)

    def record_update():
        redraws.append(len(app.conversion_queue))
        update()

    monkeypatch.setattr(file_handler.os, 'walk', record_walk)
    monkeypatch.setattr(app.conversion_page, 'update_queue_display', record_update)
    app.add_paths_to_queue([str(tmp_path)])
    until(lambda: len(app.conversion_queue) == 100 and not app._pending_imports)
    pump()
    assert scanned_on == ['bvc-file-scan']
    assert redraws == [100]
    assert app.header_bar.convert_button.get_sensitive()
    app.clear_queue()


def test_space_check_uses_selected_destinations(app, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import queue_manager

    sources = [tmp_path / 'one', tmp_path / 'two']
    for source in sources:
        source.mkdir()
        (source / 'video.mp4').write_bytes(b'video')
    app.settings_manager.save_setting('output-folder', str(tmp_path / 'stale'))
    app.settings_manager.save_setting('use-custom-output-folder', False)
    original_stat = queue_manager.os.stat
    checked = []

    def different_volumes(path, *args, **kwargs):
        if str(path) in map(str, sources):
            return SimpleNamespace(st_dev=sources.index(Path(path)))
        return original_stat(path, *args, **kwargs)

    from pathlib import Path
    monkeypatch.setattr(queue_manager.os, 'stat', different_volumes)
    monkeypatch.setattr(queue_manager.shutil, 'disk_usage',
                        lambda path: checked.append(path) or SimpleNamespace(free=100))
    assert app._check_disk_space([str(path / 'video.mp4') for path in sources])
    assert checked == list(map(str, sources))
    app.settings_manager.save_setting('output-folder', '')


def test_selected_file_survives_unusual_suffix_and_settings_write_failure(app, tmp_path, monkeypatch):
    from gi.repository import Gio

    app.clear_queue()
    pump()
    source = tmp_path / 'selected-video-without-extension'
    source.touch()
    errors = []
    monkeypatch.setattr(app.settings_manager, 'save_to_disk', lambda: False)
    monkeypatch.setattr(app, 'show_error_dialog', errors.append)
    dialog = SimpleNamespace(open_multiple_finish=lambda result: [Gio.File.new_for_path(str(source))])
    app._on_files_selected(dialog, None)
    until(lambda: not app._pending_imports)
    pump()
    assert list(app.conversion_queue) == [str(source)]
    assert len(errors) == 1
    assert app.header_bar.add_button.get_sensitive()
    app.clear_queue()


def test_presets_row_opens_one_dialog_from_another_profile(app,monkeypatch):
    """Clicking the row both ticks its radio and activates it: one dialog, not two."""
    from ui import presets_dialog
    opened=[]
    original=presets_dialog.PresetsDialog.present
    monkeypatch.setattr(presets_dialog.PresetsDialog,'present',
        lambda self:(opened.append(self),original(self)))
    app.settings_manager.save_setting('active-preset','')
    app._radio_universal.set_active(True);pump()
    app._presets_row.activate();pump(.2)
    try:
        assert len(opened)==1
    finally:
        for dialog in opened:
            dialog.dialog.force_close()
        pump(.2)
    # Closed without choosing: the sidebar shows the profile still in use.
    assert app._radio_universal.get_active()


def test_only_the_editor_can_hide_the_sidebar(app,media):
    split=app.split_view
    toggle=app.lookup_action('toggle_sidebar')
    app.show_queue_view();pump()
    toggle.activate(None);pump()
    assert split.get_show_sidebar() and not app.header_bar.sidebar_button.get_visible()
    app.show_editor_for_file(str(media['video']));pump(.3)
    try:
        assert app.header_bar.sidebar_button.get_visible()
        toggle.activate(None);pump()
        assert not split.get_show_sidebar()
    finally:
        app.show_queue_view();pump()
    assert split.get_show_sidebar()


def test_editor_fullscreen_keeps_the_tools_reachable_and_restores_the_sidebar(app):
    editor=app.video_edit_page
    split=app.split_view
    split.set_show_sidebar(True)
    editor._enter_video_fullscreen()
    try:
        assert not split.get_show_sidebar() and editor.ui.sidebar_button.get_visible()
        editor.ui.sidebar_button.set_active(True)
        assert split.get_show_sidebar()
        editor.ui.sidebar_button.set_active(False)
    finally:
        # Leaving fullscreen from the window manager restores the same way.
        editor._on_window_fullscreened(SimpleNamespace(is_fullscreen=lambda:False),None)
    assert not editor.is_video_fullscreen and split.get_show_sidebar()
    assert not editor.ui.sidebar_button.get_visible() and editor.ui.toolbar.get_visible()


def test_frame_rate_row_shows_a_preset_rate_the_list_lacks(app):
    page = app.settings_page
    try:
        app.settings_manager.save_setting('video-fps', '30000/1001')
        page._load_settings()
        combo = page.video_fps_combo
        assert combo.get_model().get_string(combo.get_selected()) == '29.97 fps'
        app.settings_manager.save_setting('video-fps', '24')
        page._load_settings()
        assert page._fps_values[combo.get_selected()] == '24'
        combo.set_selected(0)
        assert app.settings_manager.load_setting('video-fps', None) == ''
    finally:
        app.settings_manager.save_setting('video-fps', '')


def _reserved_jobs(app, monkeypatch, *paths):
    """Jobs as process_next_in_queue reserves them, without launching anything."""
    import threading
    jobs=[{'file_path':path,'job_id':f'job-{index}','cancel_event':threading.Event(),'gpu_slot':None}
          for index,path in enumerate(paths)]
    monkeypatch.setattr(app,'active_conversions',list(jobs))
    return jobs


def _job_settings(app, monkeypatch, **overrides):
    monkeypatch.setattr(app.settings_manager,'settings',{
        **app.settings_manager.settings,'output-format-index':0,'force-copy-video':False,
        'size-target':'','additional-options':'','gpu':'software',**overrides})
    monkeypatch.setattr(app,'active_preset',lambda:None)
    monkeypatch.setattr(app,'claimed_outputs',set())


def test_parallel_jobs_reserve_distinct_output_names(app, media, tmp_path, monkeypatch, request):
    import shutil
    page=app.conversion_page
    sources=[]
    for folder,name in (('a','clip.mkv'),('b','clip.avi')):
        (tmp_path/folder).mkdir()
        shutil.copyfile(media['video'],tmp_path/folder/name)
        sources.append(str(tmp_path/folder/name))
    out=tmp_path/'out'
    out.mkdir()
    _reserved_jobs(app,monkeypatch,*sources)
    _job_settings(app,monkeypatch)
    monkeypatch.setattr(page,'file_metadata',{})
    old=(page.folder_combo.get_selected(),page.output_folder_entry.get_text())
    request.addfinalizer(lambda:(page.folder_combo.set_selected(old[0]),page.output_folder_entry.set_text(old[1])))
    page.folder_combo.set_selected(1)
    page.output_folder_entry.set_text(str(out))
    contexts=[]
    monkeypatch.setattr(page,'_continue_conversion',lambda context:contexts.append(context) or True)
    for source in sources:
        monkeypatch.setattr(page,'current_file_path',source,raising=False)
        assert page.force_start_conversion()
    outputs=[context['full_output_path'] for context in contexts]
    assert outputs==[str(out/'clip.mp4'),str(out/'clip_1.mp4')]
    assert app.claimed_outputs==set(outputs)
    monkeypatch.setattr(app,'process_next_in_queue',lambda:False)
    app.conversion_completed(True,job_id='job-0')
    assert app.claimed_outputs=={outputs[1]}


def test_cancelled_single_file_run_restores_the_queue(app, monkeypatch):
    from collections import deque
    queue=['/videos/A.mp4','/videos/B.mp4','/videos/C.mp4']
    monkeypatch.setattr(app,'conversion_queue',deque())
    monkeypatch.setattr(app,'_original_queue_before_single_conversion',list(queue),raising=False)
    monkeypatch.setattr(app,'_single_file_conversion',True,raising=False)
    monkeypatch.setattr(app,'_single_file_to_convert',queue[1],raising=False)
    monkeypatch.setattr(app,'currently_converting',True)
    monkeypatch.setattr(app,'is_cancellation_requested',True)
    _reserved_jobs(app,monkeypatch,queue[1])
    app.conversion_completed(False,job_id='job-0')
    assert list(app.conversion_queue)==queue and not app._single_file_conversion
    assert not app.currently_converting and not app.is_cancellation_requested


def test_vanished_single_file_restores_the_queue_and_says_why(app, monkeypatch, tmp_path):
    from collections import deque
    missing,other=str(tmp_path/'gone.mp4'),str(tmp_path/'other.mp4')
    monkeypatch.setattr(app,'conversion_queue',deque([missing]))
    monkeypatch.setattr(app,'_original_queue_before_single_conversion',[other,missing],raising=False)
    monkeypatch.setattr(app,'_single_file_conversion',True,raising=False)
    monkeypatch.setattr(app,'_single_file_to_convert',missing,raising=False)
    monkeypatch.setattr(app,'active_conversions',[])
    monkeypatch.setattr(app,'currently_converting',True)
    app.progress_page.initialize_queue([missing])
    try:
        app.process_next_in_queue()
        until(lambda:not app._single_file_conversion)
        assert list(app.conversion_queue)==[other,missing] and not app.currently_converting
        row=app.progress_page.queue_items[missing]
        assert row.status=='failed' and row.status_label.get_text()=='File not found'
    finally:
        app.progress_page.reset()


def test_vanished_queued_file_stays_listed_as_not_found(app, monkeypatch, tmp_path):
    from collections import deque
    missing=str(tmp_path/'moved.mp4')
    monkeypatch.setattr(app,'conversion_queue',deque([missing]))
    page=app.conversion_page
    page.update_queue_display()
    pump(.05)
    assert [row.file_path for row in page.queue_rows]==[missing]
    assert any(isinstance(w,Gtk.Label) and w.get_label()=='File not found' for w in widgets(page.queue_rows[0]))


def test_skipped_reserved_job_never_launches(app, media, monkeypatch, tmp_path):
    import shutil
    import threading
    from collections import deque

    from utils import file_info
    source=str(tmp_path/'reserved.mp4')
    shutil.copyfile(media['video'],source)
    probing=threading.Event()
    monkeypatch.setattr(file_info,'warm_probe_cache',lambda path:probing.wait(5))
    launched=[]
    monkeypatch.setattr(app.conversion_page,'force_start_conversion',lambda **kw:launched.append(kw) or True)
    monkeypatch.setattr(app,'conversion_queue',deque([source]))
    monkeypatch.setattr(app,'active_conversions',[])
    monkeypatch.setattr(app,'is_cancellation_requested',False)
    monkeypatch.setattr(app,'currently_converting',True)
    app.progress_page.initialize_queue([source])
    try:
        app.process_next_in_queue()
        assert app.active_conversions
        app.progress_page.queue_items[source].cancel()
        probing.set()
        until(lambda:not app.currently_converting)
        assert not launched and not app.active_conversions
        assert app.progress_page.queue_items[source].status=='cancelled'
    finally:
        probing.set()
        app.progress_page.reset()


def test_queue_actions_are_off_while_a_queue_runs(app, monkeypatch, tmp_path):
    from collections import deque
    names=('add_files','add_folder','add_network_file','clear_queue','start_conversion','restore_settings')
    kept=tmp_path/'kept.mp4'
    dropped=tmp_path/'dropped.mp4'
    kept.write_bytes(b'')
    dropped.write_bytes(b'')
    monkeypatch.setattr(app,'conversion_queue',deque([str(kept)]))
    with monkeypatch.context() as patch:
        patch.setattr(app,'currently_converting',True)
        app.header_bar.set_buttons_sensitive(True)
        assert not any(app.lookup_action(name).get_enabled() for name in names)
        app.clear_queue()
        assert not app.add_file_to_queue(str(dropped))
        assert list(app.conversion_queue)==[str(kept)]
    app.header_bar.set_buttons_sensitive(True)
    assert all(app.lookup_action(name).get_enabled() for name in names)


def test_invalid_additional_options_stop_the_queue_with_one_message(app, monkeypatch):
    from collections import deque
    queue=['/videos/a.mp4','/videos/b.mp4']
    _job_settings(app,monkeypatch,**{'additional-options':"-vf 'unclosed"})
    monkeypatch.setattr(app,'conversion_queue',deque(queue))
    monkeypatch.setattr(app.conversion_page,'file_metadata',{})
    errors=[]
    monkeypatch.setattr(app,'show_error_dialog',lambda *args:errors.append(args))
    monkeypatch.setattr(app,'_check_disk_space',lambda files:pytest.fail('the queue started'))
    app.start_queue_processing()
    assert len(errors)==1 and 'No closing quotation' in errors[0][1]
    assert list(app.conversion_queue)==queue and not app.currently_converting


def test_queue_drop_accepts_only_its_own_rows_and_moves_to_the_end(app, monkeypatch):
    from collections import deque
    paths=['/videos/a.mp4','/videos/b.mp4','/videos/c.mp4']
    monkeypatch.setattr(app,'conversion_queue',deque(paths))
    page=app.conversion_page
    monkeypatch.setattr(page,'dragged_row',None)
    # Text dragged from another application is not a row index.
    assert not page.on_drop_listbox(None,'0',0,10**6)
    page.dragged_row=object()
    assert not page.on_drop_listbox(None,'5',0,10**6)
    assert not page.on_drop_listbox(None,'-1',0,10**6)
    assert list(app.conversion_queue)==paths
    assert page.on_drop_listbox(None,'0',0,10**6)
    assert list(app.conversion_queue)==paths[1:]+paths[:1]


def test_closing_the_mp4_question_completes_its_job(app, media, monkeypatch):
    from utils import file_info
    page=app.conversion_page
    source=str(media['video'])
    jobs=_reserved_jobs(app,monkeypatch,source)
    _job_settings(app,monkeypatch,**{'force-copy-video':True})
    monkeypatch.setattr(page,'file_metadata',{})
    monkeypatch.setattr(page,'current_file_path',source,raising=False)
    monkeypatch.setattr(file_info,'check_mp4_compatibility',
                        lambda path:(False,[{'codec_type':'audio','codec_name':'pcm_s16le','index':1}]))
    completed=[]
    monkeypatch.setattr(app,'conversion_completed',lambda ok,**kwargs:completed.append((ok,kwargs)))
    assert page.force_start_conversion()
    until(lambda:bool(page._decision_dialogs))
    # What quit() does: the job it waits for must settle.
    page.close_decision_dialogs()
    pump(.3)
    assert completed==[(False,{'file_path':source,'job_id':'job-0'})]
    assert jobs[0]['cancel_event'].is_set() and not page._decision_dialogs


def test_job_environment_ignores_inherited_script_settings(app, media, monkeypatch):
    for name,value in (('options','-an'),('size_limit','1'),('video_filter','hflip'),
                       ('http_proxy','http://proxy.invalid:3128')):
        monkeypatch.setenv(name,value)
    page=app.conversion_page
    source=str(media['video'])
    _reserved_jobs(app,monkeypatch,source)
    _job_settings(app,monkeypatch)
    monkeypatch.setattr(page,'file_metadata',{})
    monkeypatch.setattr(page,'current_file_path',source,raising=False)
    contexts=[]
    monkeypatch.setattr(page,'_continue_conversion',lambda context:contexts.append(context) or True)
    assert page.force_start_conversion()
    env=contexts[-1]['env_vars']
    assert env['options']=='' and env['video_filter']=='' and 'size_limit' not in env
    assert env['http_proxy']=='http://proxy.invalid:3128' and env['PATH']==os.environ['PATH']


@pytest.mark.parametrize('size',[(None,None),(56,72)])
def test_crop_that_cannot_be_applied_fails_the_job(app, media, monkeypatch, size):
    from utils import file_info
    page=app.conversion_page
    source=str(media['video'])
    _reserved_jobs(app,monkeypatch,source)
    _job_settings(app,monkeypatch)
    monkeypatch.setattr(file_info,'get_video_dimensions',lambda path:size)
    monkeypatch.setattr(page,'file_metadata',{source:{'crop_left':28,'crop_right':28}})
    monkeypatch.setattr(page,'current_file_path',source,raising=False)
    errors,contexts=[],[]
    monkeypatch.setattr(app,'show_error_dialog',lambda *args:errors.append(args))
    monkeypatch.setattr(page,'_continue_conversion',lambda context:contexts.append(context) or True)
    assert page.force_start_conversion() is False
    assert not contexts and len(errors)==1 and not app.claimed_outputs


def test_size_estimate_waits_for_the_spin_and_stops_on_close(app, media, monkeypatch):
    import ui.conversion_page as module
    calls=[]
    def plan(context):
        calls.append(context)
        raise ValueError('planned')
    monkeypatch.setattr(module,'prepare_size_job',plan)
    page=app.conversion_page
    source=str(media['video'])
    monkeypatch.setattr(page,'file_metadata',{source:{}})
    page.on_file_options_by_path(source)
    dialog=app.window.get_visible_dialog()
    size_row=next(w for w in widgets(dialog) if isinstance(w,Adw.ComboRow) and w.get_title()=='Maximum size')
    size_row.set_selected(size_row.get_model().get_n_items()-1)
    spin=next(w for w in widgets(dialog) if isinstance(w,Adw.ActionRow)
              and w.get_title()=='Size in megabytes').get_activatable_widget()
    for value in range(60,66):
        spin.set_value(value)
    pump(.6)
    assert len(calls)==1
    spin.set_value(80)
    dialog.force_close()
    pump(.6)
    assert len(calls)==1


def test_second_instance_arguments_use_its_directory_and_report_uris(app, media, monkeypatch, tmp_path):
    import shutil

    from gi.repository import Gio
    shutil.copyfile(media['video'],tmp_path/'relative.mp4')
    line=Gio.ApplicationCommandLine(
        arguments=GLib.Variant('aay',[b'big-video-converter\0',b'relative.mp4\0',
                                      b'bvc-test://host/share/remote.mp4\0']),
        platform_data=GLib.Variant('a{sv}',{'cwd':GLib.Variant('ay',str(tmp_path).encode()+b'\0')}))
    opened=[]
    real_open=app.do_open
    monkeypatch.setattr(app,'do_open',lambda files,count,hint:opened.extend(files))
    monkeypatch.setattr(app,'activate',lambda:None)
    assert app.do_command_line(line)==0
    assert [f.get_path() for f in opened]==[str(tmp_path/'relative.mp4'),None]
    errors=[]
    monkeypatch.setattr(app,'show_error_dialog',lambda *args:errors.append(args))
    real_open(opened[1:]+[Gio.File.new_for_uri('bvc-test://host/other.mp4')],2,'')
    assert len(errors)==1 and 'bvc-test://host/share/remote.mp4' in errors[0][1]


def test_missing_dependencies_open_one_install_dialog(app, monkeypatch):
    import main
    created=[]
    class Dialog:
        installation_success=False
        def __init__(self,*args):created.append(self)
        def connect(self,*args):pass
        def present(self):pass
    monkeypatch.setattr(main,'InstallDependencyDialog',Dialog)
    monkeypatch.setattr(app.dependency_checker,'are_dependencies_available',lambda:False)
    monkeypatch.setattr(app.dependency_checker,'get_install_command',lambda:{'command':['true'],'display':'true'})
    monkeypatch.setattr(app,'_dependency_prompted',False,raising=False)
    try:
        app.on_activate(app)
        app.on_activate(app)
    finally:
        app.window.set_sensitive(True)
    assert len(created)==1


def test_collapsed_split_puts_the_settings_behind_a_header_toggle(app):
    split,header=app.split_view,app.header_bar
    toggle=app.lookup_action('toggle_sidebar')
    app.show_queue_view();pump()
    split.set_collapsed(True)
    try:
        assert header.sidebar_button.get_visible() and toggle.get_enabled()
        assert header.sidebar_button.get_tooltip_text()=='Show conversion settings (F9)'
        app.show_queue_view();pump()
        assert not split.get_show_sidebar()
        toggle.activate(None)
        assert split.get_show_sidebar()
    finally:
        split.set_collapsed(False)
        app.show_queue_view();pump()
    assert split.get_show_sidebar() and not header.sidebar_button.get_visible() and not toggle.get_enabled()


def test_queue_count_uses_plural_forms(app):
    header=app.header_bar
    header.update_queue_size(2)
    assert header.queue_size_label.get_text()=='2 files'
    header.update_queue_size(1)
    assert header.queue_size_label.get_text()=='1 file'
    header.update_queue_size(len(app.conversion_queue))


def test_queue_rows_show_facts_and_own_settings_only_where_set(app, media, monkeypatch, tmp_path):
    import shutil
    from collections import deque

    from ui.progress_page import format_size
    plain, custom = str(tmp_path/'plain.mp4'), str(tmp_path/'custom.mp4')
    shutil.copyfile(media['video'], plain); shutil.copyfile(media['video'], custom)
    monkeypatch.setattr(app, 'conversion_queue', deque([plain, custom]))
    page = app.conversion_page
    monkeypatch.setitem(page.file_metadata, custom, {'resolution_mode': '1280x720'})
    page.update_queue_display()
    until(lambda: len(page.queue_rows) == 2 and all(r.duration_badge.get_visible() for r in page.queue_rows))
    rows = {row.file_path: row for row in page.queue_rows}
    assert not rows[plain].recipe_label.get_visible()
    assert rows[custom].recipe_label.get_visible()
    assert rows[custom].recipe_label.get_text().startswith('Own: ')
    size = format_size(os.path.getsize(plain))
    assert rows[plain].meta_label.get_text() == f'128 × 72 · H.264 · {size}'
    assert rows[plain].duration_badge.get_text() == '0:03'
    # Adding and converting both keep the theme's accent in the header.
    header = app.header_bar
    assert header.add_button.has_css_class('suggested-action')
    assert header.convert_button.has_css_class('suggested-action')


def test_sizes_follow_the_numeric_locale_despite_mpv(monkeypatch):
    import locale

    from ui import progress_page
    monkeypatch.setenv('LC_NUMERIC', 'pt_BR.UTF-8')
    monkeypatch.setattr(progress_page, '_DECIMAL_POINT', None)
    locale.setlocale(locale.LC_NUMERIC, 'C')  # what python-mpv does on import
    if progress_page._user_decimal_point() != ',':
        pytest.skip('pt_BR locale is not installed')
    assert progress_page.format_size(10_800_000).replace('\xa0', ' ') == '10,8 MB'
    assert progress_page.format_size(53_546).replace('\xa0', ' ') == '53,5 kB'


def test_empty_queue_is_a_quiet_hint_under_the_header_action(app, monkeypatch):
    from collections import deque
    monkeypatch.setattr(app, 'conversion_queue', deque())
    app.conversion_page.update_queue_display()
    pump(.1)
    empty = app.conversion_page.placeholder
    assert isinstance(empty, Adw.StatusPage) and empty.has_css_class('card')
    # The header already offers adding files; the empty page adds no buttons.
    assert not [w for w in widgets(empty) if isinstance(w, Gtk.Button)]
    assert app.header_bar.add_button.get_visible()
    assert app.header_bar.add_button.has_css_class('suggested-action')
