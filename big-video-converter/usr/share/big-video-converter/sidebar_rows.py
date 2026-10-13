"""Sidebar rows mixin: the left pane's saved settings and the summaries its rows show."""

import gettext

from constants import (
    AUDIO_VALUES,
    GPU_VALUES,
    SUBTITLE_VALUES,
    VIDEO_CODEC_VALUES,
    VIDEO_QUALITY_VALUES,
)
from gi.repository import GLib

_ = gettext.gettext


class SidebarRowsMixin:
    """Mixin restoring and saving the sidebar settings and keeping its row summaries current."""

    def _save_slider(self, key, value):
        """Write the settings file once the slider has stopped for 300 ms."""
        self.settings_store.set_value_soon(key, value)
        if not getattr(self, "_slider_flush_id", 0):
            self._slider_flush_id = GLib.timeout_add(300, self._flush_slider_settings)

    def _flush_slider_settings(self):
        self._slider_flush_id = 0
        self.settings_store.flush()
        return GLib.SOURCE_REMOVE

    def _load_left_pane_settings(self):
        """Load settings for left pane controls.

        Restoring widgets emits their signals, whose handlers write back to the
        settings. Writes are suspended for the whole restore so that a failure
        halfway through cannot leave the user's configuration replaced by the
        widgets' default values.
        """
        with self.settings_store.suspend_writes():
            self._load_left_pane_settings_inner()

    def _load_left_pane_settings_inner(self):
        """Actual widget restore; never writes settings (see caller)."""
        # GPU
        gpu_value = self.settings_store.load_setting("gpu", "auto")
        gpu_index = self._find_gpu_index(gpu_value)
        self.gpu_combo.set_selected(gpu_index)

        # GPU Device
        gpu_device_index = self.settings_store.load_setting("gpu-device-index", 0)
        if gpu_device_index <= len(self.detected_gpus):
            self.gpu_device_combo.set_selected(gpu_device_index)

        # Output Format
        format_index = self.settings_store.load_setting("output-format-index", 0)
        self.output_format_combo.set_selected(format_index)

        # Video Quality
        quality_value = self.settings_store.load_setting("video-quality", "default")
        quality_index = self._find_quality_index(quality_value)
        self.video_quality_combo.set_selected(quality_index)

        # Video Codec (index 0 = Copy, 1 = H.264, ...)
        force_copy = self.settings_store.load_setting("force-copy-video", False)
        if force_copy:
            self.video_codec_combo.set_selected(0)
        else:
            codec_value = self.settings_store.load_setting("video-codec", "h264")
            codec_index = self._find_codec_index(codec_value)
            # Avoid accidentally selecting "Copy" when loading a real codec
            if codec_index == 0:
                codec_index = 1
            self.video_codec_combo.set_selected(codec_index)
        self.force_copy_video_check.set_active(force_copy)
        self._update_encoding_options_state(force_copy)

        # Audio Handling - Use reverse lookup
        audio_value = self.settings_store.load_setting("audio-handling", "copy")
        audio_index = 0  # Default
        for index, internal_value in AUDIO_VALUES.items():
            if internal_value == audio_value:
                audio_index = index
                break
        self.audio_handling_combo.set_selected(audio_index)

        # Subtitle - Use reverse lookup
        subtitle_value = self.settings_store.load_setting(
            "subtitle-extract", "embedded"
        )
        subtitle_index = 0  # Default
        for index, internal_value in SUBTITLE_VALUES.items():
            if internal_value == subtitle_value:
                subtitle_index = index
                break
        self.subtitle_combo.set_selected(subtitle_index)

        # Noise Reduction
        noise_reduction = self.settings_store.load_setting("noise-reduction", False)
        self.noise_reduction_switch.set_active(noise_reduction)
        self.noise_reduction_expander.set_enable_expansion(noise_reduction)

        # Noise Reduction Strength
        noise_strength = self.settings_store.load_setting(
            "noise-reduction-strength", 1.0
        )
        self.noise_strength_scale.set_value(noise_strength)

        self.noise_model_row.set_selected(
            self.settings_store.load_setting("noise-model", 0)
        )

        # Noise Gate
        gate_enabled = self.settings_store.load_setting("noise-gate-enabled", False)
        self.gate_switch.set_active(gate_enabled)
        self.gate_expander.set_enable_expansion(gate_enabled)
        self.gate_intensity_scale.set_value(
            self.settings_store.load_setting("noise-gate-intensity", 0.5)
        )
        self.gate_intensity_scale.set_sensitive(gate_enabled)

        # Compressor
        comp_enabled = self.settings_store.load_setting("compressor-enabled", False)
        self.compressor_switch.set_active(comp_enabled)
        self.compressor_expander.set_enable_expansion(comp_enabled)
        self.compressor_intensity_scale.set_value(
            self.settings_store.load_setting("compressor-intensity", 1.0)
        )
        self.compressor_intensity_scale.set_sensitive(comp_enabled)

        # HPF
        hpf_enabled = self.settings_store.load_setting("hpf-enabled", False)
        self.hpf_row.set_active(hpf_enabled)
        self.hpf_freq_scale.set_value(
            self.settings_store.load_setting("hpf-frequency", 80)
        )
        self.hpf_freq_row.set_visible(hpf_enabled)

        # EQ
        eq_enabled = self.settings_store.load_setting("eq-enabled", False)
        self.eq_switch.set_active(eq_enabled)
        self.eq_expander.set_enable_expansion(eq_enabled)
        self.eq_preset_row.set_sensitive(eq_enabled)
        eq_preset = self.settings_store.load_setting("eq-preset", "flat")
        if eq_preset in self._eq_preset_keys:
            self.eq_preset_row.set_selected(self._eq_preset_keys.index(eq_preset))

        # Loudness Normalization
        normalize_enabled = self.settings_store.load_setting("normalize-enabled", False)
        self.normalize_row.set_active(normalize_enabled)

        # The tooltip setting is loaded by tooltip_action in _setup_actions.

        self._select_profile_radio(self._detect_current_profile())
        self._update_customize_subtitle()
        self._update_presets_subtitle()
        self._update_audio_subtitle()
        self._update_audio_cleaning_subtitle()
        self._update_subtitles_subtitle()
        self._update_extra_subtitle()
        self._update_size_subtitle()

    def _update_customize_subtitle(self):
        """Update the 'Customize encoding...' row subtitle with current settings."""
        profile = self._detect_current_profile()
        parts = []
        if profile == "custom":
            codec_model = self.video_codec_combo.get_model()
            if codec_model:
                val = (
                    codec_model.get_string(self.video_codec_combo.get_selected()) or ""
                )
                if val:
                    parts.append(val)
        quality_idx = self.video_quality_combo.get_selected()
        if quality_idx > 0:
            quality_model = self.video_quality_combo.get_model()
            if quality_model:
                val = quality_model.get_string(quality_idx) or ""
                if val:
                    parts.append(val)
        fmt_model = self.output_format_combo.get_model()
        if fmt_model:
            val = fmt_model.get_string(self.output_format_combo.get_selected()) or ""
            if val:
                parts.append(val)
        gpu_idx = self.gpu_combo.get_selected()
        if gpu_idx > 0:
            gpu_model = self.gpu_combo.get_model()
            if gpu_model:
                val = gpu_model.get_string(gpu_idx) or ""
                if val:
                    parts.append(val)
        if hasattr(self, "settings_page"):
            res_idx = self.settings_page.video_resolution_combo.get_selected()
            if res_idx > 0:
                res_model = self.settings_page.video_resolution_combo.get_model()
                if res_model:
                    val = res_model.get_string(res_idx) or ""
                    if val:
                        parts.append(val)
            if self.settings_page.gpu_partial_check.get_active():
                parts.append(_("SW Decode"))
        self._customize_row.show_value(" · ".join(parts), profile == "custom")
        self._update_video_mode_value(profile)
        if getattr(self, "conversion_page", None) is not None:
            self.conversion_page.refresh_recipe_summaries()

    def _update_video_mode_value(self, profile):
        names = {
            "copy": _("Fast Copy"),
            "universal": _("Universal"),
            "smaller": _("More Efficient"),
            "quality": _("Smaller File"),
        }
        preset = self.active_preset() if profile == "preset" else None
        self._video_mode_row.show_value(
            preset.display_name if preset else names.get(profile, _("Custom"))
        )

    def _active_mode_radio(self):
        """The mode tile a click focuses: the active one, or the first."""
        for radio in (
            self._radio_copy,
            self._radio_universal,
            self._radio_smaller,
            self._radio_quality,
        ):
            if radio.get_active():
                return radio
        return self._radio_copy

    def _on_video_encoding_activated(self, *_args):
        from ui.video_encoding_dialog import show_video_encoding_dialog

        show_video_encoding_dialog(self.window, self)

    def _on_audio_activated(self, *_args):
        from ui.audio_dialog import show_audio_dialog

        # Opened from the audio card's button: the card goes, or Escape would
        # close it under the dialog first.
        self.setting_cards.close()
        show_audio_dialog(self.window, self)

    def _on_noise_options_activated(self, *_args):
        """Open the audio cleaning dialog."""
        from ui.noise_dialog import show_noise_dialog

        show_noise_dialog(self.window, self)

    def _on_presets_activated(self, *_args):
        from ui.presets_dialog import show_presets_dialog

        show_presets_dialog(self.window, self)

    def _update_presets_subtitle(self):
        """Name of the preset in use, or an invitation to browse them."""
        preset = self.active_preset()
        self._presets_row.show_value(
            preset.display_name if preset else "", preset is not None
        )

    def _on_extra_activated(self, *_args):
        from ui.extra_dialog import show_extra_dialog

        show_extra_dialog(self.window, self)

    # Sidebar subtitle update methods

    def _update_audio_subtitle(self):
        parts = []
        audio_model = self.audio_handling_combo.get_model()
        if audio_model:
            idx = self.audio_handling_combo.get_selected()
            item = audio_model.get_string(idx)
            if item:
                parts.append(item)
        if hasattr(self, "settings_page"):
            codec_model = self.settings_page.audio_codec_combo.get_model()
            if codec_model:
                idx = self.settings_page.audio_codec_combo.get_selected()
                item = codec_model.get_string(idx)
                if item and self.audio_handling_combo.get_selected() == 1:
                    parts.append(item)
        self._audio_row.show_value(" · ".join(parts))
        self._audio_options_button.set_visible(
            self.audio_handling_combo.get_selected() == 1
        )

    def audio_cleaning_summary(self):
        """What the audio settings apply, and whether anything; None while
        the audio is not re-encoded, when they cannot apply."""
        if self.audio_handling_combo.get_selected() != 1:
            return None
        active = [
            name
            for widget, name in (
                (self.noise_reduction_switch, _("AI noise removal")),
                (self.gate_switch, _("gate")),
                (self.compressor_switch, _("compressor")),
                (self.hpf_row, _("low cut")),
                (self.eq_switch, _("equalizer")),
                (self.normalize_row, _("normalize")),
            )
            if widget.get_active()
        ]
        return (", ".join(active), True) if active else (_("Disabled"), False)

    def _update_audio_cleaning_subtitle(self):
        """Show what the audio settings apply on their sidebar row."""
        summary = self.audio_cleaning_summary()
        self._audio_cleaning_row.set_unavailable(
            self._extract_reason()
            or (_("Requires audio set to re-encode") if summary is None else None)
        )
        if summary is not None:
            self._audio_cleaning_row.show_value(*summary)
        # Sync editor's Audio Cleaning row if present
        if (
            hasattr(self, "video_edit_page")
            and self.video_edit_page
            and hasattr(self.video_edit_page, "ui")
        ):
            self.video_edit_page.ui._sync_nr_subtitle()

    def _update_subtitles_subtitle(self):
        model = self.subtitle_combo.get_model()
        if model:
            idx = self.subtitle_combo.get_selected()
            item = model.get_string(idx)
            self._subtitles_row.show_value(item or "")

    def _extract_reason(self):
        """Why a row other than Subtitles and Maximum size is grayed while only
        subtitles are extracted, or None."""
        page = getattr(self, "settings_page", None)
        if page is not None and page.only_extract_subtitles_check.get_active():
            return _("Off while only extracting subtitles")
        return None

    def _update_sidebar_for_extract_mode(self):
        """Gray every option but Subtitles and Maximum size while only
        subtitles are extracted, saying why on each row."""
        reason = self._extract_reason()
        for row in (
            self._video_mode_row,
            self._presets_row,
            self._extra_row,
            self._audio_row,
        ):
            row.set_unavailable(reason)
        self._update_encoding_options_state(self.force_copy_video_check.get_active())
        self._update_audio_cleaning_subtitle()

    def _on_size_activated(self, *_args):
        from ui.size_dialog import show_size_dialog

        show_size_dialog(self.window, self)

    def _update_size_subtitle(self):
        from ui.size_dialog import size_summary

        self._size_row.show_value(
            size_summary(self.settings_store),
            bool(self.settings_store.load_setting("size-target", "")),
        )
        if getattr(self, "conversion_page", None) is not None:
            self.conversion_page.refresh_recipe_summaries()

    def _update_extra_subtitle(self):
        parts = []
        if (
            hasattr(self, "settings_page")
            and self.settings_page.options_entry.get_text().strip()
        ):
            parts.append(_("Custom FFmpeg flags"))
        self._extra_row.show_value(
            " · ".join(parts) if parts else _("No customizations"), bool(parts)
        )

    def _save_gpu_setting(self, index):
        if index in GPU_VALUES:
            self.settings_store.save_setting("gpu", GPU_VALUES[index])

    def _save_quality_setting(self, index):
        if index in VIDEO_QUALITY_VALUES:
            self.settings_store.save_setting(
                "video-quality", VIDEO_QUALITY_VALUES[index]
            )

    def _save_codec_setting(self, index):
        """Save the video codec; index 0 is copy, without re-encoding."""
        if index not in VIDEO_CODEC_VALUES:
            return
        value = VIDEO_CODEC_VALUES[index]
        is_copy = value == "copy"
        self.settings_store.save_setting("video-codec", value)
        self.settings_store.save_setting("force-copy-video", is_copy)
        # Keep hidden data-holder in sync
        if self.force_copy_video_check.get_active() != is_copy:
            self.force_copy_video_check.set_active(is_copy)
        self._update_encoding_options_state(is_copy)

    def _find_gpu_index(self, value):
        value = value.lower()
        reverse_map = {v: k for k, v in GPU_VALUES.items()}
        return reverse_map.get(value, 0)

    def _find_quality_index(self, value):
        value = value.lower()
        reverse_map = {v: k for k, v in VIDEO_QUALITY_VALUES.items()}
        return reverse_map.get(value, 0)

    def _find_codec_index(self, value):
        """Find index of codec value (default: 1 = H.264)"""
        value = value.lower()
        reverse_map = {v: k for k, v in VIDEO_CODEC_VALUES.items()}
        return reverse_map.get(value, 1)

    def _on_force_copy_toggled(self, switch, param):
        """Sync force-copy data-holder with codec combo."""
        is_active = switch.get_active()
        self.settings_store.save_setting("force-copy-video", is_active)
        # Sync codec combo: select "Copy" (0) or fallback to H.264 (1)
        if is_active and self.video_codec_combo.get_selected() != 0:
            self.video_codec_combo.set_selected(0)
        elif not is_active and self.video_codec_combo.get_selected() == 0:
            self.video_codec_combo.set_selected(1)
        self._update_encoding_options_state(is_active)

    def _on_audio_handling_changed(self, combo, _pspec):
        """Change audio applicability without erasing cleaning preferences."""
        selected = combo.get_selected()
        self.settings_store.save_setting(
            "audio-handling", AUDIO_VALUES.get(selected, "copy")
        )
        self._update_audio_subtitle()
        self._update_audio_cleaning_subtitle()
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            self.video_edit_page.update_nr_button_visibility()

    def _update_encoding_options_state(self, force_copy_enabled):
        """Enable/disable encoding options based on force copy state"""
        enable = not force_copy_enabled

        self.gpu_combo.set_sensitive(enable)
        self.video_quality_combo.set_sensitive(enable)
        self.video_codec_combo.set_sensitive(enable)

        # Codec and quality mean nothing while the video is copied.
        self._customize_row.set_unavailable(
            self._extract_reason() or (None if enable else _("Not used with Fast Copy"))
        )

        if (
            hasattr(self, "video_edit_page")
            and self.video_edit_page
            and hasattr(self.video_edit_page, "ui")
        ):
            self.video_edit_page.ui.update_for_force_copy_state(force_copy_enabled)

        self._update_customize_subtitle()
