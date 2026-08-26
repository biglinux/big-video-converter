"""Profile manager mixin — video encoding profile toggle/apply/detect."""

import gettext

_ = gettext.gettext


class ProfileManagerMixin:
    """Mixin providing video encoding profile management."""

    def _on_profile_toggled(self, btn):
        """Apply a video profile when its radio is selected."""
        if not btn.get_active() or self._profile_guard:
            return
        self._profile_guard = True
        try:
            if btn is self._radio_copy:
                self._apply_profile("copy")
            elif btn is self._radio_universal:
                self._apply_profile("universal")
            elif btn is self._radio_smaller:
                self._apply_profile("smaller")
            elif btn is self._radio_quality:
                self._apply_profile("quality")
        finally:
            self._profile_guard = False

    def _apply_profile(self, profile: str):
        """Configure data-holder widgets for the selected profile."""
        # Profiles: (codec_idx, quality_idx, force_copy)
        # codec 0=copy, 1=h264, 2=h265, 3=av1
        # quality 0=good(default)
        profiles = {
            "copy": (0, 0, True),
            "universal": (1, 0, False),
            "smaller": (2, 0, False),
            "quality": (3, 0, False),  # AV1
        }
        codec_idx, quality_idx, force_copy = profiles[profile]
        self.video_codec_combo.set_selected(codec_idx)
        self.video_quality_combo.set_selected(quality_idx)
        self.force_copy_video_check.set_active(force_copy)
        self.settings_manager.save_setting("video-profile", profile)
        self._update_encoding_options_state(force_copy)
        self._update_customize_subtitle()

    def _select_profile_radio(self, profile: str):
        """Select the correct radio for a profile without triggering the handler."""
        self._profile_guard = True
        radios = {
            "copy": self._radio_copy,
            "universal": self._radio_universal,
            "smaller": self._radio_smaller,
            "quality": self._radio_quality,
        }
        btn = radios.get(profile, self._radio_custom)
        btn.set_active(True)
        self._profile_guard = False

    def _detect_current_profile(self) -> str:
        """Detect which profile matches current widget state."""
        codec_idx = self.video_codec_combo.get_selected()
        quality_idx = self.video_quality_combo.get_selected()
        force_copy = self.force_copy_video_check.get_active()
        if force_copy or codec_idx == 0:
            return "copy"
        if codec_idx == 1 and quality_idx == 0:
            return "universal"
        if codec_idx == 2 and quality_idx == 0:
            return "smaller"
        if codec_idx == 3 and quality_idx == 0:
            return "quality"
        return "custom"
