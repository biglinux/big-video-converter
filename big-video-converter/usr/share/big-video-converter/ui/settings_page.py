import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Setup translation
import gettext

from constants import (
    APP_ID,
    GPU_OPTIONS,
    GPU_VALUES,
    VIDEO_QUALITY_OPTIONS,
    VIDEO_QUALITY_VALUES,
    VIDEO_CODEC_OPTIONS,
    VIDEO_CODEC_VALUES,
    PRESET_OPTIONS,
    PRESET_VALUES,
    SUBTITLE_OPTIONS,
    SUBTITLE_VALUES,
    AUDIO_CODEC_OPTIONS,
    AUDIO_CODEC_VALUES,
    VIDEO_RESOLUTION_OPTIONS,
    VIDEO_RESOLUTION_VALUES,
)
from gi.repository import Adw, Gtk

_ = gettext.gettext


class SettingsPage:
    """
    Settings page for application configuration.
    Adapted from the original settings dialog.
    """

    def __init__(self, app):
        self.app = app
        self.settings_manager = app.settings_manager

        # Create the settings page
        self.page = self._create_page()

        # Connect settings signals
        self._connect_setting_signals()

        # Load initial values
        self._load_settings()

    def get_page(self):
        """Return the settings page widget"""
        return self.page

    def update_for_force_copy_state(self, force_copy_enabled):
        """Update controls sensitivity based on force copy state"""
        # When force copy is enabled, most encoding options don't apply
        # Only Output Format remains functional (can choose container)
        enable_encoding_options = not force_copy_enabled

        # Encoding settings - disable all except Output Format
        self.gpu_partial_check.set_sensitive(enable_encoding_options)
        self.preset_combo.set_sensitive(enable_encoding_options)
        self.video_resolution_combo.set_sensitive(enable_encoding_options)
        self.custom_resolution_row.set_sensitive(enable_encoding_options)

        # Output format stays enabled - user can still choose container
        # self.output_format_combo.set_sensitive(True)  # Always enabled

        # Note: Audio settings remain enabled even in copy mode
        # Users may want to configure audio handling/extraction separately

        # General options stay enabled (additional ffmpeg options, extract subtitles)
        # self.options_entry.set_sensitive(True)  # Always enabled
        # self.only_extract_subtitles_check.set_sensitive(True)  # Always enabled

    def _create_page(self):
        # Create page for settings
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Add ScrolledWindow to enable scrolling when window is small
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        page.append(scrolled_window)

        # Container for scrollable content
        scrollable_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrollable_content.set_vexpand(True)
        scrolled_window.set_child(scrollable_content)

        # Use Adw.Clamp for consistent width
        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        clamp.set_tightening_threshold(600)
        scrollable_content.append(clamp)

        # Main content box
        main_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_content.set_spacing(24)
        main_content.set_margin_start(12)
        main_content.set_margin_end(12)
        main_content.set_margin_top(24)
        main_content.set_margin_bottom(24)
        clamp.set_child(main_content)

        # Create settings groups
        self._create_encoding_settings(main_content)
        self._create_audio_settings(main_content)
        self._create_general_options(main_content)

        # Group for reset button
        reset_group = Adw.PreferencesGroup()

        # Create a box for centering the button
        reset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        reset_box.set_halign(Gtk.Align.CENTER)
        reset_box.set_margin_top(24)
        reset_box.set_margin_bottom(24)

        # Create the reset button with warning style
        reset_button = Gtk.Button(label=_("Reset All Settings"))
        reset_button.add_css_class(
            "destructive-action"
        )  # Red to indicate a destructive action
        reset_button.add_css_class("pill")  # Rounded style
        reset_button.connect("clicked", self._on_reset_button_clicked)
        reset_box.append(reset_button)

        reset_group.add(reset_box)
        main_content.append(reset_group)

        return page

    def _create_encoding_settings(self, main_content):
        encoding_group = Adw.PreferencesGroup(title=_("Encoding Settings"))

        # Conversion mode switches
        self.gpu_partial_check = Adw.SwitchRow(title=_("GPU partial mode"))
        self.gpu_partial_check.set_subtitle(_("Decode using CPU, encode using GPU"))
        encoding_group.add(self.gpu_partial_check)
        self.app.tooltip_helper.add_tooltip(self.gpu_partial_check, "gpu_partial")

        # Preset
        preset_row = Adw.ComboRow(
            title=_("Compression preset"), subtitle=_("Speed vs compression ratio")
        )
        preset_model = Gtk.StringList.new(PRESET_OPTIONS)
        preset_row.set_model(preset_model)
        self.preset_combo = preset_row
        encoding_group.add(preset_row)
        self.app.tooltip_helper.add_tooltip(preset_row, "preset")

        # Output Format
        output_format_row = Adw.ComboRow(
            title=_("Output Format"), subtitle=_("Container format for output file")
        )
        format_model = Gtk.StringList()
        format_model.append("MP4")
        format_model.append("MKV")
        output_format_row.set_model(format_model)
        self.output_format_combo = output_format_row
        encoding_group.add(output_format_row)
        self.app.tooltip_helper.add_tooltip(output_format_row, "output_format")

        # Video resolution combo with common values
        resolution_model = Gtk.StringList()
        for option in VIDEO_RESOLUTION_OPTIONS:
            resolution_model.append(option)

        self.video_resolution_combo = Adw.ComboRow(title=_("Video resolution"))
        self.video_resolution_combo.set_subtitle(_("Select output resolution"))
        self.video_resolution_combo.set_model(resolution_model)
        self.video_resolution_combo.set_selected(0)  # Default
        # Request wider size to accommodate long option names
        self.video_resolution_combo.set_size_request(400, -1)
        self.app.tooltip_helper.add_tooltip(self.video_resolution_combo, "resolution")

        # Add custom entry for resolution that shows when "Custom" is selected
        self.custom_resolution_row = Adw.EntryRow(title=_("Custom resolution"))
        self.custom_resolution_row.set_tooltip_text(_("Ex: 1280x720, 1920x1080"))
        self.custom_resolution_row.set_visible(False)  # Initially hidden

        # Connect to changed signal for the custom entry
        self.custom_resolution_row.connect(
            "changed", self._on_custom_resolution_changed
        )

        encoding_group.add(self.video_resolution_combo)
        encoding_group.add(self.custom_resolution_row)

        main_content.append(encoding_group)

    def _create_audio_settings(self, main_content):
        audio_group = Adw.PreferencesGroup(title=_("Audio Settings"))

        # Audio bitrate combo with common values
        bitrate_model = Gtk.StringList()
        self.bitrate_values = [
            _("Default"),
            "96k",
            "128k",
            "192k",
            "256k",
            "320k",
            _("Custom"),
        ]
        for option in self.bitrate_values:
            bitrate_model.append(option)

        self.audio_bitrate_combo = Adw.ComboRow(title=_("Audio bitrate"))
        self.audio_bitrate_combo.set_subtitle(
            _("Select common bitrate or enter custom value")
        )
        self.audio_bitrate_combo.set_model(bitrate_model)
        self.audio_bitrate_combo.set_selected(0)  # Default

        # Add custom entry for bitrate that shows when "Custom" is selected
        self.custom_bitrate_row = Adw.EntryRow(title=_("Custom bitrate"))
        self.custom_bitrate_row.set_tooltip_text(_("Ex: 128k, 192k, 256k"))
        self.custom_bitrate_row.set_visible(False)  # Initially hidden

        # Connect combo box to show/hide custom entry
        self.audio_bitrate_combo.connect(
            "notify::selected", self._on_bitrate_combo_changed
        )

        audio_group.add(self.audio_bitrate_combo)
        audio_group.add(self.custom_bitrate_row)
        self.app.tooltip_helper.add_tooltip(self.audio_bitrate_combo, "audio_bitrate")

        # Audio channels combo with common values
        channels_model = Gtk.StringList()
        self.channels_values = [
            _("Default"),
            "1",  # Mono
            "2",  # Stereo
            "6",  # 5.1
            _("Custom"),
        ]
        for option in self.channels_values:
            channels_model.append(option)

        self.audio_channels_combo = Adw.ComboRow(title=_("Audio channels"))
        self.audio_channels_combo.set_subtitle(_("Select common channel configuration"))
        self.audio_channels_combo.set_model(channels_model)
        self.audio_channels_combo.set_selected(0)  # Default

        # Add custom entry for channels that shows when "Custom" is selected
        self.custom_channels_row = Adw.EntryRow(title=_("Custom channels"))
        self.custom_channels_row.set_tooltip_text(_("Ex: 2 (stereo), 6 (5.1)"))
        self.custom_channels_row.set_visible(False)  # Initially hidden

        # Connect combo box to show/hide custom entry
        self.audio_channels_combo.connect(
            "notify::selected", self._on_channels_combo_changed
        )

        audio_group.add(self.audio_channels_combo)
        audio_group.add(self.custom_channels_row)
        self.app.tooltip_helper.add_tooltip(self.audio_channels_combo, "audio_channels")

        # Audio codec combo for re-encoding
        codec_model = Gtk.StringList()
        for option in AUDIO_CODEC_OPTIONS:
            codec_model.append(option)

        self.audio_codec_combo = Adw.ComboRow(title=_("Audio codec"))
        self.audio_codec_combo.set_subtitle(_("Codec to use when re-encoding audio"))
        self.audio_codec_combo.set_model(codec_model)
        self.audio_codec_combo.set_selected(0)  # Default to AAC
        audio_group.add(self.audio_codec_combo)
        self.app.tooltip_helper.add_tooltip(self.audio_codec_combo, "audio_codec")

        main_content.append(audio_group)

    def _create_general_options(self, main_content):
        options_group = Adw.PreferencesGroup(title=_("General Options"))

        # Additional options
        self.options_entry = Adw.EntryRow(title=_("Additional FFmpeg options"))
        self.options_entry.set_tooltip_text(_("Ex: -ss 60 -t 30"))
        options_group.add(self.options_entry)
        self.app.tooltip_helper.add_tooltip(self.options_entry, "additional_options")

        self.only_extract_subtitles_check = Adw.SwitchRow(
            title=_("Only extract subtitles")
        )
        self.only_extract_subtitles_check.set_subtitle(
            _("Extract subtitles to .srt files")
        )
        options_group.add(self.only_extract_subtitles_check)
        self.app.tooltip_helper.add_tooltip(
            self.only_extract_subtitles_check, "extract_subtitles"
        )

        main_content.append(options_group)

    def _on_bitrate_combo_changed(self, combo, param):
        selected = combo.get_selected()
        is_custom = (
            selected == len(self.bitrate_values) - 1
        )  # Check if "Custom" is selected
        self.custom_bitrate_row.set_visible(is_custom)

        # Update the setting unless it's custom
        if not is_custom and selected > 0:  # Not default and not custom
            self.settings_manager.save_setting(
                "audio-bitrate", self.bitrate_values[selected]
            )
        elif not is_custom and selected == 0:  # Default selected
            self.settings_manager.save_setting("audio-bitrate", "")

    def _on_channels_combo_changed(self, combo, param):
        selected = combo.get_selected()
        is_custom = (
            selected == len(self.channels_values) - 1
        )  # Check if "Custom" is selected
        self.custom_channels_row.set_visible(is_custom)

        # Update the setting unless it's custom
        if not is_custom and selected > 0:  # Not default and not custom
            self.settings_manager.save_setting(
                "audio-channels", self.channels_values[selected]
            )
        elif not is_custom and selected == 0:  # Default selected
            self.settings_manager.save_setting("audio-channels", "")

    def _on_resolution_combo_changed(self, combo, param):
        """Handle resolution combo selection change"""
        selected = combo.get_selected()

        # Check if Custom is selected (last option in VIDEO_RESOLUTION_OPTIONS)
        if selected == len(VIDEO_RESOLUTION_OPTIONS) - 1:  # Custom option
            self.custom_resolution_row.set_visible(True)

            # Use the custom value if it's not empty
            custom_value = self.custom_resolution_row.get_text()
            if custom_value:
                self.settings_manager.save_setting("video-resolution", custom_value)

        else:
            self.custom_resolution_row.set_visible(False)

            # Use the mapping to get the internal value
            if selected < len(VIDEO_RESOLUTION_VALUES):
                resolution = VIDEO_RESOLUTION_VALUES[selected]
                self.settings_manager.save_setting("video-resolution", resolution)

    def _on_custom_resolution_changed(self, entry):
        """Save custom resolution when entry changes"""
        value = entry.get_text()
        if (
            value
            and self.video_resolution_combo.get_selected()
            == len(VIDEO_RESOLUTION_OPTIONS) - 1
        ):
            self.settings_manager.save_setting("video-resolution", value)

    def _connect_setting_signals(self):
        """Connect signals for saving settings"""
        # Use direct value saving instead of indexes

        # Additional settings
        self.custom_resolution_row.connect(
            "changed",
            lambda w: self.settings_manager.save_setting(
                "video-resolution", w.get_text()
            ),
        )
        self.custom_bitrate_row.connect(
            "changed",
            lambda w: self.settings_manager.save_setting("audio-bitrate", w.get_text()),
        )
        self.custom_channels_row.connect(
            "changed",
            lambda w: self.settings_manager.save_setting(
                "audio-channels", w.get_text()
            ),
        )
        self.options_entry.connect(
            "changed",
            lambda w: self.settings_manager.save_setting(
                "additional-options", w.get_text()
            ),
        )
        self.gpu_partial_check.connect(
            "notify::active",
            lambda w, p: self.settings_manager.save_setting(
                "gpu-partial", w.get_active()
            ),
        )

        # Connect preset combo change
        self.preset_combo.connect("notify::selected", self._save_preset_setting)

        # Connect output format combo change
        self.output_format_combo.connect(
            "notify::selected",
            lambda w, p: self.settings_manager.save_setting(
                "output-format-index", w.get_selected()
            ),
        )

        self.only_extract_subtitles_check.connect(
            "notify::active",
            lambda w, p: self.settings_manager.save_setting(
                "only-extract-subtitles", w.get_active()
            ),
        )

        # Connect resolution combo change
        self.video_resolution_combo.connect(
            "notify::selected", self._on_resolution_combo_changed
        )

        # Connect audio codec combo change
        self.audio_codec_combo.connect(
            "notify::selected", self._save_audio_codec_setting
        )

    def _save_gpu_setting(self, index):
        """Save GPU setting as direct value"""
        # Use the mapping from constants to save the internal value
        internal_value = GPU_VALUES.get(index, "auto")
        self.settings_manager.save_setting("gpu", internal_value)

    def _save_quality_setting(self, index):
        """Save video quality setting as direct value"""
        # Use the mapping from constants to save the internal value
        internal_value = VIDEO_QUALITY_VALUES.get(index, "medium")
        self.settings_manager.save_setting("video-quality", internal_value)

    def _save_codec_setting(self, combo_box):
        """Save video codec setting"""
        selected = combo_box.get_selected()
        if selected < len(VIDEO_CODEC_VALUES):
            # Get the internal value from the mapping
            internal_value = VIDEO_CODEC_VALUES[selected]
            self.app.settings_manager.save_setting("video_encoder", internal_value)
            print(f"Saved codec: {internal_value}")

    def _save_preset_setting(self, combo_box, _param=None):
        """Save preset setting"""
        selected = combo_box.get_selected()
        if selected < len(PRESET_VALUES):
            # Get the internal value from the mapping
            internal_value = PRESET_VALUES[selected]
            self.app.settings_manager.save_setting("preset", internal_value)
            print(f"Saved preset: {internal_value}")

    def _save_audio_codec_setting(self, combo_box, _param=None):
        """Save audio codec setting"""
        selected = combo_box.get_selected()
        if selected < len(AUDIO_CODEC_VALUES):
            # Get the internal value from the mapping
            internal_value = AUDIO_CODEC_VALUES[selected]
            self.app.settings_manager.save_setting("audio-codec", internal_value)
            print(f"Saved audio codec: {internal_value}")

    def _load_settings(self):
        """Load settings and update UI components"""

        # Load video resolution setting
        saved_resolution = self.settings_manager.load_setting("video-resolution", "")

        if saved_resolution:
            # Check if it's one of the standard resolutions using reverse mapping
            standard_index = -1
            for index, internal_value in VIDEO_RESOLUTION_VALUES.items():
                if internal_value == saved_resolution:
                    standard_index = index
                    break

            if standard_index >= 0:
                # Standard resolution found
                self.video_resolution_combo.set_selected(standard_index)
                self.custom_resolution_row.set_visible(False)
            else:
                # Must be a custom resolution
                self.video_resolution_combo.set_selected(
                    len(VIDEO_RESOLUTION_OPTIONS) - 1
                )  # Custom
                self.custom_resolution_row.set_text(saved_resolution)
                self.custom_resolution_row.set_visible(True)
        else:
            # No saved resolution, use default
            self.video_resolution_combo.set_selected(0)
            self.custom_resolution_row.set_visible(False)

        # Audio bitrate
        bitrate_value = self.settings_manager.load_setting("audio-bitrate", "")
        if bitrate_value:
            # Check if it's one of the standard bitrates
            standard_index = -1
            for i, rate in enumerate(self.bitrate_values):
                if rate == bitrate_value:
                    standard_index = i
                    break

            if standard_index >= 0:
                # Standard bitrate found
                self.audio_bitrate_combo.set_selected(standard_index)
                self.custom_bitrate_row.set_visible(False)
            else:
                # Must be a custom bitrate
                self.audio_bitrate_combo.set_selected(
                    len(self.bitrate_values) - 1
                )  # Custom
                self.custom_bitrate_row.set_text(bitrate_value)
                self.custom_bitrate_row.set_visible(True)
        else:
            # No saved bitrate, use default
            self.audio_bitrate_combo.set_selected(0)
            self.custom_bitrate_row.set_visible(False)

        # Audio channels
        channels_value = self.settings_manager.load_setting("audio-channels", "")
        if channels_value:
            # Check if it's one of the standard channel configurations
            standard_index = -1
            for i, ch in enumerate(self.channels_values):
                if ch == channels_value:
                    standard_index = i
                    break

            if standard_index >= 0:
                # Standard channels found
                self.audio_channels_combo.set_selected(standard_index)
                self.custom_channels_row.set_visible(False)
            else:
                # Must be a custom channel configuration
                self.audio_channels_combo.set_selected(
                    len(self.channels_values) - 1
                )  # Custom
                self.custom_channels_row.set_text(channels_value)
                self.custom_channels_row.set_visible(True)
        else:
            # No saved channels, use default
            self.audio_channels_combo.set_selected(0)
            self.custom_channels_row.set_visible(False)

        # Load boolean switch settings
        gpu_partial_active = self.settings_manager.load_setting("gpu-partial", False)
        self.gpu_partial_check.set_active(gpu_partial_active)

        # Load preset
        preset_value = self.settings_manager.load_setting("preset", "default")
        preset_index = self._find_preset_index(preset_value)
        self.preset_combo.set_selected(preset_index)

        # Load output format
        format_index = self.settings_manager.load_setting("output-format-index", 0)
        self.output_format_combo.set_selected(format_index)

        # Load audio codec
        audio_codec_value = self.settings_manager.load_setting("audio-codec", "aac")
        audio_codec_index = self._find_audio_codec_index(audio_codec_value)
        self.audio_codec_combo.set_selected(audio_codec_index)

        only_extract_subtitles_active = self.settings_manager.load_setting(
            "only-extract-subtitles", False
        )
        self.only_extract_subtitles_check.set_active(only_extract_subtitles_active)

    def _find_gpu_index(self, value):
        """Find index of GPU value in GPU_OPTIONS using reverse mapping"""
        value = value.lower()

        # Check for key words in the value
        if "nvenc" in value or "nvidia" in value:
            return 1
        elif "vaapi" in value or "amd" in value:
            return 2
        elif "qsv" in value or "intel" in value:
            return 3
        elif "vulkan" in value:
            return 4
        elif "software" in value:
            return 5
        elif value == "auto" or "auto-detect" in value:
            return 0

        # Reverse lookup in GPU_VALUES
        for index, internal_value in GPU_VALUES.items():
            if internal_value == value:
                return index

        # Default to Auto-detect
        return 0

    def _find_quality_index(self, value):
        """Find index of quality value using reverse mapping"""
        value = value.lower()

        # Reverse lookup in VIDEO_QUALITY_VALUES
        for index, internal_value in VIDEO_QUALITY_VALUES.items():
            if internal_value == value:
                return index

        # Default to Medium (index 3)
        return 3

    def _find_codec_index(self, value):
        """Find index of codec value using reverse mapping"""
        value = value.lower()

        # Reverse lookup in VIDEO_CODEC_VALUES
        for index, internal_value in VIDEO_CODEC_VALUES.items():
            if internal_value == value:
                return index

        # Default to H.264 (index 0)
        return 0

    def _find_preset_index(self, value):
        """Find index of preset value using reverse mapping"""
        value = value.lower()

        # Reverse lookup in PRESET_VALUES
        for index, internal_value in PRESET_VALUES.items():
            if internal_value == value:
                return index

        # Default to Medium (index 3)
        return 3

    def _find_audio_codec_index(self, value):
        """Find index of audio codec value using reverse mapping"""
        value = value.lower()

        # Reverse lookup in AUDIO_CODEC_VALUES
        for index, internal_value in AUDIO_CODEC_VALUES.items():
            if internal_value == value:
                return index

        # Default to AAC (index 0)
        return 0

    def _on_reset_button_clicked(self, button):
        """Handle reset settings button click"""
        print("DEBUG: Reset button clicked")
        # Show a confirmation dialog before resetting
        dialog = Gtk.AlertDialog()
        dialog.set_message(_("Reset All Settings?"))
        dialog.set_detail(
            _(
                "This will reset all settings to their default values. This action cannot be undone."
            )
        )
        dialog.set_buttons([_("Cancel"), _("Reset")])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)

        dialog.choose(self.app.window, None, self._on_reset_confirmation_response)
        print("DEBUG: Reset confirmation dialog shown")

    def _on_reset_confirmation_response(self, dialog, result):
        """Handle response from reset confirmation dialog"""
        try:
            response = dialog.choose_finish(result)
            print(f"DEBUG: Reset confirmation response: {response}")
            if response == 1:  # User clicked Reset
                print("DEBUG: User confirmed reset, calling _reset_all_settings()")
                # Reset all settings to defaults
                self._reset_all_settings()

                # Show a confirmation message
                success_dialog = Gtk.AlertDialog()
                success_dialog.set_message(_("Settings Reset"))
                success_dialog.set_detail(
                    _("All settings have been reset to their default values.")
                )
                success_dialog.show(self.app.window)
            else:
                print("DEBUG: User cancelled reset")
        except Exception as e:
            print(f"DEBUG: Error in reset confirmation: {e}")

    def _reset_all_settings(self):
        """Reset all settings to their default values"""
        # Get all default values from settings manager
        default_values = self.settings_manager.DEFAULT_VALUES

        # Reset each setting to its default value
        for key, value in default_values.items():
            print(f"Resetting {key} to {value}")

            # Use the appropriate setter method based on the value type
            if isinstance(value, bool):
                self.settings_manager.set_boolean(key, value)
            elif isinstance(value, int):
                self.settings_manager.set_int(key, value)
            elif isinstance(value, float):
                self.settings_manager.set_double(key, value)
            else:
                self.settings_manager.set_string(
                    key, str(value) if value is not None else ""
                )

        # Reload settings to update Advanced Settings UI
        self._load_settings()

        # Also reload sidebar settings in main window
        if hasattr(self.app, "_load_left_pane_settings"):
            self.app._load_left_pane_settings()

        # Update encoding options state based on reset force copy value
        force_copy = self.settings_manager.load_setting("force-copy-video", False)
        self.update_for_force_copy_state(force_copy)
        if hasattr(self.app, "_update_encoding_options_state"):
            self.app._update_encoding_options_state(force_copy)
