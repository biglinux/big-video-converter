"""Regressions for optional desktop integration, including native GIO aborts."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path
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


# On the private bus of dbus-run-session, GTK and libadwaita activated the
# desktop's portal (xdg-desktop-portal-kde, which started ksecretd), and both
# outlived the bus, one pair per test run. These tests need no portal.
NO_PORTALS = {"ADW_DISABLE_PORTAL": "1", "GDK_DEBUG": "no-portals"}


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
        **NO_PORTALS,
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
               XDG_DATA_HOME=str(tmp_path / 'data'), **NO_PORTALS)
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
    assert not {'sh', '-c', '-y', '--noconfirm', '-Sy', '-Syu'} & set(command)
    assert not any('://' in argument for argument in command)
    assert set(info['packages']) <= set(command)
    # Replacing ffmpeg-free needs --allowerasing; dnf still asks before erasing.
    assert ('--allowerasing' in command) == (base == 'rpm') == bool(info.get('note'))


@pytest.mark.parametrize('returncode,owner,available', [
    (0, 'ffmpeg-free\n', False), (0, 'ffmpeg\n', True),
    (1, 'file /usr/local/bin/ffmpeg is not owned by any package\n', True),
])
def test_only_an_ffmpeg_free_owner_needs_replacement(monkeypatch, returncode, owner, available):
    from utils import dependency_checker

    checker = dependency_checker.DependencyChecker.__new__(dependency_checker.DependencyChecker)
    checker.distro, checker.ffmpeg_path, checker.mpv_path = {'base': 'rpm'}, '/x/ffmpeg', '/x/mpv'
    monkeypatch.setattr(dependency_checker.subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a[0], returncode, owner, ''))
    assert checker.are_dependencies_available() is available


def _fake_drm(tmp_path, nodes):
    for node, slot, vendor in nodes:
        pci = tmp_path / 'pci' / slot
        pci.mkdir(parents=True)
        if vendor:
            (pci / 'vendor').write_text(vendor + '\n')
        (tmp_path / 'drm' / node).mkdir(parents=True)
        (tmp_path / 'drm' / node / 'device').symlink_to(pci)
    return str(tmp_path / 'drm')


def test_render_nodes_are_named_by_their_own_pci_device(tmp_path):
    from utils.gpu_selector import detect_render_devices

    drm = _fake_drm(tmp_path, [('card0', '0000:00:01.0', None),
                               ('renderD128', '0000:00:02.0', '0x8086'),
                               ('renderD129', '0000:01:00.0', '0x10de'),
                               ('renderD130', '0000:05:00.0', '0x1a03'),
                               ('renderD131', '0000:06:00.0', '0x10de')])
    lspci = {'0000:00:02.0': 'Vendor:\tIntel Corporation\nDevice:\tCoffeeLake-S GT2 [UHD Graphics 630]\n',
             '0000:01:00.0': 'Vendor:\tNVIDIA Corporation\nDevice:\tTU116 [GeForce GTX 1660 Ti]\n',
             '0000:05:00.0': 'Vendor:\tASPEED Technology, Inc.\nDevice:\tASPEED Graphics Family with a very long name\n',
             '0000:06:00.0': 'Vendor:\tNVIDIA Corporation\nDevice:\tTU116 [GeForce GTX 1660 Ti]\n'}
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs['timeout'] <= 2
        return subprocess.CompletedProcess(argv, 0, lspci[argv[-1]], '')

    gpus = detect_render_devices(drm, run)
    assert calls == [['lspci', '-vmm', '-s', slot] for slot in sorted(lspci)]
    assert [(g['device'], g['type'], g['name']) for g in gpus] == [
        ('/dev/dri/renderD128', 'intel', 'Intel CoffeeLake-S GT2 [UHD Graphics 630]'),
        ('/dev/dri/renderD129', 'nvidia', 'NVIDIA TU116 [GeForce GTX 1660 Ti] (renderD129)'),
        ('/dev/dri/renderD130', 'unknown', 'ASPEED Technology, Inc. ASPEED Graphics Family...'),
        ('/dev/dri/renderD131', 'nvidia', 'NVIDIA TU116 [GeForce GTX 1660 Ti] (renderD131)')]


@pytest.mark.parametrize('failure', [FileNotFoundError('lspci'), subprocess.TimeoutExpired('lspci', 2), None])
def test_render_nodes_fall_back_to_the_vendor_without_lspci(tmp_path, failure):
    from utils.gpu_selector import detect_render_devices

    drm = _fake_drm(tmp_path, [('renderD128', '0000:00:02.0', '0x8086'),
                               ('renderD129', '0000:01:00.0', '0x10de'),
                               ('renderD130', '0000:05:00.0', '0x1a03')])

    def run(argv, **kwargs):
        if failure:
            raise failure
        return subprocess.CompletedProcess(argv, 1, '', 'lspci: -s: Invalid slot number')

    assert [g['name'] for g in detect_render_devices(drm, run)] == [
        'Intel (renderD128)', 'NVIDIA (renderD129)', 'renderD130']


def test_every_integration_offers_the_same_video_files():
    """Nautilus cannot import the application, so it keeps its own copy."""
    import ast
    import configparser

    import constants
    from file_handler import FileHandlerMixin

    share = APP_DIR.parent
    tree = ast.parse((share / 'nautilus-python/extensions/big_video_converter_extension.py').read_text())
    literal = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and ast.unparse(node.targets[0]) == 'self.supported_extensions')
    assert ast.literal_eval(literal) == constants.VIDEO_FILE_EXTENSIONS
    for suffix in ('.mpg', '.3gp', '.ogv', '.m2ts', '.mts'):
        assert FileHandlerMixin.is_valid_video_file(None, 'clip' + suffix.upper())
    for path in [share / 'kio/servicemenus/big-video-converter.desktop',
                 share / 'applications/br.com.biglinux.converter.desktop']:
        entry = configparser.ConfigParser(interpolation=None, strict=False)
        entry.read(path, encoding='utf-8')
        execs = [entry[s]['Exec'] for s in entry.sections() if 'Exec' in entry[s]]
        assert execs and all('%U' not in e and '%u' not in e for e in execs), path
        assert set(entry['Desktop Entry']['MimeType'].strip(';').split(';')) >= {
            'video/matroska', 'video/x-matroska', 'video/3gpp', 'video/ogg', 'video/mpeg', 'video/mp2t'}

