"""Regressions for optional desktop integration, including native GIO aborts."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from unittest.mock import Mock

import pytest


SCHEMA_ID = "org.gnome.desktop.wm.preferences"
APP_DIR = Path(__file__).resolve().parents[1] / "big-video-converter/usr/share/big-video-converter"


@pytest.fixture
def main_module():
    import main
    return main


@pytest.mark.parametrize("missing", ["source", "schema", "key", "fixed-path"])
def test_unavailable_desktop_schema_never_reaches_constructor(main_module, monkeypatch, missing):
    schema = Mock()
    schema.has_key.return_value = missing != "key"
    schema.get_path.return_value = None if missing == "fixed-path" else "/org/gnome/desktop/wm/preferences/"
    source = Mock()
    source.lookup.return_value = None if missing == "schema" else schema
    get_default = Mock(return_value=None if missing == "source" else source)
    legacy_constructor = Mock()
    full_constructor = Mock()
    monkeypatch.setattr(main_module.Gio.SettingsSchemaSource, "get_default", get_default)
    monkeypatch.setattr(main_module.Gio.Settings, "new", legacy_constructor)
    monkeypatch.setattr(main_module.Gio.Settings, "new_full", full_constructor)

    assert main_module.VideoConverterApp._window_buttons_on_left(None) is False
    get_default.assert_called_once_with()
    if missing != "source":
        source.lookup.assert_called_once_with(SCHEMA_ID, True)
    legacy_constructor.assert_not_called()
    full_constructor.assert_not_called()


@pytest.mark.parametrize("layout,expected", [
    ("close,minimize,maximize:", True),
    (":minimize,maximize,close", False),
    ("menu:close", False),
    ("close", False),
    ("", False),
])
def test_available_desktop_schema_preserves_button_layout(main_module, monkeypatch, layout, expected):
    schema = Mock()
    schema.has_key.return_value = True
    schema.get_path.return_value = "/org/gnome/desktop/wm/preferences/"
    source = Mock()
    source.lookup.return_value = schema
    settings = Mock()
    settings.get_string.return_value = layout
    constructor = Mock(return_value=settings)
    monkeypatch.setattr(main_module.Gio.SettingsSchemaSource, "get_default", lambda: source)
    monkeypatch.setattr(main_module.Gio.Settings, "new_full", constructor)

    assert main_module.VideoConverterApp._window_buttons_on_left(None) is expected
    source.lookup.assert_called_once_with(SCHEMA_ID, True)
    schema.has_key.assert_called_once_with("button-layout")
    constructor.assert_called_once_with(schema, None, None)
    settings.get_string.assert_called_once_with("button-layout")


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Requires Xvfb or an X11 display")
def test_real_application_opens_with_no_gnome_schemas(tmp_path):
    """Use a fresh process because GLib caches the schema source globally."""
    empty = tmp_path / "empty-schemas"
    home = tmp_path / "home"
    empty.mkdir()
    home.mkdir()
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_DATA_DIRS=str(empty),
        GSETTINGS_SCHEMA_DIR=str(empty),
        GSETTINGS_BACKEND="memory",
        PYTHONUNBUFFERED="1",
    )
    code = textwrap.dedent('''
        import sys
        import time
        sys.path.insert(0, sys.argv[1])
        from gi.repository import Gio, GLib
        source = Gio.SettingsSchemaSource.get_default()
        assert source is None or source.lookup("org.gnome.desktop.wm.preferences", True) is None
        from main import VideoConverterApp
        assert VideoConverterApp._window_buttons_on_left(None) is False
        application = VideoConverterApp()
        application.settings_manager.save_setting("show-welcome-dialog", False)
        application.settings_manager.save_setting("show-conversion-help-on-startup", False)
        assert application.register(None)
        application.activate()
        try:
            context = GLib.MainContext.default()
            deadline = time.monotonic() + 5
            while not application.window.get_mapped() and time.monotonic() < deadline:
                for _ in range(100):
                    if not context.pending():
                        break
                    context.iteration(False)
                time.sleep(0.01)
            assert application.window.get_mapped()
            assert application.window.get_title() == "Big Video Converter"
            assert application.conversion_page and application.progress_page and application.video_edit_page
            print("APPLICATION_OPENED_WITHOUT_GNOME_SCHEMAS", flush=True)
        finally:
            if getattr(application, "video_edit_page", None):
                application.video_edit_page.cleanup()
            application.window.destroy()
            application.quit()
    ''')
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code, str(APP_DIR)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "APPLICATION_OPENED_WITHOUT_GNOME_SCHEMAS" in result.stdout
