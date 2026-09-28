"""Remembering an unchanged preference must not replace its on-disk copy."""

import pytest
from utils.settings_manager import SettingsManager


@pytest.mark.parametrize("repeats", [0, 5, 25])
def test_unchanged_preference_does_not_write(tmp_path, repeats):
    path = tmp_path / "settings.json"
    settings = SettingsManager("test", dev_mode=True, dev_settings_file=str(path))
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
