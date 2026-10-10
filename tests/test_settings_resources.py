"""Remembering an unchanged preference must not replace its on-disk copy."""

import pytest
from engine.settings_store import SettingsStore


@pytest.mark.parametrize("repeats", [0, 5, 25])
def test_unchanged_preference_does_not_write(tmp_path, repeats):
    path = tmp_path / "settings.json"
    settings = SettingsStore(dev_mode=True, dev_settings_file=str(path))
    assert settings.save_setting("output-folder", "/videos")
    before = path.stat()
    for _ in range(repeats):
        assert settings.save_setting("output-folder", "/videos")
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert not path.with_suffix(".json.bak").exists()


def test_reset_failure_is_reported_not_hidden(monkeypatch):
    """A settings file that cannot be written must not look like a reset."""
    from types import SimpleNamespace

    from ui import settings_page

    shown = []

    class Alert:
        def set_message(self, text):
            shown.append(text)

        def set_detail(self, text):
            shown.append(text)

        def show(self, _window):
            pass

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(settings_page.Gtk, "AlertDialog", Alert)
    page = SimpleNamespace(_reset_all_settings=fail, app=SimpleNamespace(window=None))
    dialog = SimpleNamespace(choose_finish=lambda _result: 1)
    settings_page.SettingsPage._on_reset_confirmation_response(page, dialog, None)
    assert shown[0] == "Settings Could Not Be Reset" and "disk full" in shown[1]


def test_a_dragged_slider_writes_the_file_once(tmp_path, monkeypatch):
    settings = SettingsStore(dev_mode=True, dev_settings_file=str(tmp_path / "s.json"))
    writes = []
    save = settings.save_to_disk
    monkeypatch.setattr(settings, "save_to_disk", lambda: writes.append(1) or save())
    for step in range(50):
        settings.set_value_soon("noise-gate-intensity", step / 50)
    assert settings.get_value("noise-gate-intensity") == 49 / 50 and not writes
    settings.flush()
    settings.flush()
    assert len(writes) == 1
    reread = SettingsStore(dev_mode=True, dev_settings_file=str(tmp_path / "s.json"))
    assert reread.get_value("noise-gate-intensity") == 49 / 50
