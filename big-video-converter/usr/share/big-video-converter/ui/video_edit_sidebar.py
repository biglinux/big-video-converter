"""Video editor sidebar mixin: the adjustment, transform, effects and segment cards."""

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import gettext

from gi.repository import Adw, Gio, GLib, Gtk

_ = gettext.gettext

from engine.job_options import EFFECT_LEVELS, SOURCE_HDR_MODES
from engine.video_settings import available_filters

from ui.pause_panel import PausePanel
from ui.setting_cards import ChoiceRow, SettingRow, StepRow, SwitchRow


class VideoEditSidebarMixin:
    """Mixin building the editor sidebar and keeping its cards in step with the page."""

    def _create_nr_sidebar_group(self, cards):
        """The audio settings row, as on the main screen; it opens their dialog."""
        box, listbox = cards.add_group(None)
        self.nr_settings_row = SettingRow(
            "audio-volume-high-symbolic",
            _("Audio Settings"),
            None,
            None,
            on_activate=self._on_nr_settings_activated,
        )
        cards.add_row(listbox, self.nr_settings_row)
        self._sync_nr_subtitle()
        return box

    def _sync_nr_subtitle(self):
        """Show on the editor's row what the audio settings apply."""
        if not hasattr(self, "nr_settings_row"):
            return
        summary = self.page.app.audio_cleaning_summary()
        if summary is not None:
            self.nr_settings_row.show_value(*summary)

    def _on_nr_settings_activated(self, *_args):
        """Open the noise cleaning dialog from the editing sidebar."""
        from ui.noise_dialog import show_noise_dialog

        show_noise_dialog(self.page.app.window, self.page.app)

    def populate_sidebar(self, sidebar_box) -> None:
        """Populate the sidebar with video adjustment controls and trim marking"""
        # The controls stay usable in copy mode: freeze() re-encodes a file
        # once it is cropped, adjusted or turned, and this note says so.
        self.copy_mode_note = Gtk.Label(
            label=_(
                "The video is being copied without re-encoding. Cropping, adjusting, turning or adding effects to the picture re-encodes this video."
            ),
            wrap=True,
            xalign=0,
            css_classes=["dim-label"],
            visible=False,
        )
        sidebar_box.append(self.copy_mode_note)

        cards = self.page.app.setting_cards
        help_texts = getattr(self.page.app, "help_tooltips", None)
        self._help = help_texts.tooltips if help_texts is not None else {}

        # Picture, orientation and pauses: the editor's everyday options.
        adjust_box, adjust_list = cards.add_group(None)

        def percent(value):
            return f"{round(value * 100):+d}%" if round(value * 100) else "0%"

        for name, icon, title, low, high, default, describe in (
            ("brightness", "display-brightness-symbolic", _("Brightness"), -1.0, 1.0, 0.0, percent),
            ("contrast", "image-adjust-contrast-symbolic", _("Contrast"), -1.0, 1.0, 0.0, percent),
            ("saturation", "color-select-symbolic", _("Saturation"), 0.0, 2.0, 1.0, lambda value: percent(value - 1.0)),
            ("hue", "applications-graphics-symbolic", _("Hue"), -1.0, 1.0, 0.0, percent),
        ):  # fmt: skip
            scale, row = self._create_adjustment_row(
                cards,
                adjust_list,
                icon,
                title,
                self._help.get(name),
                (low, high, default),
                getattr(self.page, f"on_{name}_changed"),
                getattr(self.page, f"reset_{name}"),
                describe,
            )
            setattr(self, f"{name}_scale", scale)
            setattr(self, f"{name}_row", row)
        # For a file that does not say how its colors are coded: HDR read
        # as SDR looks gray and washed out, and is converted that way too.
        self.source_hdr_combo = ChoiceRow(
            "video-display-symbolic",
            _("Source colors"),
            self._help.get("source_hdr"),
            [
                _("As the file says"),
                _("HDR10 (PQ)"),
                _("HDR (HLG)"),
                _("Standard (SDR)"),
            ],
            changes_result=True,
        )
        self.source_hdr_combo.connect(
            "notify::selected",
            lambda row, _p: self.page.on_source_hdr_changed(
                SOURCE_HDR_MODES[row.get_selected()]
            ),
        )
        cards.add_row(adjust_list, self.source_hdr_combo)

        # Turning and mirroring: four buttons in one card.
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        for icon, label, degrees in (
            ("object-rotate-left-symbolic", _("Rotate 90° Left"), -90),
            ("object-rotate-right-symbolic", _("Rotate 90° Right"), 90),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=label)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.connect("clicked", lambda _b, d=degrees: self.page.on_rotate(d))
            buttons.append(button)
        self.flip_h_btn = self._flip_toggle(
            "object-flip-horizontal-symbolic", _("Flip Horizontal"), "horizontal"
        )
        self.flip_v_btn = self._flip_toggle(
            "object-flip-vertical-symbolic", _("Flip Vertical"), "vertical"
        )
        buttons.append(self.flip_h_btn)
        buttons.append(self.flip_v_btn)
        self.rotation_row = SettingRow(
            "object-rotate-right-symbolic",
            _("Rotation"),
            None,
            buttons,
            reset=self.page.on_reset_transform,
        )
        cards.add_row(adjust_list, self.rotation_row)
        self.update_transform_state(0, False, False)

        # The analysis of a long video runs in its card; the row says where it
        # stands while the card is closed.
        self.pause_panel = PausePanel(self.page)
        self.pause_row = SettingRow(
            "media-playback-pause-symbolic",
            _("Remove pauses"),
            _(
                "Made for speech: the pauses are found once the background noise is removed."
            ),
            self.pause_panel,
        )
        cards.add_row(adjust_list, self.pause_row)
        self.pause_panel.on_changed = self._show_pause_state
        self._show_pause_state()
        sidebar_box.append(adjust_box)

        sidebar_box.append(self._create_effects_group(cards))

        # Audio Cleaning Group (visible only when re-encode active)
        self.nr_sidebar_group = self._create_nr_sidebar_group(cards)
        self.nr_sidebar_group.set_visible(False)
        sidebar_box.append(self.nr_sidebar_group)

        # Trim Segments
        trim_group = Adw.PreferencesGroup(title=_("Trim Segments"))
        self.trim_group = trim_group
        segment_actions = Gtk.Box(spacing=4)
        add_button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add_button.add_css_class("flat")
        add_button.set_tooltip_text(_("Add segment manually"))
        add_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Add segment manually")]
        )
        add_button.connect("clicked", self.page._on_add_manual_segment_clicked)
        segment_actions.append(add_button)
        self.clear_segments_button = Gtk.Button(
            icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, sensitive=False
        )
        self.clear_segments_button.add_css_class("flat")
        self.clear_segments_button.set_tooltip_text(_("Clear all segments"))
        self.clear_segments_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Clear all segments")]
        )
        self.clear_segments_button.connect(
            "clicked", self.page._on_clear_all_segments_with_confirmation
        )
        segment_actions.append(self.clear_segments_button)
        trim_group.set_header_suffix(segment_actions)

        # A list view builds rows only for what is on screen; removing pauses
        # leaves hundreds of segments.
        self.segment_items = Gtk.StringList()
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_segment_row)
        factory.connect("bind", self._bind_segment_row)
        segments_list = Gtk.ListView(
            model=Gtk.NoSelection(model=self.segment_items),
            factory=factory,
            single_click_activate=True,
        )
        segments_list.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Trim Segments")]
        )
        segments_list.connect(
            "activate",
            lambda _view, position: self.page._on_goto_segment_clicked(
                None, self.page.trim_segments[position]["start"]
            ),
        )
        self.segments_stack = Gtk.Stack(vhomogeneous=False)
        self.segments_scroller = Gtk.ScrolledWindow(
            child=segments_list,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            max_content_height=360,
            css_classes=["card"],
        )
        self.segments_stack.add_named(self.segments_scroller, "list")
        placeholder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=18,
            margin_bottom=18,
            margin_start=12,
            margin_end=12,
        )
        placeholder.append(
            Gtk.Image(
                icon_name="bookmark-new-symbolic",
                pixel_size=32,
                css_classes=["dim-label"],
            )
        )
        placeholder.append(
            Gtk.Label(
                label=_("Press M or use the mark button to add segments"),
                css_classes=["dim-label"],
                wrap=True,
                justify=Gtk.Justification.CENTER,
            )
        )
        placeholder.append(
            Gtk.Label(
                label=_("Or remove the pauses to keep only the speech."),
                css_classes=["dim-label"],
                wrap=True,
                justify=Gtk.Justification.CENTER,
            )
        )
        empty = Gtk.Box(css_classes=["card"])
        empty.append(placeholder)
        placeholder.set_hexpand(True)
        self.segments_stack.add_named(empty, "empty")
        self.segments_stack.set_visible_child_name("empty")
        trim_group.add(self.segments_stack)
        if hasattr(self.page.app, "help_tooltips"):
            self.page.app.help_tooltips.add_tooltip(trim_group, "segments")
        sidebar_box.append(trim_group)

        # Shown once a segment is marked: keep it (joined or apart) or remove
        # it from the video. The order is OUTPUT_MODES'.
        self.output_group = Adw.PreferencesGroup(visible=False)
        output_mode_model = Gtk.StringList.new(
            [
                _("Join segments into a single file"),
                _("Save each segment as a separate file"),
                _("Remove segments and keep the rest"),
            ]
        )
        self.output_mode_combo = Adw.ComboRow(
            title=_("Segment output"), use_subtitle=True
        )
        self.output_mode_combo.set_model(output_mode_model)
        self.output_mode_combo.set_tooltip_text(
            _("Choose how marked segments are saved")
        )
        self.output_mode_combo.connect(
            "notify::selected", self.page._on_output_mode_changed
        )
        self.output_group.add(self.output_mode_combo)
        sidebar_box.append(self.output_group)

        self.apply_tooltips()

    def _show_pause_state(self):
        self.pause_row.show_value(*self.pause_panel.summary())

    def _create_effects_group(self, cards):
        """Noise reduction, sharpening, stabilization and a look or shader."""
        box, listbox = cards.add_group(_("Effects"))
        levels = [_("Off"), _("Light"), _("Medium"), _("Strong")]
        self.denoise_combo = StepRow(
            "weather-fog-symbolic",
            _("Noise reduction"),
            self._help.get("denoise"),
            levels,
        )
        self.sharpen_combo = StepRow(
            "find-location-symbolic",
            _("Sharpening"),
            self._help.get("sharpen"),
            levels,
        )
        for combo, name in (
            (self.denoise_combo, "denoise"),
            (self.sharpen_combo, "sharpen"),
        ):
            combo.steps.connect(
                "notify::selected",
                lambda tiles, _p, n=name: self.page.on_effect_changed(
                    n, EFFECT_LEVELS[tiles.get_selected()]
                ),
            )
            combo.set_visible(False)
            cards.add_row(listbox, combo)

        self.stabilize_row = SwitchRow(
            "camera-video-symbolic",
            _("Stabilization"),
            self._help.get(
                "stabilize", _("Applied when converting, not in the preview")
            ),
        )
        self.stabilize_row.switch.connect(
            "notify::active",
            lambda switch, _p: self.page.on_effect_changed(
                "stabilize", switch.get_active()
            ),
        )
        self.stabilize_row.set_visible(False)
        cards.add_row(listbox, self.stabilize_row)

        choose = Gtk.Button(
            label=_("Choose a color look (.cube) or shader (.hook)"),
            halign=Gtk.Align.START,
        )
        choose.get_child().set_wrap(True)
        choose.connect("clicked", self._on_choose_effect_file)
        clear_label = _("Remove the look or shader")
        self.effect_clear_button = Gtk.Button(
            label=clear_label, halign=Gtk.Align.START, sensitive=False
        )
        self.effect_clear_button.connect(
            "clicked", lambda _b: self.page.on_effect_changed("effect_file", "")
        )
        effect_buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        effect_buttons.append(choose)
        effect_buttons.append(self.effect_clear_button)
        self.effect_file_row = SettingRow(
            "applications-graphics-symbolic",
            _("Look or shader"),
            self._help.get("effect_file"),
            effect_buttons,
        )
        cards.add_row(listbox, self.effect_file_row)

        # Which effects this FFmpeg can apply: hqdn3d needs a GPL build,
        # stabilization libvidstab, shaders libplacebo. Asked off the GTK
        # thread; an option the build lacks is not offered.
        self._shaders_available = False

        def probe():
            names = available_filters()
            GLib.idle_add(show, names)

        def show(names):
            self.denoise_combo.set_visible("hqdn3d" in names)
            self.sharpen_combo.set_visible("cas" in names)
            self.stabilize_row.set_visible("vidstabdetect" in names)
            self._shaders_available = "libplacebo" in names
            # Shown rows count in the group's header from now on.
            cards.row_changed(self.denoise_combo)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=probe, daemon=True).start()
        return box

    def _on_choose_effect_file(self, _button):
        dialog = Gtk.FileDialog(title=_("Choose a Look or Shader"))
        file_filter = Gtk.FileFilter()
        patterns = ["*.cube", "*.3dl"] + (
            ["*.hook", "*.glsl"] if self._shaders_available else []
        )
        file_filter.set_name(_("Looks and shaders") + f" ({', '.join(patterns)})")
        for pattern in patterns:
            file_filter.add_pattern(pattern)
            file_filter.add_pattern(pattern.upper())
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)

        def done(dlg, result):
            try:
                gfile = dlg.open_finish(result)
            except GLib.Error:
                return
            if gfile is not None and gfile.get_path():
                self.page.on_effect_changed("effect_file", gfile.get_path())

        dialog.open(self.page.app.window, None, done)

    def update_source_hdr(self, mode) -> None:
        self.source_hdr_combo.set_selected(
            SOURCE_HDR_MODES.index(mode) if mode in SOURCE_HDR_MODES else 0
        )

    def update_effects_state(self, denoise, sharpen, stabilize, effect_file) -> None:
        """Show the file's effects on their rows."""
        self.denoise_combo.set_selected(
            EFFECT_LEVELS.index(denoise) if denoise in EFFECT_LEVELS else 0
        )
        self.sharpen_combo.set_selected(
            EFFECT_LEVELS.index(sharpen) if sharpen in EFFECT_LEVELS else 0
        )
        self.stabilize_row.set_active(bool(stabilize))
        self.effect_file_row.show_value(
            os.path.basename(effect_file) if effect_file else _("None"),
            bool(effect_file),
        )
        self.effect_clear_button.set_sensitive(bool(effect_file))

    def _flip_toggle(self, icon, label, direction):
        button = Gtk.ToggleButton(icon_name=icon, tooltip_text=label)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", lambda _b: self.page.on_flip(direction))
        return button

    def update_transform_state(self, rotation, flip_h, flip_v) -> None:
        """Show the current rotation and mirroring on their row."""
        parts = [f"{rotation}°"] if rotation else []
        if flip_h:
            parts.append(_("Flip Horizontal"))
        if flip_v:
            parts.append(_("Flip Vertical"))
        self.rotation_row.show_value(" · ".join(parts) or "0°", bool(parts))
        self.flip_h_btn.set_active(flip_h)
        self.flip_v_btn.set_active(flip_v)

    def _setup_segment_row(self, _factory, item):
        row = Gtk.Box(
            spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=6
        )
        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER
        )
        row.title = Gtk.Label(xalign=0)
        row.subtitle = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"])
        text.append(row.title)
        text.append(row.subtitle)
        row.append(text)
        page = self.page
        for icon, label, handler in (
            (
                "document-edit-symbolic",
                _("Edit segment times"),
                lambda button: page._on_edit_segment_clicked(
                    button, item.get_position()
                ),
            ),
            (
                "edit-delete-symbolic",
                _("Remove segment"),
                lambda button: page._on_remove_segment_clicked(
                    button, page.trim_segments[item.get_position()]
                ),
            ),
        ):
            button = Gtk.Button(
                icon_name=icon,
                css_classes=["flat"],
                tooltip_text=label,
                valign=Gtk.Align.CENTER,
            )
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.connect("clicked", handler)
            row.append(button)
        row.set_tooltip_text(_("Go to segment start"))
        item.set_child(row)

    def _bind_segment_row(self, _factory, item):
        position = item.get_position()
        segment = self.page.trim_segments[position]
        row = item.get_child()
        title = _("Segment {num}").format(num=position + 1)
        subtitle = _("{start} → {end} ({duration})").format(
            start=self.page.format_time_precise(segment["start"]),
            end=self.page.format_time_precise(segment["end"]),
            duration=self.page.format_time_precise(segment["end"] - segment["start"]),
        )
        row.title.set_label(title)
        row.subtitle.set_label(subtitle)
        item.set_accessible_label(f"{title}, {subtitle}")

    def fit_segment_list(self, count) -> None:
        """Show every row up to the cap, then scroll within it.

        The sidebar scrolls, and a scrolled window gives its content only its
        minimum height: the list's own scroller asked for one row. Ten rows
        already pass the cap, and measuring hundreds cost 40 ms.
        """
        cap = self.segments_scroller.get_max_content_height()
        rows = (
            cap
            if count >= 10
            else self.segments_scroller.get_child().measure(
                Gtk.Orientation.VERTICAL, -1
            )[1]
        )
        self.segments_scroller.set_min_content_height(min(rows, cap))

    def update_segment_actions(self, count) -> None:
        self.clear_segments_button.set_sensitive(count > 0)
        self.segments_only_button.set_visible(count > 0)
        if not count:
            self.segments_only_button.set_active(False)
        self.output_group.set_visible(count > 0)
        removing = self.page.output_mode == "remove"
        label = (
            _("Play without the removed segments")
            if removing
            else _("Play only the segments")
        )
        self.segments_only_button.set_tooltip_text(label)
        self.segments_only_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [label]
        )
        self.trim_group.set_description(
            _("The marked segments are cut out of the converted video.")
            if removing and count
            else None
        )

    def update_for_force_copy_state(self, force_copy_enabled) -> None:
        """Say that editing the picture takes this video out of copy mode."""
        if hasattr(self, "copy_mode_note"):
            self.copy_mode_note.set_visible(force_copy_enabled)

    def _create_adjustment_row(
        self,
        cards,
        listbox,
        icon,
        title,
        description,
        limits,
        on_change,
        on_reset,
        describe,
    ):
        """A row showing the adjustment, its slider and Reset in its card."""
        low, high, default = limits
        scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            round_digits=2,
            width_request=220,
        )
        scale.set_adjustment(
            Gtk.Adjustment(
                value=default,
                lower=low,
                upper=high,
                step_increment=0.01,
                page_increment=0.1,
            )
        )
        scale.set_tooltip_text(title)
        scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
        row = SettingRow(icon, title, description, scale, reset=on_reset)
        cards.add_row(listbox, row)

        def refresh(*_args):
            text = describe(scale.get_value())
            scale.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [text])
            row.show_value(text, abs(scale.get_value() - default) > 1e-6)

        scale.connect("value-changed", on_change)
        scale.connect("value-changed", refresh)
        refresh()
        return scale, row
