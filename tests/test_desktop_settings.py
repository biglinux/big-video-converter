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


@pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
    reason="Requires an X11 or Wayland display")
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
    log_path = tmp_path / "application.log"
    # Session services may inherit stderr after the application has exited.
    # A file lets wait() observe the process exit instead of waiting for EOF.
    with log_path.open("w") as log:
        result = subprocess.run(
            ["dbus-run-session", "--", sys.executable, "-X", "faulthandler", "-c", code, str(APP_DIR)],
            env=env, cwd=tmp_path, stdout=log, stderr=subprocess.STDOUT, timeout=20, check=False,
        )
    output = log_path.read_text()
    assert result.returncode == 0, output
    assert "APPLICATION_OPENED_WITHOUT_GNOME_SCHEMAS" in output


@pytest.mark.skipif(not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')),
                    reason='Requires an isolated display')
@pytest.mark.parametrize('exit_action', ['quit', 'close'])
def test_exit_waits_for_conversion_cleanup(tmp_path, exit_action):
    code = textwrap.dedent('''
        import os, sys
        sys.path.insert(0, sys.argv[1])
        from main import VideoConverterApp
        from gi.repository import GLib
        from utils.conversion import run_with_progress_dialog
        app = VideoConverterApp()
        app.settings_manager.save_setting('show-welcome-dialog', False)
        app.settings_manager.save_setting('show-conversion-help-on-startup', False)
        rows = []
        def activate(app):
            run_with_progress_dialog(app,
                [sys.executable, '-c', 'import time; time.sleep(30)'],
                'shutdown test', env_vars=os.environ.copy())
            row = next(iter(app.progress_page.active_conversions.values()))['row']
            rows.append((row, row.process))
            def leave():
                if sys.argv[2] == 'quit':
                    app.lookup_action('quit').activate(None)
                else:
                    app.window.close()
                assert app._close_dialog is not None
                assert not row.cancel_event.is_set()
                def buttons(widget):
                    from gi.repository import Gtk
                    if isinstance(widget, Gtk.Button):
                        yield widget
                    child = widget.get_first_child()
                    while child:
                        yield from buttons(child)
                        child = child.get_next_sibling()
                next(button for button in buttons(app._close_dialog)
                     if button.get_label() == 'Stop and close').emit('clicked')
                return False
            GLib.timeout_add(50, leave)
        app.connect('activate', activate)
        app.run(['shutdown-test'])
        row, process = rows[0]
        assert row.cancel_event.is_set()
        assert process.poll() is not None
        assert app.conversions_running == 0
        assert not app.progress_page.active_conversions
        assert app.conversion_page.thumbnail_manager.shutdown_complete
    ''')
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / 'config'),
               XDG_DATA_HOME=str(tmp_path / 'data'))
    log_path = tmp_path / "shutdown.log"
    with log_path.open("w") as log:
        result = subprocess.run(
            ["dbus-run-session", "--", sys.executable, '-c', code, str(APP_DIR), exit_action],
            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=30, check=False)
    assert result.returncode == 0, log_path.read_text()


@pytest.mark.parametrize('base', ['arch', 'debian', 'rpm'])
def test_dependency_transaction_is_visible_and_interactive(base):
    import shlex

    from utils.dependency_checker import DependencyChecker

    checker = DependencyChecker.__new__(DependencyChecker)
    checker.distro = {'base': base}
    info = checker.get_install_command()
    command = info['command']
    assert shlex.split(info['display']) == command
    assert command[0] == 'pkexec'
    assert not {'sh', '-c', '-y', '--noconfirm', '--allowerasing', '-Sy', '-Syu'} & set(command)
    assert not any('://' in argument for argument in command)
    assert set(info['packages']) <= set(command)
