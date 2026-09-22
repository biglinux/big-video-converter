from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIDEBAR = (
    ROOT
    / "big-video-converter/usr/share/big-video-converter/sidebar_builder.py"
)
TEST = ROOT / "tests/test_audio_ui_state.py"

source = SIDEBAR.read_text(encoding="utf-8")
old = '''    def _on_audio_handling_changed(self, combo, _pspec):
        """Handle audio handling combo change — disable NR when audio is copy/none."""
        selected = combo.get_selected()
        self.settings_manager.save_setting(
            "audio-handling", AUDIO_VALUES.get(selected, "copy")
        )
        audio_will_reencode = selected == 1  # index 1 = "reencode"
        self._audio_cleaning_row.set_sensitive(audio_will_reencode)
        if not audio_will_reencode:
            self.noise_reduction_switch.set_active(False)
        self._update_audio_subtitle()
'''
new = '''    def _on_audio_handling_changed(self, combo, _pspec):
        """Update applicability without erasing saved cleaning preferences."""
        selected = combo.get_selected()
        self.settings_manager.save_setting(
            "audio-handling", AUDIO_VALUES.get(selected, "copy")
        )
        audio_will_reencode = selected == 1  # index 1 = "reencode"
        self._audio_cleaning_row.set_sensitive(audio_will_reencode)
        self._update_audio_subtitle()
'''
if new not in source:
    if old not in source:
        raise SystemExit("expected audio handler was not found")
    SIDEBAR.write_text(source.replace(old, new, 1), encoding="utf-8")

text = TEST.read_text(encoding="utf-8")
regression = '''

def test_sidebar_does_not_erase_saved_noise_reduction_when_mode_changes():
    sidebar = (
        Path(__file__).resolve().parents[1]
        / "big-video-converter/usr/share/big-video-converter/sidebar_builder.py"
    ).read_text(encoding="utf-8")
    start = sidebar.index("    def _on_audio_handling_changed")
    end = sidebar.index("    def _update_encoding_options_state", start)
    handler = sidebar[start:end]
    assert "noise_reduction_switch.set_active(False)" not in handler
    assert "_audio_cleaning_row.set_sensitive(audio_will_reencode)" in handler
'''
if "test_sidebar_does_not_erase_saved_noise_reduction_when_mode_changes" not in text:
    TEST.write_text(text.rstrip() + regression + "\n", encoding="utf-8")
