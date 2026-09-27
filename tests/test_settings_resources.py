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
