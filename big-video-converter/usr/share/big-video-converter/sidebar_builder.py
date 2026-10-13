"""Sidebar builder mixin: the left pane and its signals."""

import gettext
import logging
import threading

from constants import SUBTITLE_VALUES
from engine.gpu_selector import name_render_devices, render_devices, select_best_gpu
from gi.repository import Adw, Gdk, GLib, GObject, Gtk
from ui.setting_cards import ChoiceRow, SettingCards, SettingRow, option_tile

_ = gettext.gettext

logger = logging.getLogger(__name__)


class SidebarBuilderMixin:
    """Mixin providing sidebar (left pane) construction and signal wiring."""

    def _create_left_pane(self):
        left_toolbar_view = Adw.ToolbarView()
        left_toolbar_view.add_css_class("sidebar")

        # Detect window button layout
        window_buttons_left = self._window_buttons_on_left()

        left_header = Adw.HeaderBar()
        left_header.add_css_class("sidebar")
        left_header.set_show_title(True)
        # Configure left header bar based on window button layout
        left_header.set_decoration_layout(
            "close,maximize,minimize:menu" if window_buttons_left else ""
        )

        if not window_buttons_left:
            # The editor puts its own title and note here (show_editor_for_file).
            self.sidebar_title = Adw.WindowTitle(title="Big Video Converter")
            left_header.set_title_widget(self.sidebar_title)
        else:
            title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.sidebar_title = Adw.WindowTitle(title="Big Video Converter")
            title_box.append(self.sidebar_title)
            expander = Gtk.Box()
            expander.set_hexpand(True)
            title_box.append(expander)
            left_header.set_title_widget(title_box)
        left_toolbar_view.add_top_bar(left_header)
        # The editor's options start at the top: it keeps this header only for
        # window buttons on the left (show_editor_for_file).
        self.sidebar_header = left_header
        self.sidebar_header_holds_buttons = window_buttons_left

        left_scroll = Gtk.ScrolledWindow(css_classes=["sidebar"])
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroll.set_min_content_width(300)
        left_scroll.set_max_content_width(400)
        # One set of cards for both pages: one opens at a time.
        self.setting_cards = SettingCards(left_scroll, self.card_layer)

        self.left_stack = Adw.ViewStack()
        self.left_stack.set_hhomogeneous(False)
        # Each page scrolls by its own height: the taller editing page would
        # otherwise give the conversion page a scrollbar over empty space.
        self.left_stack.set_vhomogeneous(False)

        # Page 1: Conversion Settings
        conversion_settings = self._create_conversion_settings()
        self.left_stack.add_titled(
            conversion_settings, "conversion_settings", _("Conversion")
        )

        # Page 2: the editing tools, filled by the editor page once it exists.
        self.editing_tools_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        self.left_stack.add_titled(
            self.editing_tools_box, "editing_tools", _("Editing")
        )

        self.left_stack.connect("notify::visible-child", self.setting_cards.close)
        left_scroll.set_child(self.left_stack)
        # Without the header, empty space still moves the window.
        left_toolbar_view.set_content(Gtk.WindowHandle(child=left_scroll))

        left_toolbar_view.set_size_request(300, -1)

        self.split_view.set_sidebar(left_toolbar_view)

    def _create_sidebar_resize_handle(self):
        """Edge the user drags (or moves with Left/Right) to resize the sidebar.

        Adw.OverlaySplitView has no resize handle of its own. The handle lies
        on the content's leading edge, clear of the sidebar's scrollbar, and
        only exists while there is a boundary to move.
        """
        split = self.split_view
        handle = Gtk.Box(
            halign=Gtk.Align.START,
            width_request=8,
            focusable=True,
            accessible_role=Gtk.AccessibleRole.SEPARATOR,
            tooltip_text=_("Resize sidebar"),
        )
        handle.add_css_class("bvc-sidebar-handle")
        handle.set_cursor_from_name("col-resize")
        handle.update_property(
            [
                Gtk.AccessibleProperty.LABEL,
                Gtk.AccessibleProperty.VALUE_MIN,
                Gtk.AccessibleProperty.VALUE_MAX,
            ],
            [
                _("Resize sidebar"),
                split.get_min_sidebar_width(),
                split.get_max_sidebar_width(),
            ],
        )

        def resize(width):
            total = split.get_width()
            if total <= 0:
                return
            width = max(
                split.get_min_sidebar_width(), min(split.get_max_sidebar_width(), width)
            )
            split.set_sidebar_width_fraction(width / total)
            handle.update_property([Gtk.AccessibleProperty.VALUE_NOW], [float(width)])

        # The handle moves with the edge, so drag offsets (relative to the
        # handle) are converted to split-view coordinates first. In a
        # right-to-left layout the sidebar is on the right and grows leftwards.
        drag = Gtk.GestureDrag()
        start = [0, 0.0]

        def outwards():
            return -1 if handle.get_direction() == Gtk.TextDirection.RTL else 1

        def pointer_x(gesture, dx):
            x, _y = gesture.get_start_point()[1:]
            return handle.translate_coordinates(split, x + dx, 0)[0]

        def on_begin(gesture, _x, _y):
            # An open card's bridge lies over the handle and would trail the edge.
            self.setting_cards.close()
            start[:] = [split.get_sidebar().get_width(), pointer_x(gesture, 0)]
            handle.add_css_class("dragging")
            handle.grab_focus()

        drag.connect("drag-begin", on_begin)
        drag.connect(
            "drag-update",
            lambda g, dx, _dy: resize(
                start[0] + outwards() * (pointer_x(g, dx) - start[1])
            ),
        )
        drag.connect("drag-end", lambda *_a: handle.remove_css_class("dragging"))
        handle.add_controller(drag)

        keys = Gtk.EventControllerKey()

        def on_key(_controller, keyval, _keycode, _state):
            step = {Gdk.KEY_Left: -24, Gdk.KEY_Right: 24}.get(keyval)
            if step is None:
                return False
            resize(split.get_sidebar().get_width() + outwards() * step)
            return True

        keys.connect("key-pressed", on_key)
        handle.add_controller(keys)

        # A collapsed sidebar floats over the content and has a fixed width,
        # so the handle would only be an invisible Tab stop under the overlay.
        def sync_visible(split, _pspec=None):
            handle.set_visible(split.get_show_sidebar() and not split.get_collapsed())

        split.connect("notify::show-sidebar", sync_visible)
        split.connect("notify::collapsed", sync_visible)
        sync_visible(split)
        return handle

    def _create_conversion_settings(self):
        """The conversion settings: rows with cards, or that open a dialog."""
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

        # Create all data-holder widgets (not added to sidebar)

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

        # GPU Device: offered only when there is a choice.
        gpus = render_devices()
        self.detected_gpus = gpus if len(gpus) > 1 else []
        self.gpu_device_combo = Adw.ComboRow(title=_("GPU Device"))
        self.gpu_device_combo.set_subtitle(_("Select which GPU to use"))
        gpu_device_model = Gtk.StringList()
        gpu_device_model.append(_("Auto"))
        for gpu_info in self.detected_gpus:
            gpu_device_model.append(gpu_info["name"])
        self.gpu_device_combo.set_model(gpu_device_model)
        if self.detected_gpus:
            codec = self.settings_store.load_setting("video-codec", "h264")

            def probe_gpus() -> None:
                # lspci names each card; ranking runs ffmpeg, vainfo and an
                # NVENC test frame, whose answers are cached for the jobs.
                names = name_render_devices(gpus)
                GLib.idle_add(self._name_gpu_devices, gpus, names, gpu_device_model)
                select_best_gpu(gpus, codec)

            threading.Thread(
                target=probe_gpus, name="bvc-gpu-probe", daemon=True
            ).start()

        # Output Format
        format_model = Gtk.StringList()
        for fmt in ["MP4", "MKV", "MOV", "WebM"]:
            format_model.append(fmt)
        self.output_format_combo = Adw.ComboRow(title=_("Output Format"))
        self.output_format_combo.set_subtitle(_("Container format for output file"))
        self.output_format_combo.set_model(format_model)

        # Hidden data-holder for force copy state (driven by codec combo index 0)
        self.force_copy_video_check = Adw.SwitchRow(title=_("Copy without re-encoding"))

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

        # Audio noise reduction: a hidden expander holding its controls.
        self.noise_reduction_expander = Adw.ExpanderRow(title=_("Noise Cleaning (AI)"))
        self.noise_reduction_expander.set_subtitle(_("Remove background noise"))
        self.noise_reduction_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.noise_reduction_switch.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Enable noise reduction")],
        )
        self.noise_reduction_switch.connect(
            "state-set", self._on_noise_reduction_toggled
        )
        self.noise_reduction_expander.add_suffix(self.noise_reduction_switch)
        self.noise_reduction_expander.set_enable_expansion(False)
        self.noise_reduction_expander.set_expanded(False)

        # Noise Reduction Strength (visible only when noise reduction is enabled)
        self.noise_strength_row = Adw.ActionRow(title=_("Strength"))
        self.noise_strength_adj = Gtk.Adjustment(
            value=1.0, lower=0.0, upper=1.0, step_increment=0.05, page_increment=0.1
        )
        self.noise_strength_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.noise_strength_adj
        )
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
        self.noise_strength_adj.connect(
            "notify::value",
            lambda a, _: self._noise_strength_label.set_text(
                _("{percent}%").format(percent=f"{a.get_value() * 100:.0f}")
            ),
        )
        self.noise_strength_row.add_suffix(self.noise_strength_scale)
        self.noise_strength_row.add_suffix(self._noise_strength_label)
        self.noise_reduction_expander.add_row(self.noise_strength_row)
        self.help_tooltips.add_tooltip(
            self.noise_strength_row, "noise_reduction_strength"
        )

        # In the order of constants.NOISE_MODELS.
        self._noise_model_list = [
            _("Light — DeepFilterNet3"),
            _("Higher quality — DPDFNet-2 48 kHz"),
        ]
        noise_model_model = Gtk.StringList.new(self._noise_model_list)
        self.noise_model_row = Adw.ComboRow(
            title=_("AI Model"), model=noise_model_model
        )
        self.noise_model_row.set_selected(0)
        self.noise_reduction_expander.add_row(self.noise_model_row)

        # Noise Gate - simplified with intensity slider
        self.gate_expander = Adw.ExpanderRow(title=_("Noise Gate"))
        self.gate_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.gate_switch.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Noise Gate")],
        )
        self.gate_switch.connect("state-set", self._on_gate_switch_changed)
        self.gate_expander.add_suffix(self.gate_switch)
        self.gate_expander.set_enable_expansion(False)
        self.gate_expander.set_expanded(False)
        self.help_tooltips.add_tooltip(self.gate_expander, "noise_gate")

        self.gate_intensity_row = Adw.ActionRow(title=_("Intensity"))
        self.gate_intensity_adj = Gtk.Adjustment(
            value=0.5, lower=0.0, upper=1.0, step_increment=0.05, page_increment=0.1
        )
        self.gate_intensity_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.gate_intensity_adj
        )
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
        self.gate_intensity_adj.connect(
            "notify::value",
            lambda a, _: self._gate_intensity_label.set_text(
                _("{percent}%").format(percent=f"{a.get_value() * 100:.0f}")
            ),
        )
        self.gate_intensity_row.add_suffix(self.gate_intensity_scale)
        self.gate_intensity_row.add_suffix(self._gate_intensity_label)
        self.gate_expander.add_row(self.gate_intensity_row)

        self.noise_reduction_expander.add_row(self.gate_expander)

        # Compressor
        self.compressor_expander = Adw.ExpanderRow(title=_("Compressor"))
        self.compressor_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.compressor_switch.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Enable compressor")],
        )
        self.compressor_switch.connect("state-set", self._on_compressor_switch_changed)
        self.compressor_expander.add_suffix(self.compressor_switch)
        self.compressor_expander.set_enable_expansion(False)
        self.compressor_expander.set_expanded(False)

        self.compressor_intensity_row = Adw.ActionRow(title=_("Intensity"))
        self.compressor_intensity_adj = Gtk.Adjustment(
            value=1.0, lower=0.0, upper=1.0, step_increment=0.05, page_increment=0.1
        )
        self.compressor_intensity_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=self.compressor_intensity_adj,
        )
        self.compressor_intensity_scale.set_digits(2)
        self.compressor_intensity_scale.set_hexpand(True)
        self.compressor_intensity_scale.set_size_request(200, -1)
        self.compressor_intensity_scale.set_valign(Gtk.Align.CENTER)
        self.compressor_intensity_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, _("0%"))
        self.compressor_intensity_scale.add_mark(0.5, Gtk.PositionType.BOTTOM, _("50%"))
        self.compressor_intensity_scale.add_mark(
            1.0, Gtk.PositionType.BOTTOM, _("100%")
        )
        self.compressor_intensity_scale.set_sensitive(False)
        self._compressor_label = Gtk.Label(label="100%")
        self._compressor_label.set_size_request(45, -1)
        self._compressor_label.add_css_class("numeric")
        self._compressor_label.add_css_class("caption")
        self.compressor_intensity_adj.connect(
            "notify::value",
            lambda a, _: self._compressor_label.set_text(
                _("{percent}%").format(percent=f"{a.get_value() * 100:.0f}")
            ),
        )
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
        self.hpf_freq_adj = Gtk.Adjustment(
            value=80, lower=20, upper=500, step_increment=5, page_increment=20
        )
        self.hpf_freq_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.hpf_freq_adj
        )
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
        self.hpf_freq_adj.connect(
            "notify::value",
            lambda a, _: self._hpf_label.set_text(f"{a.get_value():.0f} Hz"),
        )
        self.hpf_freq_row.add_suffix(self.hpf_freq_scale)
        self.hpf_freq_row.add_suffix(self._hpf_label)
        self.hpf_freq_row.set_visible(False)
        self.noise_reduction_expander.add_row(self.hpf_freq_row)

        # EQ
        self.eq_expander = Adw.ExpanderRow(title=_("Equalizer"))
        self.eq_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.eq_switch.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Enable equalizer")],
        )
        self.eq_switch.connect("state-set", self._on_eq_switch_changed)
        self.eq_expander.add_suffix(self.eq_switch)
        self.eq_expander.set_enable_expansion(False)
        self.eq_expander.set_expanded(False)

        self._eq_preset_keys = [
            "default_voice",
            "flat",
            "voice_boost",
            "podcast",
            "warm",
            "bright",
            "de_esser",
            "bass_cut",
            "presence",
            "custom",
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
            _("Default Voice"),
            _("Natural (No Effects)"),
            _("Crystal Voice"),
            _("Radio Host"),
            _("Velvet Voice"),
            _("Extra Brightness"),
            _("Soften 'S' (De-esser)"),
            _("Remove Rumble"),
            _("Present Voice"),
            _("Custom"),
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

        # Each setting is a row saying what it applies: the simple choices
        # open a card beside the sidebar and come first, the rows below them
        # open a whole dialog on a click.
        cards = self.setting_cards
        video_box, video_list = cards.add_group(None)
        dialog_box, dialog_list = cards.add_group(None)

        # The mode radios stay the source of truth: the four built-in modes
        # are tiles in a card, Customize and Presets are rows of their own.
        self._radio_copy = Gtk.CheckButton()
        self._radio_universal = Gtk.CheckButton(group=self._radio_copy)
        self._radio_smaller = Gtk.CheckButton(group=self._radio_copy)
        self._radio_quality = Gtk.CheckButton(group=self._radio_copy)
        self._radio_custom = Gtk.CheckButton(group=self._radio_copy)
        self._radio_preset = Gtk.CheckButton(group=self._radio_copy)

        self._profile_guard = False  # Prevent recursive signal loops

        modes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for radio, name, detail, tooltip in (
            (
                self._radio_copy,
                _("Fast Copy"),
                _("No re-encoding, fastest"),
                "profile_copy",
            ),
            (
                self._radio_universal,
                _("Universal"),
                _("H.264, compatible with all devices"),
                "profile_universal",
            ),
            (
                self._radio_smaller,
                _("More Efficient"),
                _("H.265, better compression"),
                "profile_efficient",
            ),
            (
                self._radio_quality,
                _("Smaller File"),
                _("AV1, best compression"),
                "profile_smaller",
            ),
        ):
            modes.append(option_tile(name, detail, radio))
            self.help_tooltips.add_tooltip(radio, tooltip)
        self._radio_universal.get_child().append(
            Gtk.Label(
                label=_("Recommended"), xalign=0, css_classes=["caption", "success"]
            )
        )
        self._video_mode_row = SettingRow(
            "video-x-generic-symbolic",
            _("Video mode"),
            _("How the video is encoded."),
            modes,
        )
        self._video_mode_row.focus_target = self._active_mode_radio
        cards.add_row(video_list, self._video_mode_row)

        self._customize_row = SettingRow(
            "emblem-system-symbolic",
            _("Customize encoding..."),
            None,
            None,
            on_activate=self._on_video_encoding_activated,
        )
        cards.add_row(dialog_list, self._customize_row)

        # One row for every preset: the list itself lives in a dialog with a
        # grid and a search box, so dozens of presets never crowd the sidebar.
        self._presets_row = SettingRow(
            "view-grid-symbolic",
            _("Presets"),
            None,
            None,
            on_activate=self._on_presets_activated,
        )
        cards.add_row(dialog_list, self._presets_row)

        self._radio_copy.connect("toggled", self._on_profile_toggled)
        self._radio_universal.connect("toggled", self._on_profile_toggled)
        self._radio_smaller.connect("toggled", self._on_profile_toggled)
        self._radio_quality.connect("toggled", self._on_profile_toggled)
        self._radio_preset.connect("toggled", self._on_profile_toggled)

        self._audio_options_button = Gtk.Button(
            label=_("Codec, bitrate and channels…"), halign=Gtk.Align.START
        )
        self._audio_options_button.connect("clicked", self._on_audio_activated)
        self._audio_row = ChoiceRow(
            "audio-x-generic-symbolic",
            _("Audio Encoding"),
            _("Choose what the converted video should contain."),
            AUDIO_OPTIONS,
            extra=[self._audio_options_button],
        )
        self.audio_handling_combo.bind_property(
            "selected",
            self._audio_row,
            "selected",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        cards.add_row(video_list, self._audio_row)

        self._audio_cleaning_row = SettingRow(
            "audio-volume-high-symbolic",
            _("Audio Settings"),
            None,
            None,
            on_activate=self._on_noise_options_activated,
        )
        cards.add_row(dialog_list, self._audio_cleaning_row)

        # Bound to the extraction setting once the settings page exists below.
        self._extract_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        extract_label = Gtk.Label(
            label=_("Only Extract Subtitles"), xalign=0, hexpand=True, wrap=True
        )
        self._extract_switch.update_property(
            [Gtk.AccessibleProperty.LABEL], [extract_label.get_label()]
        )
        extract_box = Gtk.Box(spacing=12)
        extract_box.append(extract_label)
        extract_box.append(self._extract_switch)
        self._subtitles_row = ChoiceRow(
            "insert-text-symbolic",
            _("Subtitles"),
            _("Configure how subtitles are handled during conversion."),
            SUBTITLE_OPTIONS,
            extra=[extract_box],
        )
        self.subtitle_combo.bind_property(
            "selected",
            self._subtitles_row,
            "selected",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        cards.add_row(video_list, self._subtitles_row)

        self._size_row = SettingRow(
            "drive-harddisk-symbolic",
            _("Maximum size"),
            None,
            None,
            on_activate=self._on_size_activated,
        )
        cards.add_row(dialog_list, self._size_row)

        self._extra_row = SettingRow(
            "preferences-system-symbolic",
            _("Extra"),
            None,
            None,
            on_activate=self._on_extra_activated,
        )
        cards.add_row(dialog_list, self._extra_row)

        settings_box.append(video_box)
        settings_box.append(dialog_box)

        from ui.settings_page import SettingsPage

        self.settings_page = SettingsPage(self)
        self.settings_page.only_extract_subtitles_check.bind_property(
            "active",
            self._extract_switch,
            "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        self._update_sidebar_for_extract_mode()

        self._connect_left_pane_signals()
        self._load_left_pane_settings()

        return settings_box

    def _name_gpu_devices(self, gpus, names, model) -> bool:
        """Put lspci's names in place of the node placeholders, keeping the choice."""
        if getattr(self, "_quitting", False):
            return GLib.SOURCE_REMOVE
        for gpu, name in zip(gpus, names, strict=True):
            gpu["name"] = name
        selected = self.gpu_device_combo.get_selected()
        # Replacing the items moves the selection for a moment; that is not a
        # choice to save.
        with self.settings_store.suspend_writes():
            model.splice(1, len(names), names)
            self.gpu_device_combo.set_selected(selected)
        return GLib.SOURCE_REMOVE

    def _connect_left_pane_signals(self):
        # GPU
        self.gpu_combo.connect(
            "notify::selected",
            lambda w, p: (
                self._save_gpu_setting(w.get_selected()),
                self._update_customize_subtitle(),
            ),
        )

        # GPU Device
        self.gpu_device_combo.connect(
            "notify::selected",
            lambda w, p: (
                self.settings_store.save_setting("gpu-device-index", w.get_selected()),
                self._update_customize_subtitle(),
            ),
        )

        # Output format: saved, mirrored on the settings page, summarized.
        def _on_sidebar_format_changed(w, p):
            idx = w.get_selected()
            self.settings_store.save_setting("output-format-index", idx)
            if (
                hasattr(self, "settings_page")
                and self.settings_page.output_format_combo.get_selected() != idx
            ):
                self.settings_page.output_format_combo.set_selected(idx)
            self._update_customize_subtitle()

        self.output_format_combo.connect("notify::selected", _on_sidebar_format_changed)

        # Video Quality
        self.video_quality_combo.connect(
            "notify::selected",
            lambda w, p: (
                self._save_quality_setting(w.get_selected()),
                self._select_profile_radio(self._detect_current_profile()),
                self._update_customize_subtitle(),
            ),
        )

        # Video Codec
        self.video_codec_combo.connect(
            "notify::selected",
            lambda w, p: (
                self._save_codec_setting(w.get_selected()),
                self._select_profile_radio(self._detect_current_profile()),
                self._update_customize_subtitle(),
            ),
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

        # Subtitles: saved and summarized on their row.
        def _on_subtitle_changed(w, p):
            self.settings_store.save_setting(
                "subtitle-extract", SUBTITLE_VALUES.get(w.get_selected(), "embedded")
            )
            self._update_subtitles_subtitle()

        self.subtitle_combo.connect("notify::selected", _on_subtitle_changed)

        def _nr_save_and_preview(key, value):
            """Save NR setting and update MPV preview if active."""
            self.settings_store.save_setting(key, value)
            if hasattr(self, "video_edit_page") and self.video_edit_page:
                self.video_edit_page._apply_audio_filters()

        # Noise Reduction Strength
        def _on_noise_strength_changed(w):
            self._save_slider("noise-reduction-strength", w.get_value())
            if hasattr(self, "video_edit_page") and self.video_edit_page:
                self.video_edit_page._apply_audio_filters()

        self.noise_strength_scale.connect("value-changed", _on_noise_strength_changed)

        def _on_model_combo_changed(w, p):
            _nr_save_and_preview("noise-model", w.get_selected())

        self.noise_model_row.connect("notify::selected", _on_model_combo_changed)
        # Gate intensity
        self.gate_intensity_scale.connect(
            "value-changed",
            lambda w: (
                self._save_slider("noise-gate-intensity", w.get_value()),
                self._refresh_nr_preview(),
            ),
        )

        # Compressor intensity
        self.compressor_intensity_scale.connect(
            "value-changed",
            lambda w: (
                self._save_slider("compressor-intensity", w.get_value()),
                self._refresh_nr_preview(),
            ),
        )

        # HPF
        self.hpf_row.connect("notify::active", self._on_hpf_toggled)
        self.hpf_freq_scale.connect(
            "value-changed",
            lambda w: (
                self._save_slider("hpf-frequency", int(w.get_value())),
                self._refresh_nr_preview(),
            ),
        )

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
