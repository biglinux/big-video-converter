"""Audio settings mixin — noise reduction, gate, compressor, EQ, normalize handlers."""


class AudioSettingsMixin:
    """Mixin providing audio processing toggle/settings handlers."""

    def _refresh_nr_preview(self) -> None:
        """Refresh the editor's audio filter preview once the editor exists."""
        if getattr(self, "video_edit_page", None):
            self.video_edit_page._apply_audio_filters()

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
