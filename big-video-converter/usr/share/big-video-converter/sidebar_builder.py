"""Sidebar builder mixin — left pane UI creation, signals, and settings loading."""

import gettext
import logging
import os

from gi.repository import Adw, Gtk

from constants import (
    AUDIO_VALUES,
    GPU_VALUES,
    SUBTITLE_VALUES,
    VIDEO_CODEC_VALUES,
    VIDEO_QUALITY_VALUES,
)

_ = gettext.gettext

logger = logging.getLogger(__name__)


class SidebarBuilderMixin:
    """Mixin providing sidebar (left pane) construction, signal wiring, and settings loading."""

    def _create_left_pane(self):
        """Create left settings pane with contextual ViewStack"""
        # Create ToolbarView for left pane to get the correct sidebar style
        left_toolbar_view = Adw.ToolbarView()
        left_toolbar_view.add_css_class("sidebar")

        # Detect window button layout
        window_buttons_left = self._window_buttons_on_left()

        # Create a HeaderBar for the left pane
        left_header = Adw.HeaderBar()
        left_header.add_css_class("sidebar")
        left_header.set_show_title(True)
        # Configure left header bar based on window button layout
        left_header.set_decoration_layout(
            "close,maximize,minimize:menu" if window_buttons_left else ""
        )

        # Create title box with label and (optionally) app icon
        if not window_buttons_left:
            # Text truly centered, no icon
            center_box = Gtk.CenterBox()
            center_box.set_hexpand(True)
            title_label = Gtk.Label(label="Big Video Converter")
            title_label.set_halign(Gtk.Align.CENTER)
            title_label.set_valign(Gtk.Align.START)
            title_label.set_hexpand(True)
            center_box.set_center_widget(title_label)
            left_header.set_title_widget(center_box)
        else:
            title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title_label = Gtk.Label(label="Big Video Converter")
            title_box.append(title_label)
            # Add an expanding box to push controls to the left
            expander = Gtk.Box()
            expander.set_hexpand(True)
            title_box.append(expander)
            left_header.set_title_widget(title_box)
        left_toolbar_view.add_top_bar(left_header)

        # Create scrolled window for content
        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroll.set_min_content_width(300)
        left_scroll.set_max_content_width(400)

        # Create ViewStack for contextual content
        self.left_stack = Adw.ViewStack()

        # Page 1: Conversion Settings
        conversion_settings = self._create_conversion_settings()
        self.left_stack.add_titled(
            conversion_settings, "conversion_settings", _("Conversion")
        )

        # Page 2: Editing Tools (This is now a container to be populated later)
        self.editing_tools_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        self.left_stack.add_titled(
            self.editing_tools_box, "editing_tools", _("Editing")
        )

        left_scroll.set_child(self.left_stack)
        left_toolbar_view.set_content(left_scroll)

        # Set minimum width for left sidebar
        left_toolbar_view.set_size_request(300, -1)

        self.main_paned.set_start_child(left_toolbar_view)

    def _create_conversion_settings(self):
        """Create conversion settings sidebar with ActionRows that open dialogs."""
        from constants import (
            AUDIO_OPTIONS,
            GPU_OPTIONS,
            SUBTITLE_OPTIONS,
            VIDEO_CODEC_OPTIONS,
            VIDEO_QUALITY_OPTIONS,
        )

        settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        settings_box.set_spacing(24)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)
        settings_box.set_margin_top(12)
        settings_box.set_margin_bottom(24)

        # ── Create all data-holder widgets (not added to sidebar) ──

        # Video Codec
        codec_model = Gtk.StringList()
        for option in VIDEO_CODEC_OPTIONS:
            codec_model.append(option)
        self.video_codec_combo = Adw.ComboRow(title=_("Video Codec"))
        self.video_codec_combo.set_model(codec_model)

        # Video Quality
        quality_model = Gtk.StringList()
        for option in VIDEO_QUALITY_OPTIONS:
            quality_model.append(option)
        self.video_quality_combo = Adw.ComboRow(title=_("Image Quality"))
        self.video_quality_combo.set_model(quality_model)

        # GPU
        gpu_model = Gtk.StringList()
        for option in GPU_OPTIONS:
            gpu_model.append(option)
        self.gpu_combo = Adw.ComboRow(title=_("GPU"))
        self.gpu_combo.set_subtitle(_("Speeds up conversion if supported"))
        self.gpu_combo.set_model(gpu_model)

        # GPU Device
        self.detected_gpus = self._detect_gpu_devices()
        self.gpu_device_combo = Adw.ComboRow(title=_("GPU Device"))
        self.gpu_device_combo.set_subtitle(_("Select which GPU to use"))
        gpu_device_model = Gtk.StringList()
        gpu_device_model.append(_("Auto"))
        for gpu_info in self.detected_gpus:
            gpu_device_model.append(gpu_info["name"])
        self.gpu_device_combo.set_model(gpu_device_model)

        # Output Format
        format_model = Gtk.StringList()
        for fmt in ["MP4", "MKV", "MOV", "WebM"]:
            format_model.append(fmt)
        self.output_format_combo = Adw.ComboRow(title=_("Output Format"))
        self.output_format_combo.set_subtitle(_("Container format for output file"))
        self.output_format_combo.set_model(format_model)

        # Hidden data-holder for force copy state (driven by codec combo index 0)
        self.force_copy_video_check = Adw.SwitchRow(
            title=_("Copy without re-encoding")
        )

        # Audio Handling
        audio_model = Gtk.StringList()
        for option in AUDIO_OPTIONS:
            audio_model.append(option)
        self.audio_handling_combo = Adw.ComboRow(title=_("Audio"))
        self.audio_handling_combo.set_model(audio_model)

        # Subtitles
        subtitle_model = Gtk.StringList()
        for option in SUBTITLE_OPTIONS:
            subtitle_model.append(option)
        self.subtitle_combo = Adw.ComboRow(title=_("Subtitles"))
        self.subtitle_combo.set_model(subtitle_model)

        # Audio Noise Reduction (hidden expander — holds all sub-controls)
        self.noise_reduction_expander = Adw.ExpanderRow(title=_("Noise Cleaning (AI)"))
        self.noise_reduction_expander.set_subtitle(_("Remove background noise"))
        self.noise_reduction_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.noise_reduction_switch.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Enable noise reduction")],
        )
        self.noise_reduction_switch.connect("state-set", self._on_noise_reduction_toggled)
        self.noise_reduction_expander.add_suffix(self.noise_reduction_switch)
        self.noise_reduction_expander.set_enable_expansion(False)
        self.noise_reduction_expander.set_expanded(False)

        # Noise Reduction Strength (visible only when noise reduction is enabled)
        self.noise_strength_row = Adw.ActionRow(title=_("Strength"))
        self.noise_strength_adj = Gtk.Adjustment(value=1.0, lower=0.0, upper=1.0, step_increment=0.05, page_increment=0.1)
        self.noise_strength_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.noise_strength_adj)
        self.noise_strength_scale.set_digits(2)
        self.noise_strength_scale.set_hexpand(True)
        self.noise_strength_scale.set_size_request(200, -1)
        self.noise_strength_scale.set_valign(Gtk.Align.CENTER)
        self.noise_strength_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, _("0%"))
        self.noise_strength_scale.add_mark(0.5, Gtk.PositionType.BOTTOM, _("50%"))
        self.noise_strength_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, _("100%"))
        self._noise_strength_label = Gtk.Label(label="100%")
        self._noise_strength_label.set_size_request(45, -1)
        self._noise_strength_label.add_css_class("numeric")
        self._noise_strength_label.add_css_class("caption")
        self.noise_strength_adj.connect("notify::value", lambda a, _: self._noise_strength_label.set_text(f"{a.get_value()*100:.0f}%"))
        self.noise_strength_row.add_suffix(self.noise_strength_scale)
        self.noise_strength_row.add_suffix(self._noise_strength_label)
        self.noise_reduction_expander.add_row(self.noise_strength_row)
        self.tooltip_helper.add_tooltip(
            self.noise_strength_row, "noise_reduction_strength"
        )

        # GTCRN Advanced Controls
        self._noise_model_list = [
            _("Maximum Cleaning"),
            _("Natural Voice"),
            _("Smart (both combined)"),
        ]
        noise_model_model = Gtk.StringList.new(self._noise_model_list)
        self.noise_model_row = Adw.ComboRow(title=_("AI Model"), model=noise_model_model)
        self.noise_model_row.set_selected(0)
        self.noise_reduction_expander.add_row(self.noise_model_row)

        self.noise_speech_strength_row = Adw.ActionRow(title=_("Speech Strength"))
        self.noise_speech_strength_adj = Gtk.Adjustment(value=1.0, lower=0.0, upper=1.0, step_increment=0.05, page_increment=0.1)
        self.noise_speech_strength_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.noise_speech_strength_adj)
        self.noise_speech_strength_scale.set_digits(2)
        self.noise_speech_strength_scale.set_hexpand(True)
        self.noise_speech_strength_scale.set_size_request(200, -1)
        self.noise_speech_strength_scale.set_valign(Gtk.Align.CENTER)
        self.noise_speech_strength_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, _("0%"))
        self.noise_speech_strength_scale.add_mark(0.5, Gtk.PositionType.BOTTOM, _("50%"))
        self.noise_speech_strength_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, _("100%"))
        self._speech_strength_label = Gtk.Label(label="100%")
        self._speech_strength_label.set_size_request(45, -1)
        self._speech_strength_label.add_css_class("numeric")
        self._speech_strength_label.add_css_class("caption")
        self.noise_speech_strength_adj.connect("notify::value", lambda a, _: self._speech_strength_label.set_text(f"{a.get_value()*100:.0f}%"))
        self.noise_speech_strength_row.add_suffix(self.noise_speech_strength_scale)
        self.noise_speech_strength_row.add_suffix(self._speech_strength_label)
        self.noise_reduction_expander.add_row(self.noise_speech_strength_row)

        self.noise_lookahead_row = Adw.ActionRow(title=_("Lookahead"))
        self.noise_lookahead_adj = Gtk.Adjustment(value=0, lower=0, upper=200, step_increment=5, page_increment=20)
        self.noise_lookahead_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.noise_lookahead_adj)
        self.noise_lookahead_scale.set_digits(0)
        self.noise_lookahead_scale.set_hexpand(True)
        self.noise_lookahead_scale.set_size_request(200, -1)
        self.noise_lookahead_scale.set_valign(Gtk.Align.CENTER)
        self.noise_lookahead_scale.add_mark(0, Gtk.PositionType.BOTTOM, "0")
        self.noise_lookahead_scale.add_mark(50, Gtk.PositionType.BOTTOM, "50")
        self.noise_lookahead_scale.add_mark(100, Gtk.PositionType.BOTTOM, "100")
        self.noise_lookahead_scale.add_mark(200, Gtk.PositionType.BOTTOM, "200")
        self._lookahead_label = Gtk.Label(label="0 ms")
        self._lookahead_label.set_size_request(55, -1)
        self._lookahead_label.add_css_class("numeric")
        self._lookahead_label.add_css_class("caption")
        self.noise_lookahead_adj.connect("notify::value", lambda a, _: self._lookahead_label.set_text(f"{a.get_value():.0f} ms"))
        self.noise_lookahead_row.add_suffix(self.noise_lookahead_scale)
        self.noise_lookahead_row.add_suffix(self._lookahead_label)
        self.noise_reduction_expander.add_row(self.noise_lookahead_row)

        self.noise_voice_recovery_row = Adw.ActionRow(title=_("Voice Recovery"))
        self.noise_voice_recovery_adj = Gtk.Adjustment(value=0.75, lower=0.0, upper=1.0, step_increment=0.05, page_increment=0.1)
        self.noise_voice_recovery_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.noise_voice_recovery_adj)
        self.noise_voice_recovery_scale.set_digits(2)
        self.noise_voice_recovery_scale.set_hexpand(True)
        self.noise_voice_recovery_scale.set_size_request(200, -1)
        self.noise_voice_recovery_scale.set_valign(Gtk.Align.CENTER)
        self.noise_voice_recovery_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, _("0%"))
        self.noise_voice_recovery_scale.add_mark(0.5, Gtk.PositionType.BOTTOM, _("50%"))
        self.noise_voice_recovery_scale.add_mark(0.75, Gtk.PositionType.BOTTOM, _("75%"))
        self.noise_voice_recovery_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, _("100%"))
        self._voice_recovery_label = Gtk.Label(label="75%")
        self._voice_recovery_label.set_size_request(45, -1)
        self._voice_recovery_label.add_css_class("numeric")
        self._voice_recovery_label.add_css_class("caption")
        self.noise_voice_recovery_adj.connect("notify::value", lambda a, _: self._voice_recovery_label.set_text(f"{a.get_value()*100:.0f}%"))
        self.noise_voice_recovery_row.add_suffix(self.noise_voice_recovery_scale)
        self.noise_voice_recovery_row.add_suffix(self._voice_recovery_label)
        self.noise_reduction_expander.add_row(self.noise_voice_recovery_row)

        # Noise Gate - simplified with intensity slider
        self.gate_expander = Adw.ExpanderRow(title=_("Noise Gate"))
        self.gate_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.gate_switch.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Noise Gate")],
        )
        self.gate_switch.connect("state-set", self._on_gate_switch_changed)
        self.gate_expander.add_suffix(self.gate_switch)
        self.gate_expander.set_enable_expansion(False)
        self.gate_expander.set_expanded(False)
        self.tooltip_helper.add_tooltip(self.gate_expander, "noise_gate")

        self.gate_intensity_row = Adw.ActionRow(title=_("Intensity"))
        self.gate_intensity_adj = Gtk.Adjustment(value=0.5, lower=0.0, upper=1.0, step_increment=0.05, page_increment=0.1)
        self.gate_intensity_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.gate_intensity_adj)
        self.gate_intensity_scale.set_digits(2)
        self.gate_intensity_scale.set_hexpand(True)
        self.gate_intensity_scale.set_size_request(200, -1)
        self.gate_intensity_scale.set_valign(Gtk.Align.CENTER)
        self.gate_intensity_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, _("0%"))
        self.gate_intensity_scale.add_mark(0.5, Gtk.PositionType.BOTTOM, _("50%"))
        self.gate_intensity_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, _("100%"))
        self.gate_intensity_scale.set_sensitive(False)
        self._gate_intensity_label = Gtk.Label(label="50%")
        self._gate_intensity_label.set_size_request(45, -1)
        self._gate_intensity_label.add_css_class("numeric")
        self._gate_intensity_label.add_css_class("caption")
        self.gate_intensity_adj.connect("notify::value", lambda a, _: self._gate_intensity_label.set_text(f"{a.get_value()*100:.0f}%"))
        self.gate_intensity_row.add_suffix(self.gate_intensity_scale)
        self.gate_intensity_row.add_suffix(self._gate_intensity_label)
        self.gate_expander.add_row(self.gate_intensity_row)

        self.noise_reduction_expander.add_row(self.gate_expander)

        # Compressor
        self.compressor_expander = Adw.ExpanderRow(title=_("Compressor"))
        self.compressor_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.compressor_switch.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Enable compressor")],
        )
        self.compressor_switch.connect("state-set", self._on_compressor_switch_changed)
        self.compressor_expander.add_suffix(self.compressor_switch)
        self.compressor_expander.set_enable_expansion(False)
        self.compressor_expander.set_expanded(False)

        self.compressor_intensity_row = Adw.ActionRow(title=_("Intensity"))
        self.compressor_intensity_adj = Gtk.Adjustment(value=1.0, lower=0.0, upper=1.0, step_increment=0.05, page_increment=0.1)
        self.compressor_intensity_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.compressor_intensity_adj)
        self.compressor_intensity_scale.set_digits(2)
        self.compressor_intensity_scale.set_hexpand(True)
        self.compressor_intensity_scale.set_size_request(200, -1)
        self.compressor_intensity_scale.set_valign(Gtk.Align.CENTER)
        self.compressor_intensity_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, _("0%"))
        self.compressor_intensity_scale.add_mark(0.5, Gtk.PositionType.BOTTOM, _("50%"))
        self.compressor_intensity_scale.add_mark(1.0, Gtk.PositionType.BOTTOM, _("100%"))
        self.compressor_intensity_scale.set_sensitive(False)
        self._compressor_label = Gtk.Label(label="100%")
        self._compressor_label.set_size_request(45, -1)
        self._compressor_label.add_css_class("numeric")
        self._compressor_label.add_css_class("caption")
        self.compressor_intensity_adj.connect("notify::value", lambda a, _: self._compressor_label.set_text(f"{a.get_value()*100:.0f}%"))
        self.compressor_intensity_row.add_suffix(self.compressor_intensity_scale)
        self.compressor_intensity_row.add_suffix(self._compressor_label)
        self.compressor_expander.add_row(self.compressor_intensity_row)

        self.noise_reduction_expander.add_row(self.compressor_expander)

        # High-pass filter
        self.hpf_row = Adw.SwitchRow(title=_("Remove Low Rumble"))
        self.hpf_row.set_subtitle(_("Cuts low-frequency noise like wind or AC hum"))
        self.hpf_row.set_active(False)
        self.noise_reduction_expander.add_row(self.hpf_row)

        self.hpf_freq_row = Adw.ActionRow(title=_("Frequency"))
        self.hpf_freq_adj = Gtk.Adjustment(value=80, lower=20, upper=500, step_increment=5, page_increment=20)
        self.hpf_freq_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.hpf_freq_adj)
        self.hpf_freq_scale.set_digits(0)
        self.hpf_freq_scale.set_hexpand(True)
        self.hpf_freq_scale.set_size_request(200, -1)
        self.hpf_freq_scale.set_valign(Gtk.Align.CENTER)
        self.hpf_freq_scale.add_mark(20, Gtk.PositionType.BOTTOM, "20")
        self.hpf_freq_scale.add_mark(80, Gtk.PositionType.BOTTOM, "80")
        self.hpf_freq_scale.add_mark(200, Gtk.PositionType.BOTTOM, "200")
        self.hpf_freq_scale.add_mark(500, Gtk.PositionType.BOTTOM, "500")
        self._hpf_label = Gtk.Label(label="80 Hz")
        self._hpf_label.set_size_request(55, -1)
        self._hpf_label.add_css_class("numeric")
        self._hpf_label.add_css_class("caption")
        self.hpf_freq_adj.connect("notify::value", lambda a, _: self._hpf_label.set_text(f"{a.get_value():.0f} Hz"))
        self.hpf_freq_row.add_suffix(self.hpf_freq_scale)
        self.hpf_freq_row.add_suffix(self._hpf_label)
        self.hpf_freq_row.set_visible(False)
        self.noise_reduction_expander.add_row(self.hpf_freq_row)

        # EQ
        self.eq_expander = Adw.ExpanderRow(title=_("Equalizer"))
        self.eq_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.eq_switch.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Enable equalizer")],
        )
        self.eq_switch.connect("state-set", self._on_eq_switch_changed)
        self.eq_expander.add_suffix(self.eq_switch)
        self.eq_expander.set_enable_expansion(False)
        self.eq_expander.set_expanded(False)

        self._eq_preset_keys = [
            "default_voice", "flat", "voice_boost", "podcast", "warm",
            "bright", "de_esser", "bass_cut", "presence", "custom",
        ]
        self._eq_presets = {
            "default_voice": [0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 2.0, 3.0, 1.0, 0.0],
            "flat": [0.0] * 10,
            "voice_boost": [-10.0, -5.0, 0.0, 5.0, 15.0, 20.0, 15.0, 10.0, 5.0, 0.0],
            "podcast": [5.0, 5.0, 10.0, 5.0, 0.0, 5.0, 10.0, 5.0, 0.0, -5.0],
            "warm": [10.0, 15.0, 10.0, 5.0, 0.0, -5.0, -10.0, -15.0, -15.0, -20.0],
            "bright": [-10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 20.0, 15.0],
            "de_esser": [0.0, 0.0, 0.0, 0.0, 0.0, -5.0, -15.0, -25.0, -20.0, -10.0],
            "bass_cut": [-40.0, -35.0, -25.0, -15.0, -5.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "presence": [-5.0, 0.0, 5.0, 10.0, 15.0, 20.0, 15.0, 10.0, 5.0, 0.0],
            "custom": [0.0] * 10,
        }
        _eq_preset_names = [
            _("Default Voice"), _("Natural (No Effects)"), _("Crystal Voice"),
            _("Radio Host"), _("Velvet Voice"), _("Extra Brightness"),
            _("Soften 'S' (De-esser)"), _("Remove Rumble"), _("Present Voice"), _("Custom"),
        ]
        eq_preset_model = Gtk.StringList.new(_eq_preset_names)
        self.eq_preset_row = Adw.ComboRow(title=_("Preset"), model=eq_preset_model)
        self.eq_preset_row.set_selected(1)  # flat
        self.eq_preset_row.set_sensitive(False)
        self.eq_preset_row.connect("notify::selected", self._on_eq_preset_changed)
        self.eq_expander.add_row(self.eq_preset_row)

        self.noise_reduction_expander.add_row(self.eq_expander)

        # Loudness Normalization (last in chain)
        self.normalize_row = Adw.SwitchRow(title=_("Uniform Volume"))
        self.normalize_row.set_subtitle(_("Normalizes volume to TV/streaming standard"))
        self.normalize_row.set_active(False)
        self.normalize_row.connect("notify::active", self._on_normalize_toggled)
        self.noise_reduction_expander.add_row(self.normalize_row)

        # ── Group 1: Video Mode Profiles ──
        video_group = Adw.PreferencesGroup()

        self._radio_copy = Gtk.CheckButton()
        self._radio_universal = Gtk.CheckButton(group=self._radio_copy)
        self._radio_smaller = Gtk.CheckButton(group=self._radio_copy)
        self._radio_quality = Gtk.CheckButton(group=self._radio_copy)
        self._radio_custom = Gtk.CheckButton(group=self._radio_copy)

        self._profile_guard = False  # Prevent recursive signal loops

        self._copy_row = Adw.ActionRow(title=_("Fast Copy"))
        self._copy_row.set_subtitle(_("No re-encoding, fastest"))
        self._copy_row.add_prefix(self._radio_copy)
        self._copy_row.set_activatable_widget(self._radio_copy)
        video_group.add(self._copy_row)
        self.tooltip_helper.add_tooltip(self._copy_row, "profile_copy")

        self._universal_row = Adw.ActionRow(title=_("Universal"))
        self._universal_row.set_subtitle(_("H.264, compatible with all devices"))
        self._universal_row.add_prefix(self._radio_universal)
        self._universal_row.set_activatable_widget(self._radio_universal)
        # Visual badge indicating recommended profile
        recommended_label = Gtk.Label(label=_("Recommended"))
        recommended_label.add_css_class("caption")
        recommended_label.add_css_class("success")
        recommended_label.set_valign(Gtk.Align.CENTER)
        self._universal_row.add_suffix(recommended_label)
        video_group.add(self._universal_row)
        self.tooltip_helper.add_tooltip(self._universal_row, "profile_universal")

        self._efficient_row = Adw.ActionRow(title=_("More Efficient"))
        self._efficient_row.set_subtitle(_("H.265, better compression"))
        self._efficient_row.add_prefix(self._radio_smaller)
        self._efficient_row.set_activatable_widget(self._radio_smaller)
        video_group.add(self._efficient_row)
        self.tooltip_helper.add_tooltip(self._efficient_row, "profile_efficient")

        self._smallest_row = Adw.ActionRow(title=_("Smaller File"))
        self._smallest_row.set_subtitle(_("AV1, best compression"))
        self._smallest_row.add_prefix(self._radio_quality)
        self._smallest_row.set_activatable_widget(self._radio_quality)
        video_group.add(self._smallest_row)
        self.tooltip_helper.add_tooltip(self._smallest_row, "profile_smaller")

        self._customize_row = Adw.ActionRow(title=_("Customize encoding..."))
        self._customize_row.add_prefix(self._radio_custom)
        self._customize_row.set_activatable_widget(self._radio_custom)
        self._customize_row.add_suffix(
            Gtk.Image.new_from_icon_name("go-next-symbolic")
        )
        self._customize_row.set_activatable(True)
        self._customize_row.connect("activated", self._on_video_encoding_activated)
        video_group.add(self._customize_row)

        self._radio_copy.connect("toggled", self._on_profile_toggled)
        self._radio_universal.connect("toggled", self._on_profile_toggled)
        self._radio_smaller.connect("toggled", self._on_profile_toggled)
        self._radio_quality.connect("toggled", self._on_profile_toggled)

        settings_box.append(video_group)

        # ── Group 2: Audio / Subtitles / Extra ──
        other_group = Adw.PreferencesGroup()

        self._audio_row = Adw.ActionRow(title=_("Audio Encoding"))
        self._audio_row.add_prefix(
            Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
        )
        self._audio_row.add_suffix(
            Gtk.Image.new_from_icon_name("go-next-symbolic")
        )
        self._audio_row.set_activatable(True)
        self._audio_row.connect("activated", self._on_audio_activated)
        other_group.add(self._audio_row)

        self._audio_cleaning_row = Adw.ActionRow(title=_("Audio Settings"))
        self._audio_cleaning_row.add_prefix(
            Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        )
        self._audio_cleaning_row.add_suffix(
            Gtk.Image.new_from_icon_name("go-next-symbolic")
        )
        self._audio_cleaning_row.set_activatable(True)
        self._audio_cleaning_row.connect(
            "activated", self._on_noise_options_activated
        )
        other_group.add(self._audio_cleaning_row)

        self._subtitles_row = Adw.ActionRow(title=_("Subtitles"))
        self._subtitles_row.add_prefix(
            Gtk.Image.new_from_icon_name("insert-text-symbolic")
        )
        self._subtitles_row.add_suffix(
            Gtk.Image.new_from_icon_name("go-next-symbolic")
        )
        self._subtitles_row.set_activatable(True)
        self._subtitles_row.connect("activated", self._on_subtitles_activated)
        other_group.add(self._subtitles_row)

        self._extra_row = Adw.ActionRow(title=_("Extra"))
        self._extra_row.add_prefix(
            Gtk.Image.new_from_icon_name("preferences-system-symbolic")
        )
        self._extra_row.add_suffix(
            Gtk.Image.new_from_icon_name("go-next-symbolic")
        )
        self._extra_row.set_activatable(True)
        self._extra_row.connect("activated", self._on_extra_activated)
        other_group.add(self._extra_row)

        settings_box.append(other_group)

        # Initialize settings page (data holder for advanced widgets)
        from ui.settings_page import SettingsPage

        self.settings_page = SettingsPage(self)
        self._update_sidebar_for_extract_mode()

        # Connect signals and load settings
        self._connect_left_pane_signals()
        self._load_left_pane_settings()

        return settings_box

    def _connect_left_pane_signals(self):
        """Connect signals for left pane settings controls"""
        # GPU
        self.gpu_combo.connect(
            "notify::selected",
            lambda w, p: (self._save_gpu_setting(w.get_selected()), self._update_customize_subtitle()),
        )

        # GPU Device
        self.gpu_device_combo.connect(
            "notify::selected",
            lambda w, p: (
                self.settings_manager.save_setting("gpu-device-index", w.get_selected()),
                self._update_customize_subtitle(),
            ),
        )

        # Output Format — save, sync with settings_page, update subtitle
        def _on_sidebar_format_changed(w, p):
            idx = w.get_selected()
            self.settings_manager.save_setting("output-format-index", idx)
            if hasattr(self, "settings_page") and self.settings_page.output_format_combo.get_selected() != idx:
                self.settings_page.output_format_combo.set_selected(idx)
            self._update_customize_subtitle()

        self.output_format_combo.connect("notify::selected", _on_sidebar_format_changed)

        # Video Quality
        self.video_quality_combo.connect(
            "notify::selected",
            lambda w, p: (self._save_quality_setting(w.get_selected()), self._select_profile_radio(self._detect_current_profile()), self._update_customize_subtitle()),
        )

        # Video Codec
        self.video_codec_combo.connect(
            "notify::selected",
            lambda w, p: (self._save_codec_setting(w.get_selected()), self._select_profile_radio(self._detect_current_profile()), self._update_customize_subtitle()),
        )

        # Copy video: sync hidden data-holder → codec combo
        self.force_copy_video_check.connect(
            "notify::active",
            self._on_force_copy_toggled,
        )

        # Audio Handling
        self.audio_handling_combo.connect(
            "notify::selected",
            self._on_audio_handling_changed,
        )

        # Subtitle — save + update sidebar subtitle
        def _on_subtitle_changed(w, p):
            self.settings_manager.save_setting(
                "subtitle-extract", SUBTITLE_VALUES.get(w.get_selected(), "embedded")
            )
            self._update_subtitles_subtitle()

        self.subtitle_combo.connect("notify::selected", _on_subtitle_changed)

        def _nr_save_and_preview(key, value):
            """Save NR setting and update MPV preview if active."""
            self.settings_manager.save_setting(key, value)
            if hasattr(self, "video_edit_page") and self.video_edit_page:
                self.video_edit_page._apply_audio_filters()

        # Noise Reduction Strength
        self.noise_strength_scale.connect(
            "value-changed",
            lambda w: _nr_save_and_preview(
                "noise-reduction-strength", w.get_value()
            ),
        )

        # GTCRN Advanced
        def _on_model_combo_changed(w, p):
            index = w.get_selected()
            model = 0 if index != 1 else 1
            blending = index == 2
            self.settings_manager.save_setting("noise-model", model)
            self.settings_manager.save_setting("noise-model-blend", blending)
            if hasattr(self, "video_edit_page") and self.video_edit_page:
                self.video_edit_page._apply_audio_filters()
        self.noise_model_row.connect("notify::selected", _on_model_combo_changed)
        self.noise_speech_strength_scale.connect(
            "value-changed",
            lambda w: _nr_save_and_preview(
                "noise-speech-strength", w.get_value()
            ),
        )
        self.noise_lookahead_scale.connect(
            "value-changed",
            lambda w: _nr_save_and_preview(
                "noise-lookahead", int(w.get_value())
            ),
        )
        self.noise_voice_recovery_scale.connect(
            "value-changed",
            lambda w: _nr_save_and_preview(
                "noise-voice-recovery", w.get_value()
            ),
        )

        # Gate intensity
        self.gate_intensity_scale.connect(
            "value-changed",
            lambda w: (self.settings_manager.save_setting(
                "noise-gate-intensity", w.get_value()
            ), self._refresh_nr_preview()),
        )

        # Compressor intensity
        self.compressor_intensity_scale.connect(
            "value-changed",
            lambda w: (self.settings_manager.save_setting(
                "compressor-intensity", w.get_value()
            ), self._refresh_nr_preview()),
        )

        # HPF
        self.hpf_row.connect("notify::active", self._on_hpf_toggled)
        self.hpf_freq_scale.connect(
            "value-changed",
            lambda w: (self.settings_manager.save_setting(
                "hpf-frequency", int(w.get_value())
            ), self._refresh_nr_preview()),
        )

        # Show tooltips — now managed via hamburger menu action

        # Resolution change → update customize subtitle
        self.settings_page.video_resolution_combo.connect(
            "notify::selected",
            lambda w, p: self._update_customize_subtitle(),
        )

        # SW Decode change → update customize subtitle
        self.settings_page.gpu_partial_check.connect(
            "notify::active",
            lambda w, p: self._update_customize_subtitle(),
        )

    def _load_left_pane_settings(self):
        """Load settings for left pane controls"""
        # GPU
        gpu_value = self.settings_manager.load_setting("gpu", "auto")
        gpu_index = self._find_gpu_index(gpu_value)
        self.gpu_combo.set_selected(gpu_index)

        # GPU Device
        gpu_device_index = self.settings_manager.load_setting("gpu-device-index", 0)
        if gpu_device_index <= len(self.detected_gpus):
            self.gpu_device_combo.set_selected(gpu_device_index)

        # Output Format
        format_index = self.settings_manager.load_setting("output-format-index", 0)
        self.output_format_combo.set_selected(format_index)

        # Video Quality
        quality_value = self.settings_manager.load_setting("video-quality", "default")
        quality_index = self._find_quality_index(quality_value)
        self.video_quality_combo.set_selected(quality_index)

        # Video Codec (index 0 = Copy, 1 = H.264, ...)
        force_copy = self.settings_manager.load_setting("force-copy-video", False)
        if force_copy:
            self.video_codec_combo.set_selected(0)
        else:
            codec_value = self.settings_manager.load_setting("video-codec", "h264")
            codec_index = self._find_codec_index(codec_value)
            # Avoid accidentally selecting "Copy" when loading a real codec
            if codec_index == 0:
                codec_index = 1
            self.video_codec_combo.set_selected(codec_index)
        self.force_copy_video_check.set_active(force_copy)
        self._update_encoding_options_state(force_copy)

        # Audio Handling - Use reverse lookup
        audio_value = self.settings_manager.load_setting("audio-handling", "copy")
        audio_index = 0  # Default
        for index, internal_value in AUDIO_VALUES.items():
            if internal_value == audio_value:
                audio_index = index
                break
        self.audio_handling_combo.set_selected(audio_index)

        # Disable audio cleaning ActionRow when audio is copy/none
        audio_will_reencode = audio_index == 1
        self._audio_cleaning_row.set_sensitive(audio_will_reencode)

        # Subtitle - Use reverse lookup
        subtitle_value = self.settings_manager.load_setting(
            "subtitle-extract", "embedded"
        )
        subtitle_index = 0  # Default
        for index, internal_value in SUBTITLE_VALUES.items():
            if internal_value == subtitle_value:
                subtitle_index = index
                break
        self.subtitle_combo.set_selected(subtitle_index)

        # Noise Reduction
        noise_reduction = self.settings_manager.load_setting("noise-reduction", False)
        self.noise_reduction_switch.set_active(noise_reduction)
        self.noise_reduction_expander.set_enable_expansion(noise_reduction)

        # Noise Reduction Strength
        noise_strength = self.settings_manager.load_setting(
            "noise-reduction-strength", 1.0
        )
        self.noise_strength_scale.set_value(noise_strength)

        # GTCRN Advanced — derive combo index from model + blending
        saved_model = self.settings_manager.load_setting("noise-model", 0)
        saved_blending = self.settings_manager.load_setting("noise-model-blend", False)
        if saved_blending:
            model_combo_index = 2  # Smart (both combined)
        elif saved_model == 1:
            model_combo_index = 1  # Natural Voice (VCTK)
        else:
            model_combo_index = 0  # Maximum Cleaning (DNS3)
        self.noise_model_row.set_selected(model_combo_index)

        self.noise_speech_strength_scale.set_value(
            self.settings_manager.load_setting("noise-speech-strength", 1.0)
        )
        self.noise_lookahead_scale.set_value(
            self.settings_manager.load_setting("noise-lookahead", 50)
        )
        self.noise_voice_recovery_scale.set_value(
            self.settings_manager.load_setting("noise-voice-recovery", 0.75)
        )

        # Noise Gate
        gate_enabled = self.settings_manager.load_setting("noise-gate-enabled", False)
        self.gate_switch.set_active(gate_enabled)
        self.gate_expander.set_enable_expansion(gate_enabled)
        self.gate_intensity_scale.set_value(
            self.settings_manager.load_setting("noise-gate-intensity", 0.5)
        )
        self.gate_intensity_scale.set_sensitive(gate_enabled)

        # Compressor
        comp_enabled = self.settings_manager.load_setting("compressor-enabled", False)
        self.compressor_switch.set_active(comp_enabled)
        self.compressor_expander.set_enable_expansion(comp_enabled)
        self.compressor_intensity_scale.set_value(
            self.settings_manager.load_setting("compressor-intensity", 1.0)
        )
        self.compressor_intensity_scale.set_sensitive(comp_enabled)

        # HPF
        hpf_enabled = self.settings_manager.load_setting("hpf-enabled", False)
        self.hpf_row.set_active(hpf_enabled)
        self.hpf_freq_scale.set_value(
            self.settings_manager.load_setting("hpf-frequency", 80)
        )
        self.hpf_freq_row.set_visible(hpf_enabled)

        # EQ
        eq_enabled = self.settings_manager.load_setting("eq-enabled", False)
        self.eq_switch.set_active(eq_enabled)
        self.eq_expander.set_enable_expansion(eq_enabled)
        self.eq_preset_row.set_sensitive(eq_enabled)
        eq_preset = self.settings_manager.load_setting("eq-preset", "flat")
        if eq_preset in self._eq_preset_keys:
            self.eq_preset_row.set_selected(self._eq_preset_keys.index(eq_preset))

        # Loudness Normalization
        normalize_enabled = self.settings_manager.load_setting("normalize-enabled", False)
        self.normalize_row.set_active(normalize_enabled)

        # Show tooltips — state loaded via tooltip_action in _setup_actions

        # Update all sidebar subtitles
        self._select_profile_radio(self._detect_current_profile())
        self._update_customize_subtitle()
        self._update_audio_subtitle()
        self._update_audio_cleaning_subtitle()
        self._update_subtitles_subtitle()
        self._update_extra_subtitle()

    def _update_customize_subtitle(self):
        """Update the 'Customize encoding...' row subtitle with current settings."""
        profile = self._detect_current_profile()
        parts = []
        if profile == "custom":
            codec_model = self.video_codec_combo.get_model()
            if codec_model:
                val = codec_model.get_string(self.video_codec_combo.get_selected()) or ""
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
        self._customize_row.set_subtitle(" · ".join(parts) if parts else "")

    def _on_video_encoding_activated(self, _row):
        """Open the educational video encoding dialog."""
        from ui.video_encoding_dialog import show_video_encoding_dialog

        show_video_encoding_dialog(self.window, self)

    def _on_audio_activated(self, _row):
        """Open the educational audio dialog."""
        from ui.audio_dialog import show_audio_dialog

        show_audio_dialog(self.window, self)

    def _on_noise_options_activated(self, _row):
        """Open the educational audio cleaning dialog."""
        from ui.noise_dialog import show_noise_dialog

        show_noise_dialog(self.window, self)

    def _on_subtitles_activated(self, _row):
        """Open the educational subtitles dialog."""
        from ui.subtitles_dialog import show_subtitles_dialog

        show_subtitles_dialog(self.window, self)

    def _on_extra_activated(self, _row):
        """Open the extra settings dialog."""
        from ui.extra_dialog import show_extra_dialog

        show_extra_dialog(self.window, self)

    # ── Sidebar subtitle update methods ──

    def _update_audio_subtitle(self):
        """Update the Audio ActionRow subtitle with current settings."""
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
        self._audio_row.set_subtitle(" · ".join(parts) if parts else "")

    def _update_audio_cleaning_subtitle(self):
        """Update the Audio Settings ActionRow subtitle with active features."""
        audio_will_reencode = self.audio_handling_combo.get_selected() == 1
        if not audio_will_reencode:
            self._audio_cleaning_row.set_subtitle(
                _("Requires audio set to re-encode")
            )
        else:
            active = []
            if self.noise_reduction_switch.get_active():
                active.append(_("AI noise removal"))
            if self.gate_switch.get_active():
                active.append(_("gate"))
            if self.compressor_switch.get_active():
                active.append(_("compressor"))
            if self.hpf_row.get_active():
                active.append(_("low cut"))
            if self.eq_switch.get_active():
                active.append(_("equalizer"))
            if self.normalize_row.get_active():
                active.append(_("normalize"))
            if active:
                self._audio_cleaning_row.set_subtitle(", ".join(active))
            else:
                self._audio_cleaning_row.set_subtitle(_("Disabled"))
        # Sync editor's Audio Cleaning row if present
        if hasattr(self, "video_edit_page") and self.video_edit_page and hasattr(self.video_edit_page, "ui"):
            self.video_edit_page.ui._sync_nr_subtitle()

    def _update_subtitles_subtitle(self):
        """Update the Subtitles ActionRow subtitle."""
        model = self.subtitle_combo.get_model()
        if model:
            idx = self.subtitle_combo.get_selected()
            item = model.get_string(idx)
            self._subtitles_row.set_subtitle(item or "")

    def _update_sidebar_for_extract_mode(self):
        """Disable all sidebar options except Subtitles when only-extract-subtitles is active."""
        is_extract = self.settings_page.only_extract_subtitles_check.get_active()
        enable = not is_extract
        for row in (self._copy_row, self._universal_row, self._efficient_row,
                    self._smallest_row, self._customize_row,
                    self._audio_row, self._audio_cleaning_row, self._extra_row):
            row.set_sensitive(enable)

    def _update_extra_subtitle(self):
        """Update the Extra ActionRow subtitle."""
        parts = []
        if hasattr(self, "settings_page") and self.settings_page.options_entry.get_text().strip():
            parts.append(_("Custom FFmpeg flags"))
        self._extra_row.set_subtitle(" · ".join(parts) if parts else _("No customizations"))

    def _save_gpu_setting(self, index):
        """Save GPU setting as direct value"""
        if index in GPU_VALUES:
            self.settings_manager.save_setting("gpu", GPU_VALUES[index])

    def _save_quality_setting(self, index):
        """Save video quality setting as direct value"""
        if index in VIDEO_QUALITY_VALUES:
            self.settings_manager.save_setting(
                "video-quality", VIDEO_QUALITY_VALUES[index]
            )

    def _save_codec_setting(self, index):
        """Save video codec setting — index 0 means copy (no re-encoding)."""
        if index not in VIDEO_CODEC_VALUES:
            return
        value = VIDEO_CODEC_VALUES[index]
        is_copy = value == "copy"
        self.settings_manager.save_setting("video-codec", value)
        self.settings_manager.save_setting("force-copy-video", is_copy)
        # Keep hidden data-holder in sync
        if self.force_copy_video_check.get_active() != is_copy:
            self.force_copy_video_check.set_active(is_copy)
        self._update_encoding_options_state(is_copy)

    def _detect_gpu_devices(self):
        """Detect available GPU render devices in the system"""
        gpus = []
        try:
            import glob
            import subprocess

            render_devices = sorted(glob.glob("/dev/dri/renderD*"))
            if len(render_devices) <= 1:
                return gpus

            result = subprocess.run(
                ["lspci", "-nn"], capture_output=True, text=True, timeout=5
            )
            gpu_lines = [
                line
                for line in result.stdout.splitlines()
                if any(kw in line.lower() for kw in ["vga", "3d", "display"])
            ]

            for i, device_path in enumerate(render_devices):
                if i < len(gpu_lines):
                    name = (
                        gpu_lines[i].split(": ", 1)[-1]
                        if ": " in gpu_lines[i]
                        else gpu_lines[i]
                    )
                    # Trim to reasonable length
                    if len(name) > 50:
                        name = name[:47] + "..."
                else:
                    name = os.path.basename(device_path)
                gpus.append({"name": name, "device": device_path})
        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"GPU detection error: {e}")
        return gpus

    def _find_gpu_index(self, value):
        """Find index of GPU value"""
        value = value.lower()
        reverse_map = {v: k for k, v in GPU_VALUES.items()}
        return reverse_map.get(value, 0)

    def _find_quality_index(self, value):
        """Find index of quality value"""
        value = value.lower()
        reverse_map = {v: k for k, v in VIDEO_QUALITY_VALUES.items()}
        return reverse_map.get(value, 3)

    def _find_codec_index(self, value):
        """Find index of codec value (default: 1 = H.264)"""
        value = value.lower()
        reverse_map = {v: k for k, v in VIDEO_CODEC_VALUES.items()}
        return reverse_map.get(value, 1)

    def _on_force_copy_toggled(self, switch, param):
        """Sync force-copy data-holder with codec combo."""
        is_active = switch.get_active()
        self.settings_manager.save_setting("force-copy-video", is_active)
        # Sync codec combo: select "Copy" (0) or fallback to H.264 (1)
        if is_active and self.video_codec_combo.get_selected() != 0:
            self.video_codec_combo.set_selected(0)
        elif not is_active and self.video_codec_combo.get_selected() == 0:
            self.video_codec_combo.set_selected(1)
        self._update_encoding_options_state(is_active)

    def _on_audio_handling_changed(self, combo, _pspec):
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
        self._update_audio_cleaning_subtitle()
        # Update NR preview button visibility in edit page
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            self.video_edit_page.update_nr_button_visibility()

    def _update_encoding_options_state(self, force_copy_enabled):
        """Enable/disable encoding options based on force copy state"""
        enable = not force_copy_enabled

        self.gpu_combo.set_sensitive(enable)
        self.video_quality_combo.set_sensitive(enable)
        self.video_codec_combo.set_sensitive(enable)

        # Disable customize row when copy is active
        self._customize_row.set_sensitive(enable)

        # Update video edit page if it exists
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            if hasattr(self.video_edit_page, "ui"):
                self.video_edit_page.ui.update_for_force_copy_state(force_copy_enabled)

        self._update_customize_subtitle()
