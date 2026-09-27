"""Finalization regressions; run only inside an isolated GTK session."""

import ctypes
import gc
import os

import pytest
import test_gtk
from test_gtk import pump


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    import main

    # The ordinary GTK fixture may have already exported its application in this process.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(main, "APP_ID", "br.com.biglinux.converter.ResourceTests")
        yield from test_gtk.app.__wrapped__(tmp_path_factory)


pytestmark = pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
    reason="Requires an isolated graphical session",
)

# qdata's destructor runs at finalize, unlike GObject weak notifications (dispose).
_gobject = ctypes.CDLL("libgobject-2.0.so.0")
_notify = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
_gobject.g_object_set_data_full.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_void_p,
    _notify,
]
_gobject.g_object_set_data_full.restype = None
_capsule_pointer = ctypes.pythonapi.PyCapsule_GetPointer
_capsule_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
_capsule_pointer.restype = ctypes.c_void_p
_finalized = set()


@_notify
def _finalize(serial):
    _finalized.add(serial)


def track(widget, serial):
    _finalized.discard(serial)
    _gobject.g_object_set_data_full(
        _capsule_pointer(widget.__gpointer__, None),
        b"bvc-resource-test",
        serial,
        _finalize,
    )


def test_tooltip_controller_releases_widget(app):
    from gi.repository import Gtk

    for serial in range(1, 6):
        button = Gtk.Button()
        track(button, serial)
        app.tooltip_helper.add_tooltip(button, "video-codec")
        del button
        gc.collect()
    assert set(range(1, 6)) <= _finalized


def test_welcome_dialog_finalizes_after_close(app):
    from ui.welcome_dialog import WelcomeDialog

    for serial in range(20, 25):
        welcome = WelcomeDialog(app.window, app.settings_manager)
        track(welcome.dialog, serial)
        welcome.present()
        pump(0.6)
        assert welcome.dialog.get_mapped()
        welcome.dialog.force_close()
        del welcome
        pump(0.6)
        gc.collect()
    assert set(range(20, 25)) <= _finalized


def test_info_window_finalizes_after_close(app, media):
    from utils.file_info import VideoInfoDialog

    for serial in range(10, 15):
        info = VideoInfoDialog(app.window, str(media["video"]))
        track(info.dialog, serial)
        info.show()
        pump(0.6)
        assert info.dialog.get_mapped()
        info.dialog.close()
        del info
        pump(0.6)
        gc.collect()
    assert set(range(10, 15)) <= _finalized


@pytest.mark.parametrize(
    "module,entry",
    [
        ("extra_dialog", "show_extra_dialog"),
        ("presets_dialog", "show_presets_dialog"),
        ("presets_dialog", "show_ai_preset_dialog"),
    ],
)
def test_dialog_tree_finalizes(app, module, entry):
    import importlib

    from test_gtk import widgets

    created = set()
    for cycle in range(3):
        getattr(importlib.import_module("ui." + module), entry)(app.window, app)
        dialog = app.window.get_visible_dialog()
        pump(0.3)
        assert dialog.get_mapped()
        for index, widget in enumerate(widgets(dialog)):
            serial = 1000 + cycle * 10000 + index
            created.add(serial)
            track(widget, serial)
        del widget
        dialog.force_close()
        del dialog
        pump(0.6)
        gc.collect()
    assert created <= _finalized, (
        f"{entry}: {len(created - _finalized)}/{len(created)} widgets retained"
    )


def test_prompt_probe_does_not_block_gtk_or_publish_after_close(app, monkeypatch):
    import threading

    from ui import presets_dialog

    main_thread = threading.get_ident()
    probe_threads = []
    copied = []
    finished = threading.Event()

    def build(_request):
        probe_threads.append(threading.get_ident())
        finished.set()
        return "measured prompt"

    monkeypatch.setattr(presets_dialog, "build_prompt", build)
    monkeypatch.setattr(presets_dialog, "copy_to_clipboard", copied.append)
    owner = presets_dialog.show_ai_preset_dialog(app.window, app)
    pump(0.1)
    owner._on_copy()
    assert finished.wait(2)
    assert probe_threads == [probe_threads[0]] and probe_threads[0] != main_thread
    test_gtk.until(lambda: copied == ["measured prompt"])
    owner._on_copy()
    owner.dialog.force_close()
    pump(0.6)
    assert copied == ["measured prompt"]
