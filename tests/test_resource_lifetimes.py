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
