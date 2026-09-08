"""Native GTK/libadwaita regressions; run under dbus-run-session + Xvfb.

These tests create real widgets and open the existing application. They do not
claim physical-GPU, screen-reader usability or GTCRN audio-quality coverage.
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
from gi.repository import Adw, GLib, Gtk

from conftest import CLI
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
    application.settings_manager.save_setting('show-conversion-help-on-startup',False)
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


@pytest.mark.parametrize('module,entry',[
    ('audio_dialog','show_audio_dialog'),('video_encoding_dialog','show_video_encoding_dialog'),
    ('extra_dialog','show_extra_dialog'),('noise_dialog','show_noise_dialog'),
    ('subtitles_dialog','show_subtitles_dialog'),('video_options_dialog','show_video_options_dialog')])
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
        dialog.force_close();until(lambda:tracker._closed)
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
    assert page.metadata_save_timeout is None


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


def test_mpv_property_cache_only_changes_after_success():
    from ui.mpv_player import MPVPlayer
    player=MPVPlayer.__new__(MPVPlayer)
    player.cached_hue=0;player.cached_saturation=0;player.cached_brightness=0
    player.mpv_instance=SimpleNamespace()
    player.set_hue(1)
    assert player.mpv_instance.hue==100 and player.cached_hue==100
    player.set_hue(-1)
    assert player.mpv_instance.hue==-100
    class Refusing:
        def __setattr__(self,k,v):raise RuntimeError('injected property failure')
    player.mpv_instance=Refusing();player.set_hue(.5)
    assert player.cached_hue==-100


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
