"""Audio settings mixin — noise reduction, gate, compressor, EQ, normalize handlers."""

import gettext

from gi.repository import GLib

_ = gettext.gettext


class AudioSettingsMixin:
    """Mixin providing audio processing toggle/settings handlers."""

    def _refresh_nr_preview(self) -> None:
        """Refresh the mpv audio filter preview if NR is active."""
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            if hasattr(self.video_edit_page, "_apply_audio_filters"):
                self.video_edit_page._apply_audio_filters()

    def build_nr_ladspa_filter(self) -> str:
        """Build the LADSPA noise reduction filter string from current settings.

        Returns an empty string if NR is disabled or the plugin is missing.
        """
        if not self.noise_reduction_switch.get_active():
            return ""
        import os
        if not os.path.exists("/usr/lib/ladspa/libgtcrn_ladspa.so"):
            return ""

        sm = self.settings_manager
        strength = sm.load_setting("noise-reduction-strength", 1.0)
        model = sm.load_setting("noise-model", 0)
        speech = sm.load_setting("noise-speech-strength", 1.0)
        lookahead = sm.load_setting("noise-lookahead", 50)
        blend = 1 if sm.load_setting("noise-model-blend", False) else 0
        voice_recovery = sm.load_setting("noise-voice-recovery", 0.75)

        return (
            f"ladspa=file=libgtcrn_ladspa:plugin=gtcrn_mono:"
            f"controls=c0=1|c1={strength}|c2={model}|"
            f"c3={speech}|c4={lookahead}|c5={blend}|"
            f"c6={voice_recovery}"
        )

    def _on_noise_reduction_toggled(self, switch, state):
        """Handle noise reduction toggle.

        Sub-filters (gate, compressor, HPF, EQ, normalize) are NOT disabled
        when NR is toggled off — they work independently.
        """
        self.settings_manager.save_setting("noise-reduction", state)
        self._update_audio_cleaning_subtitle()

        # Update audio filter preview in edit page
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            self.video_edit_page.update_nr_button_visibility()

        return False

    def _on_gate_switch_changed(self, switch, state):
        """Handle noise gate toggle"""
        self.settings_manager.save_setting("noise-gate-enabled", state)

        self.gate_expander.set_enable_expansion(state)
        self.gate_intensity_scale.set_sensitive(state)

        if not state:
            self.gate_expander.set_expanded(False)

        self._refresh_nr_preview()
        return False

    def _on_compressor_switch_changed(self, switch, state):
        """Handle compressor toggle"""
        self.settings_manager.save_setting("compressor-enabled", state)

        self.compressor_expander.set_enable_expansion(state)
        self.compressor_intensity_scale.set_sensitive(state)

        if not state:
            self.compressor_expander.set_expanded(False)

        self._refresh_nr_preview()
        return False

    def _on_hpf_toggled(self, row, pspec):
        """Handle HPF toggle"""
        active = row.get_active()
        self.settings_manager.save_setting("hpf-enabled", active)
        self.hpf_freq_row.set_visible(active)
        self._refresh_nr_preview()

    def _on_eq_switch_changed(self, switch, state):
        """Handle EQ toggle"""
        self.settings_manager.save_setting("eq-enabled", state)
        self.eq_expander.set_enable_expansion(state)
        self.eq_preset_row.set_sensitive(state)

        if not state:
            self.eq_expander.set_expanded(False)

        self._refresh_nr_preview()
        return False

    def _on_normalize_toggled(self, row, pspec):
        """Handle loudness normalization toggle"""
        self.settings_manager.save_setting("normalize-enabled", row.get_active())
        self._refresh_nr_preview()

    def _on_eq_preset_changed(self, row, param):
        """Handle EQ preset change"""
        idx = row.get_selected()
        if idx < len(self._eq_preset_keys):
            key = self._eq_preset_keys[idx]
            self.settings_manager.save_setting("eq-preset", key)
            bands = self._eq_presets.get(key, [0.0] * 10)
            self.settings_manager.save_setting(
                "eq-bands", ",".join(str(b) for b in bands)
            )
            self._refresh_nr_preview()
