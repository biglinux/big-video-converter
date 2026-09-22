"""Profile manager mixin — video mode radios and user presets.

Four built-in modes are triples of (codec, quality, copy) applied to the
data-holder widgets. A preset is a TOML recipe (utils/presets.py) applied to
many settings at once; it stays "active" while the widgets still agree with
it, so the per-encoder arguments it carries reach the script. The moment the
user changes codec, quality or copy mode by hand, the preset is released and
the mode becomes "Customize", exactly as the built-in modes behave.
"""

import gettext
import logging

_ = gettext.gettext
logger = logging.getLogger(__name__)


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
            elif btn is getattr(self, "_radio_preset", None):
                preset = self.active_preset()
                if preset is not None:
                    self.apply_preset(preset)
                else:
                    from ui.presets_dialog import show_presets_dialog

                    show_presets_dialog(self.window, self)
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
        self.settings_manager.save_setting("active-preset", "")
        self.video_codec_combo.set_selected(codec_idx)
        self.video_quality_combo.set_selected(quality_idx)
        self.force_copy_video_check.set_active(force_copy)
        self.settings_manager.save_setting("video-profile", profile)
        self._update_encoding_options_state(force_copy)
        self._update_customize_subtitle()
        if hasattr(self, "_update_presets_subtitle"):
            self._update_presets_subtitle()

    def _select_profile_radio(self, profile: str):
        """Select the correct radio for a profile without triggering the handler."""
        self._profile_guard = True
        radios = {
            "copy": self._radio_copy,
            "universal": self._radio_universal,
            "smaller": self._radio_smaller,
            "quality": self._radio_quality,
            "preset": getattr(self, "_radio_preset", None),
        }
        btn = radios.get(profile) or self._radio_custom
        btn.set_active(True)
        self._profile_guard = False

    def _detect_current_profile(self) -> str:
        """Detect which profile matches current widget state."""
        codec_idx = self.video_codec_combo.get_selected()
        quality_idx = self.video_quality_combo.get_selected()
        force_copy = self.force_copy_video_check.get_active()
        preset = self.active_preset()
        if preset is not None:
            if self._widgets_match_preset(preset, codec_idx, quality_idx, force_copy):
                return "preset"
            # The user walked away from the recipe: release it, otherwise its
            # encoder arguments would keep shaping a conversion that no longer
            # looks like the preset on screen.
            self.settings_manager.save_setting("active-preset", "")
            if hasattr(self, "_update_presets_subtitle"):
                self._update_presets_subtitle()
        if force_copy or codec_idx == 0:
            return "copy"
        if codec_idx == 1 and quality_idx == 0:
            return "universal"
        if codec_idx == 2 and quality_idx == 0:
            return "smaller"
        if codec_idx == 3 and quality_idx == 0:
            return "quality"
        return "custom"

    # ── Presets ──

    def active_preset(self):
        """The Preset selected in the sidebar, or None."""
        preset_id = self.settings_manager.load_setting("active-preset", "")
        if not preset_id:
            return None
        from utils.presets import find_preset

        preset = find_preset(preset_id)
        if preset is None:
            # The file is gone; forget it rather than failing every conversion.
            self.settings_manager.save_setting("active-preset", "")
        return preset

    def _widgets_match_preset(self, preset, codec_idx, quality_idx, force_copy) -> bool:
        from constants import VIDEO_CODEC_VALUES, VIDEO_QUALITY_VALUES

        codec = preset.video.get("codec")
        if codec == "copy":
            return bool(force_copy) or codec_idx == 0
        if force_copy:
            return False
        if codec and VIDEO_CODEC_VALUES.get(codec_idx) != codec:
            return False
        quality = preset.video.get("quality")
        if quality and VIDEO_QUALITY_VALUES.get(quality_idx) != quality:
            return False
        return True

    def apply_preset(self, preset) -> None:
        """Write a preset into the settings and refresh every widget from them."""
        from utils.presets import preset_settings

        values = preset_settings(preset)
        with self.settings_manager.batch_update():
            for key, value in values.items():
                self.settings_manager.save_setting(key, value)
            self.settings_manager.save_setting("active-preset", preset.id)
            self.settings_manager.save_setting("video-profile", "custom")
        force_copy = values.get("force-copy-video", self.settings_manager.load_setting("force-copy-video", False))
        self._load_left_pane_settings()
        if hasattr(self, "settings_page"):
            self.settings_page._load_settings()
            self.settings_page.update_for_force_copy_state(force_copy)
        self._update_encoding_options_state(force_copy)
        self._select_profile_radio("preset")
        self._update_customize_subtitle()
        if hasattr(self, "_update_presets_subtitle"):
            self._update_presets_subtitle()
        for updater in ("_update_audio_subtitle", "_update_subtitles_subtitle", "_update_extra_subtitle"):
            if hasattr(self, updater):
                getattr(self, updater)()
        logger.info("Preset applied: %s (%s)", preset.name, preset.id)
