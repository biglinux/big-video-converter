import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from constants import (
    GPU_OPTIONS,
    VIDEO_QUALITY_OPTIONS,
    VIDEO_CODEC_OPTIONS,
    PRESET_OPTIONS,
    SUBTITLE_OPTIONS,
    AUDIO_OPTIONS,
)

# Setup translation
import gettext

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
        reset_button.add_css_class("destructive-action")  # Red to indicate a destructive action
        reset_button.add_css_class("pill")  # Rounded style
        reset_button.connect("clicked", self._on_reset_button_clicked)
        reset_box.append(reset_button)

        reset_group.add(reset_box)
        main_content.append(reset_group)


        return page

    def _create_encoding_settings(self, main_content):
        encoding_group = Adw.PreferencesGroup(title=_("Encoding Settings"))

        # GPU selection
        gpu_model = Gtk.StringList()
        for option in GPU_OPTIONS:
            gpu_model.append(option)
        self.gpu_combo = Adw.ComboRow(title=_("GPU"))
        self.gpu_combo.set_subtitle(_("Select hardware acceleration"))
        self.gpu_combo.set_model(gpu_model)
        self.gpu_combo.set_selected(0)
        encoding_group.add(self.gpu_combo)

        # Conversion mode switches
        self.gpu_partial_check = Adw.SwitchRow(title=_("GPU partial mode"))
        self.gpu_partial_check.set_subtitle(_("Decode using CPU, encode using GPU"))
        encoding_group.add(self.gpu_partial_check)

        # Video quality
        quality_model = Gtk.StringList()
        for option in VIDEO_QUALITY_OPTIONS:
            quality_model.append(option)
        self.video_quality_combo = Adw.ComboRow(title=_("Video quality"))
        self.video_quality_combo.set_subtitle(
            _("Higher quality needs more processing power")
        )
        self.video_quality_combo.set_model(quality_model)
        self.video_quality_combo.set_selected(0)
        encoding_group.add(self.video_quality_combo)

        # Video codec
        codec_model = Gtk.StringList()
        for option in VIDEO_CODEC_OPTIONS:
            codec_model.append(option)
        self.video_encoder_combo = Adw.ComboRow(title=_("Video codec"))
        self.video_encoder_combo.set_subtitle(_("Select encoding format"))
        self.video_encoder_combo.set_model(codec_model)
        self.video_encoder_combo.set_selected(0)
        encoding_group.add(self.video_encoder_combo)

        # Video resolution combo with common values
        resolution_model = Gtk.StringList()
        self.resolution_values = [
            _("Default"),
            "3840x2160",  # 4K UHD
            "2560x1440",  # 2K QHD
            "1920x1080",  # 1080p Full HD
            "1280x720",  # 720p HD
            "854x480",  # 480p SD
            _("Custom"),
        ]
        for option in self.resolution_values:
            resolution_model.append(option)

        self.video_resolution_combo = Adw.ComboRow(title=_("Video resolution"))
        self.video_resolution_combo.set_subtitle(_("Select output resolution"))
        self.video_resolution_combo.set_model(resolution_model)
        self.video_resolution_combo.set_selected(0)  # Default

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

        # Preset
        preset_model = Gtk.StringList()
        for option in PRESET_OPTIONS:
            preset_model.append(option)
        self.preset_combo = Adw.ComboRow(title=_("Compression preset"))
        self.preset_combo.set_subtitle(_("Slower presets provide better compression"))
        self.preset_combo.set_model(preset_model)
        self.preset_combo.set_selected(0)
        encoding_group.add(self.preset_combo)

        # Subtitles
        subtitle_model = Gtk.StringList()
        for option in SUBTITLE_OPTIONS:
            subtitle_model.append(option)
        self.subtitle_extract_combo = Adw.ComboRow(title=_("Subtitle handling"))
        self.subtitle_extract_combo.set_model(subtitle_model)
        self.subtitle_extract_combo.set_selected(0)
        encoding_group.add(self.subtitle_extract_combo)

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

        # Audio handling
        audio_model = Gtk.StringList()
        for option in AUDIO_OPTIONS:
            audio_model.append(option)
        self.audio_handling_combo = Adw.ComboRow(title=_("Audio handling"))
        self.audio_handling_combo.set_model(audio_model)
        self.audio_handling_combo.set_selected(0)
        audio_group.add(self.audio_handling_combo)

        main_content.append(audio_group)

    def _create_general_options(self, main_content):
        options_group = Adw.PreferencesGroup(title=_("General Options"))

        # Additional options
        self.options_entry = Adw.EntryRow(title=_("Additional FFmpeg options"))
        self.options_entry.set_tooltip_text(_("Ex: -ss 60 -t 30"))
        options_group.add(self.options_entry)

        self.force_copy_video_check = Adw.SwitchRow(
            title=_("Copy video without reencoding")
        )
        self.force_copy_video_check.set_subtitle(_("Faster but less compatible"))
        options_group.add(self.force_copy_video_check)

        self.only_extract_subtitles_check = Adw.SwitchRow(
            title=_("Only extract subtitles")
        )
        self.only_extract_subtitles_check.set_subtitle(
            _("Extract subtitles to .srt files")
        )
        options_group.add(self.only_extract_subtitles_check)

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

        # Show/hide custom entry based on selection
        if selected == len(self.resolution_values) - 1:  # Custom option
            self.custom_resolution_row.set_visible(True)

            # Use the custom value if it's not empty
            custom_value = self.custom_resolution_row.get_text()
            if custom_value:
                self.settings_manager.save_setting("video-resolution", custom_value)

        else:
            self.custom_resolution_row.set_visible(False)

            if selected == 0:  # Default (no resolution change)
                self.settings_manager.save_setting("video-resolution", "")
            else:
                # Save the selected standard resolution
                resolution = self.resolution_values[selected]
                self.settings_manager.save_setting("video-resolution", resolution)

    def _on_custom_resolution_changed(self, entry):
        """Save custom resolution when entry changes"""
        value = entry.get_text()
        if (
            value
            and self.video_resolution_combo.get_selected()
            == len(self.resolution_values) - 1
        ):
            self.settings_manager.save_setting("video-resolution", value)

    def _connect_setting_signals(self):
        """Connect signals for saving settings"""
        # Use direct value saving instead of indexes

        # GPU selection
        self.gpu_combo.connect(
            "notify::selected", lambda w, p: self._save_gpu_setting(w.get_selected())
        )

        # Video quality
        self.video_quality_combo.connect(
            "notify::selected",
            lambda w, p: self._save_quality_setting(w.get_selected()),
        )

        # Video codec
        self.video_encoder_combo.connect(
            "notify::selected", lambda w, p: self._save_codec_setting(w.get_selected())
        )

        # Preset
        self.preset_combo.connect(
            "notify::selected", lambda w, p: self._save_preset_setting(w.get_selected())
        )

        # Subtitle extract
        self.subtitle_extract_combo.connect(
            "notify::selected",
            lambda w, p: self._save_subtitle_setting(w.get_selected()),
        )

        # Audio handling - directly saves string value
        self.audio_handling_combo.connect(
            "notify::selected",
            lambda w, p: self.settings_manager.save_setting(
                "audio-handling", AUDIO_OPTIONS[w.get_selected()]
            ),
        )

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
        self.force_copy_video_check.connect(
            "notify::active",
            lambda w, p: self.settings_manager.save_setting(
                "force-copy-video", w.get_active()
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

    def _save_gpu_setting(self, index):
        """Save GPU setting as direct value"""
        # Map index to GPU value and save directly
        if index == 0:  # Default/Auto
            self.settings_manager.save_setting("gpu", "auto")
        elif index == 1:  # nvidia
            self.settings_manager.save_setting("gpu", "nvidia")
        elif index == 2:  # amd
            self.settings_manager.save_setting("gpu", "amd")
        elif index == 3:  # intel
            self.settings_manager.save_setting("gpu", "intel")
        elif index == 4:  # software
            self.settings_manager.save_setting("gpu", "software")

    def _save_quality_setting(self, index):
        """Save video quality setting as direct value"""
        # Map index to quality value and save directly
        if index == 0:  # Default
            self.settings_manager.save_setting("video-quality", "default")
        elif index == 1:  # veryhigh
            self.settings_manager.save_setting("video-quality", "veryhigh")
        elif index == 2:  # high
            self.settings_manager.save_setting("video-quality", "high")
        elif index == 3:  # medium
            self.settings_manager.save_setting("video-quality", "medium")
        elif index == 4:  # low
            self.settings_manager.save_setting("video-quality", "low")
        elif index == 5:  # verylow
            self.settings_manager.save_setting("video-quality", "verylow")
        elif index == 6:  # superlow
            self.settings_manager.save_setting("video-quality", "superlow")

    def _save_codec_setting(self, index):
        """Save video codec setting as direct value"""
        # Map index to codec value and save directly
        if index == 0:  # Default (h264)
            self.settings_manager.save_setting("video-codec", "h264")
        elif index == 1:  # h265 (HEVC)
            self.settings_manager.save_setting("video-codec", "h265")
        elif index == 2:  # av1 (AV1)
            self.settings_manager.save_setting("video-codec", "av1")
        elif index == 3:  # vp9 (VP9)
            self.settings_manager.save_setting("video-codec", "vp9")

    def _save_preset_setting(self, index):
        """Save preset setting as direct value"""
        # Map index to preset value and save directly
        if index == 0:  # Default
            self.settings_manager.save_setting("preset", "default")
        elif index == 1:  # ultrafast
            self.settings_manager.save_setting("preset", "ultrafast")
        elif index == 2:  # veryfast
            self.settings_manager.save_setting("preset", "veryfast")
        elif index == 3:  # faster
            self.settings_manager.save_setting("preset", "faster")
        elif index == 4:  # medium
            self.settings_manager.save_setting("preset", "medium")
        elif index == 5:  # slow
            self.settings_manager.save_setting("preset", "slow")
        elif index == 6:  # veryslow
            self.settings_manager.save_setting("preset", "veryslow")

    def _save_subtitle_setting(self, index):
        """Save subtitle setting as direct value"""
        # Map index to subtitle handling value and save directly
        if index == 0:  # Default (extract)
            self.settings_manager.save_setting("subtitle-extract", "extract")
        elif index == 1:  # embedded
            self.settings_manager.save_setting("subtitle-extract", "embedded")
        elif index == 2:  # none
            self.settings_manager.save_setting("subtitle-extract", "none")

    def _load_settings(self):
        """Load settings and update UI components"""
        # GPU selection
        gpu_value = self.settings_manager.load_setting("gpu", "auto")
        gpu_index = self._find_gpu_index(gpu_value)
        self.gpu_combo.set_selected(gpu_index)

        # Video quality
        quality_value = self.settings_manager.load_setting("video-quality", "medium")
        quality_index = self._find_quality_index(quality_value)
        self.video_quality_combo.set_selected(quality_index)

        # Video codec
        codec_value = self.settings_manager.load_setting("video-codec", "h264")
        codec_index = self._find_codec_index(codec_value)
        self.video_encoder_combo.set_selected(codec_index)

        # Preset
        preset_value = self.settings_manager.load_setting("preset", "medium")
        preset_index = self._find_preset_index(preset_value)
        self.preset_combo.set_selected(preset_index)

        # Subtitle extraction
        subtitle_value = self.settings_manager.load_setting(
            "subtitle-extract", "extract"
        )
        subtitle_index = self._find_subtitle_index(subtitle_value)
        self.subtitle_extract_combo.set_selected(subtitle_index)

        # Load video resolution setting
        saved_resolution = self.settings_manager.load_setting("video-resolution", "")

        if saved_resolution:
            # Check if it's one of the standard resolutions
            standard_index = -1
            for i, res in enumerate(self.resolution_values):
                if res == saved_resolution:
                    standard_index = i
                    break

            if standard_index >= 0:
                # Standard resolution found
                self.video_resolution_combo.set_selected(standard_index)
                self.custom_resolution_row.set_visible(False)
            else:
                # Must be a custom resolution
                self.video_resolution_combo.set_selected(
                    len(self.resolution_values) - 1
                )  # Custom
                self.custom_resolution_row.set_text(saved_resolution)
                self.custom_resolution_row.set_visible(True)
        else:
            # No saved resolution, use default
            self.video_resolution_combo.set_selected(0)
            self.custom_resolution_row.set_visible(False)
        
        # Audio handling
        audio_value = self.settings_manager.load_setting("audio-handling", "copy")
        audio_index = 0  # Default to "copy"
        for i, option in enumerate(AUDIO_OPTIONS):
            if option.lower() == audio_value.lower():
                audio_index = i
                break
        self.audio_handling_combo.set_selected(audio_index)

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
                self.audio_bitrate_combo.set_selected(len(self.bitrate_values) - 1)  # Custom
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
                self.audio_channels_combo.set_selected(len(self.channels_values) - 1)  # Custom
                self.custom_channels_row.set_text(channels_value)
                self.custom_channels_row.set_visible(True)
        else:
            # No saved channels, use default
            self.audio_channels_combo.set_selected(0)
            self.custom_channels_row.set_visible(False)

    def _find_gpu_index(self, value):
        """Find index of GPU value in GPU_OPTIONS"""
        value = value.lower()
        for i, option in enumerate(GPU_OPTIONS):
            if option.lower() == value or (i == 0 and value == "auto"):
                return i
        return 0  # Default to Auto-detect

    def _find_quality_index(self, value):
        """Find index of quality value in VIDEO_QUALITY_OPTIONS"""
        value = value.lower()
        for i, option in enumerate(VIDEO_QUALITY_OPTIONS):
            if option.lower() == value or (i == 0 and value == "default"):
                return i
        return 0  # Default to Default

    def _find_codec_index(self, value):
        """Find index of codec value in VIDEO_CODEC_OPTIONS"""
        value = value.lower()
        # Handle special cases
        if value == "h264":
            return 0  # h264 (MP4)
        elif value == "h265":
            return 1  # h265 (HEVC)
        elif value == "av1":
            return 2  # av1 (AV1)
        elif value == "vp9":
            return 3  # vp9 (VP9)
        return 0  # Default to Default (h264)

    def _find_preset_index(self, value):
        """Find index of preset value in PRESET_OPTIONS"""
        value = value.lower()
        for i, option in enumerate(PRESET_OPTIONS):
            if option.lower() == value or (i == 0 and value == "default"):
                return i
        return 0  # Default to Default

    def _find_subtitle_index(self, value):
        """Find index of subtitle value in SUBTITLE_OPTIONS"""
        value = value.lower()
        if value == "extract":
            return 0  # Default (extract)
        elif value == "embedded":
            return 1  # embedded
        elif value == "none":
            return 2  # none
        return 0  # Default to Default (extract)

    def _on_reset_button_clicked(self, button):
        """Handle reset settings button click"""
        # Show a confirmation dialog before resetting
        dialog = Gtk.AlertDialog.new(_("Reset All Settings?"))
        dialog.set_detail(
            _("This will reset all settings to their default values. This action cannot be undone.")
        )
        dialog.set_buttons(["Cancel", "Reset"])
        dialog.set_cancel_button(0)  # First button (Cancel) is the cancel button
        dialog.set_default_button(0)  # Cancel is the default button
        
        dialog.choose(
            self.app.window,
            None,
            self._on_reset_confirmation_response
        )

    def _on_reset_confirmation_response(self, dialog, response):
        """Handle response from reset confirmation dialog"""
        if response == 1:  # User clicked Reset
            # Reset all settings to defaults
            self._reset_all_settings()
            
            # Show a confirmation message
            success_dialog = Gtk.AlertDialog.new(_("Settings Reset"))
            success_dialog.set_detail(_("All settings have been reset to their default values."))
            success_dialog.show(self.app.window)

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
                self.settings_manager.set_string(key, str(value) if value is not None else "")
        
        # Reload settings to update UI
        self._load_settings()