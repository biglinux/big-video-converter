import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Setup translation
import gettext

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

_ = gettext.gettext

from utils.job_options import EFFECT_LEVELS, SOURCE_HDR_MODES
from utils.video_settings import available_filters

from ui.crop_overlay import CropOverlay


class VideoEditUI:
    def __init__(self, page):
        self.page = page
        self._handler_ids: list[tuple] = []  # [(widget, handler_id), ...]
        self.hide_timer_id = None

    def create_page(self):
        """Create the main page layout and all UI elements"""
        # Main container with a vertical layout. Top (video) expands, bottom (toolbar) is fixed.
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_vexpand(True)
        page.add_css_class("bvc-editor-page")
        page.set_spacing(14)
        page.set_margin_start(18)
        page.set_margin_end(18)
        page.set_margin_top(14)
        page.set_margin_bottom(14)

        # TOP: Video preview area
        self.video_overlay = Gtk.Overlay()
        self.video_overlay.set_vexpand(True)
        self.video_overlay.set_hexpand(True)
        self.video_overlay.add_css_class("bvc-editor-stage")

        video_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        video_box.set_vexpand(True)
        video_box.set_hexpand(True)
        video_box.set_size_request(-1, 260)

        # GLArea provides the OpenGL context for the MPV render API
        self.preview_video = Gtk.GLArea()
        self.preview_video.set_hexpand(True)
        self.preview_video.set_vexpand(True)
        self.preview_video.set_auto_render(False)  # MPV controls rendering
        video_box.append(self.preview_video)

        self.video_overlay.set_child(video_box)

        # Crop overlay (hidden by default, shown in crop edit mode)
        self.crop_overlay = CropOverlay()
        self.crop_overlay.set_visible(False)
        self.video_overlay.add_overlay(self.crop_overlay)

        self._create_overlay_controls(self.video_overlay)
        page.append(self.video_overlay)

        # BOTTOM: Compact editing toolbar
        self.toolbar = self._create_compact_toolbar()
        page.append(self.toolbar)

        return page

    def _create_overlay_controls(self, overlay):
        """Create YouTube-style overlay controls at bottom of video"""
        controls_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        controls_container.set_valign(Gtk.Align.END)
        controls_container.set_halign(Gtk.Align.FILL)
        controls_container.set_margin_start(12)
        controls_container.set_margin_end(12)
        controls_container.set_margin_bottom(12)
        # Not "toolbar": desktop themes recolour that class (BigStyle paints it
        # with the header bar colours), and this bar sits on video, so it keeps
        # the fixed dark OSD look in every theme and focus state.
        controls_container.add_css_class("osd")
        controls_container.add_css_class("bvc-editor-toolbar")

        slider_overlay = Gtk.Overlay()

        adjustment = Gtk.Adjustment(value=0, lower=0, upper=100, step_increment=1)
        self.position_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment
        )
        self.position_scale.set_draw_value(False)
        self.position_scale.set_hexpand(True)
        self.position_scale.set_focusable(True)
        self.position_scale.set_tooltip_text(_("Video position"))
        self.position_scale.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Video position")],
        )
        self.page.position_changed_handler_id = self.position_scale.connect(
            "value-changed", self.page.on_position_changed
        )
        self._handler_ids.append((
            self.position_scale,
            self.page.position_changed_handler_id,
        ))
        self.position_scale.set_can_target(False)

        slider_overlay.set_child(self.position_scale)

        self.segment_markers_canvas = Gtk.DrawingArea()
        self.segment_markers_canvas.set_draw_func(self._draw_segment_markers)
        self.segment_markers_canvas.set_can_target(True)
        marker_hint = _("Drag to seek or adjust segment edges")
        self.segment_markers_canvas.set_tooltip_text(marker_hint)
        self.segment_markers_canvas.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [marker_hint],
        )

        slider_overlay.add_overlay(self.segment_markers_canvas)
        self._setup_drag_controllers()

        controls_container.append(slider_overlay)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        button_row.set_halign(Gtk.Align.CENTER)

        # --- New Single Mark Button ---
        mark_button = Gtk.Button(icon_name="bookmark-new-symbolic")
        mark_button.set_tooltip_text(_("Mark segment point"))
        mark_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Mark segment point")],
        )
        mark_button.connect("clicked", self.page.on_mark_segment_point)
        mark_button.add_css_class("bvc-icon-button")
        button_row.append(mark_button)

        # --- Feedback for marking ---
        self.mark_time_label = Gtk.Label(label="")
        self.mark_time_label.set_visible(False)
        button_row.append(self.mark_time_label)

        self.mark_cancel_button = Gtk.Button(icon_name="edit-clear-symbolic")
        self.mark_cancel_button.set_tooltip_text(_("Cancel current mark"))
        self.mark_cancel_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Cancel current mark")],
        )
        self.mark_cancel_button.add_css_class("flat")
        self.mark_cancel_button.connect("clicked", self.page.on_mark_cancel)
        self.mark_cancel_button.set_visible(False)
        self.mark_cancel_button.add_css_class("bvc-icon-button")
        button_row.append(self.mark_cancel_button)

        button_row.append(
            Gtk.Separator(
                orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6
            )
        )

        # Seek buttons
        seek_back_button = Gtk.Button(
            icon_name="media-seek-backward-symbolic",
            tooltip_text=_("Back 1 second (Left)"),
        )
        seek_back_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Back 1 second")],
        )
        seek_back_button.connect("clicked", lambda b: self.page.seek_relative(-1))
        seek_back_button.add_css_class("bvc-icon-button")
        button_row.append(seek_back_button)

        prev_frame_button = Gtk.Button(
            icon_name="go-previous-symbolic", tooltip_text=_("Previous frame (,)")
        )
        prev_frame_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Previous frame")],
        )
        prev_frame_button.connect(
            "clicked",
            lambda b: self.page.seek_relative(
                -1 / self.page.video_fps if self.page.video_fps > 0 else -1 / 25
            ),
        )
        prev_frame_button.add_css_class("bvc-icon-button")
        button_row.append(prev_frame_button)

        # Playback controls
        self.play_pause_button = Gtk.Button()
        self.play_pause_button.set_icon_name('media-playback-start-symbolic')
        self.play_pause_button.add_css_class("circular")
        self.play_pause_button.set_tooltip_text(_("Play/Pause (Space)"))
        self.play_pause_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Play/Pause")],
        )
        self.play_pause_button.connect("clicked", self.page.on_play_pause_clicked)
        self.play_pause_button.add_css_class("bvc-icon-button")
        button_row.append(self.play_pause_button)

        next_frame_button = Gtk.Button(
            icon_name="go-next-symbolic", tooltip_text=_("Next frame (.)")
        )
        next_frame_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Next frame")],
        )
        next_frame_button.connect(
            "clicked",
            lambda b: self.page.seek_relative(
                1 / self.page.video_fps if self.page.video_fps > 0 else 1 / 25
            ),
        )
        next_frame_button.add_css_class("bvc-icon-button")
        button_row.append(next_frame_button)

        seek_fwd_button = Gtk.Button(
            icon_name="media-seek-forward-symbolic",
            tooltip_text=_("Forward 1 second (Right)"),
        )
        seek_fwd_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Forward 1 second")],
        )
        seek_fwd_button.connect("clicked", lambda b: self.page.seek_relative(1))
        seek_fwd_button.add_css_class("bvc-icon-button")
        button_row.append(seek_fwd_button)

        # Playback speed button with popover
        self._speed_values = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
        self._current_speed = 1.0

        speed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        speed_box.set_margin_top(6)
        speed_box.set_margin_bottom(6)
        speed_box.set_margin_start(6)
        speed_box.set_margin_end(6)
        for sv in self._speed_values:
            btn = Gtk.Button(label=f"{sv}x")
            btn.add_css_class("flat")
            btn.connect("clicked", self._on_speed_btn_clicked, sv)
            speed_box.append(btn)

        speed_popover = Gtk.Popover(child=speed_box)
        speed_popover.set_autohide(True)

        speed_summary = _("Playback speed: {speed}x").format(
            speed=self._current_speed
        )
        self.speed_button = Gtk.MenuButton(
            label="1x",
            popover=speed_popover,
            tooltip_text=speed_summary,
        )
        self.speed_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [speed_summary],
        )
        button_row.append(self.speed_button)

        button_row.append(
            Gtk.Separator(
                orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6
            )
        )

        # Volume and Track controls
        self.volume_button = self._create_volume_button()
        button_row.append(self.volume_button)

        self.audio_track_button = Gtk.MenuButton(
            icon_name="audio-speakers-symbolic", tooltip_text=_("Audio Track")
        )
        self.audio_track_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Audio Track")],
        )
        self.audio_track_menu = Gio.Menu()
        self.audio_track_button.set_menu_model(self.audio_track_menu)
        self.audio_track_button.add_css_class("bvc-icon-button")
        button_row.append(self.audio_track_button)

        self.subtitle_button = Gtk.MenuButton(
            icon_name="format-text-underline-symbolic", tooltip_text=_("Subtitles")
        )
        self.subtitle_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Subtitles")],
        )
        self.subtitle_menu = Gio.Menu()
        self.subtitle_button.set_menu_model(self.subtitle_menu)
        self.subtitle_button.add_css_class("bvc-icon-button")
        button_row.append(self.subtitle_button)

        # Spacer to push fullscreen to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        button_row.append(spacer)

        # In fullscreen the header and its sidebar button are hidden, so the
        # editing tools get their own toggle here.
        self.sidebar_button = Gtk.ToggleButton(
            icon_name="sidebar-show-symbolic",
            tooltip_text=_("Show editing tools ({shortcut})").format(shortcut="F9"),
            visible=False,
        )
        self.sidebar_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Show editing tools")]
        )
        self.sidebar_button.add_css_class("bvc-icon-button")
        button_row.append(self.sidebar_button)

        # Fullscreen button
        self.fullscreen_button = Gtk.Button()
        self.fullscreen_button.set_icon_name('view-fullscreen-symbolic')
        self.fullscreen_button.set_tooltip_text(_("Toggle Fullscreen"))
        self.fullscreen_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Toggle Fullscreen")],
        )
        self.fullscreen_button.connect("clicked", self.page.on_toggle_fullscreen)
        self.fullscreen_button.add_css_class("bvc-icon-button")
        button_row.append(self.fullscreen_button)

        controls_container.append(button_row)

        labels_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.position_label = Gtk.Label(label="0:00.000 / 0:00.000")
        self.position_label.set_halign(Gtk.Align.START)
        self.position_label.set_hexpand(True)
        labels_box.append(self.position_label)

        self.frame_label = Gtk.Label(
            label=_("Frame: {current} / {total}").format(current=0, total=0)
        )
        self.frame_label.set_halign(Gtk.Align.END)
        self.frame_label.set_hexpand(True)
        labels_box.append(self.frame_label)
        controls_container.append(labels_box)

        overlay.add_overlay(controls_container)
        self.overlay_controls = controls_container

        # Auto-hide functionality
        self.hide_timer_id = None
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", self._on_video_mouse_enter)
        motion.connect("motion", self._on_video_mouse_motion)
        motion.connect("leave", self._on_video_mouse_leave)
        overlay.add_controller(motion)

    def _create_compact_toolbar(self):
        """Creates the unified, compact toolbar below the video."""
        toolbar_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=8,
            min_children_per_line=1, max_children_per_line=2,
        )
        toolbar_box.set_margin_start(12)
        toolbar_box.set_margin_end(12)
        toolbar_box.set_margin_top(6)
        toolbar_box.set_margin_bottom(6)
        toolbar_box.add_css_class("bvc-editor-inspector")

        # --- Crop Controls ---
        crop_grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        self.crop_grid = crop_grid
        crop_grid.set_valign(Gtk.Align.CENTER)

        crop_controls = (
            (_("Left"), "left", _("Crop from left")),
            (_("Right"), "right", _("Crop from right")),
            (_("Top"), "top", _("Crop from top")),
            (_("Bottom"), "bottom", _("Crop from bottom")),
        )
        self.crop_spins = {}
        for i, (label_text, key, accessible_label) in enumerate(crop_controls):
            label = Gtk.Label(label=label_text, xalign=0)
            adjustment = Gtk.Adjustment(value=0, lower=0, upper=9999, step_increment=1)
            spin = Gtk.SpinButton(adjustment=adjustment, numeric=True, width_chars=5)
            spin.set_tooltip_text(accessible_label)
            spin.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [accessible_label],
            )
            self.crop_spins[key] = spin
            spin.connect("value-changed", self.page.on_crop_value_changed)

            col, row = (i % 2, i // 2)
            crop_grid.attach(label, col * 2, row, 1, 1)
            crop_grid.attach(spin, col * 2 + 1, row, 1, 1)

        self.crop_left_spin = self.crop_spins["left"]
        self.crop_right_spin = self.crop_spins["right"]
        self.crop_top_spin = self.crop_spins["top"]
        self.crop_bottom_spin = self.crop_spins["bottom"]

        # Crop edit toggle button
        self.crop_edit_btn = Gtk.ToggleButton()
        self.crop_edit_btn.set_icon_name("tool-crop-symbolic")
        self.crop_edit_btn.set_tooltip_text(_("Visual crop editor"))
        self.crop_edit_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Visual crop editor")],
        )
        self.crop_edit_btn.set_valign(Gtk.Align.CENTER)
        self.crop_edit_btn.connect("toggled", self._on_crop_edit_toggled)

        crop_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        crop_box.append(crop_grid)

        aspect_model = Gtk.StringList.new([
            _("Free"),
            _("Original"),
            "16:9",
            "9:16",
            "1:1",
            "4:5",
            "3:2",
            "2.39:1",
        ])
        self.crop_aspect_combo = Gtk.DropDown(model=aspect_model)
        self.crop_aspect_combo.set_tooltip_text(_("Crop aspect ratio"))
        self.crop_aspect_combo.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Crop aspect ratio")],
        )
        self.crop_aspect_combo.connect(
            "notify::selected", self.page.on_crop_aspect_changed
        )
        crop_box.append(self.crop_aspect_combo)

        self.crop_edit_btn.add_css_class("bvc-icon-button")
        crop_box.append(self.crop_edit_btn)

        # Add tooltip to crop grid
        if hasattr(self.page.app, "tooltip_helper"):
            self.page.app.tooltip_helper.add_tooltip(crop_grid, "crop")

        toolbar_box.append(crop_box)
        # --- Info Display ---
        info_grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        info_grid.set_valign(Gtk.Align.CENTER)
        info_grid.set_hexpand(True)

        self.info_dimensions_label = self._add_info_row(info_grid, 0, _("Resolution:"))
        self.info_codec_label = self._add_info_row(info_grid, 1, _("Codec:"))
        self.info_filesize_label = self._add_info_row(info_grid, 2, _("Size:"))
        self.info_duration_label = self._add_info_row(info_grid, 3, _("Duration:"))

        toolbar_box.append(info_grid)

        return toolbar_box

    def _add_info_row(self, grid, row_index, title):
        """Helper to add a row to the info grid."""
        title_label = Gtk.Label(label=title, xalign=1, css_classes=["dim-label"])
        value_label = Gtk.Label(
            label="...", xalign=0, selectable=True, ellipsize=Pango.EllipsizeMode.END
        )
        grid.attach(title_label, 0, row_index, 1, 1)
        grid.attach(value_label, 1, row_index, 1, 1)
        return value_label

    def _on_speed_btn_clicked(self, button, speed_value):
        """Handle playback speed selection from popover button."""
        self._current_speed = speed_value
        self.speed_button.set_label(f"{speed_value}x")
        speed_summary = _("Playback speed: {speed}x").format(speed=speed_value)
        self.speed_button.set_tooltip_text(speed_summary)
        self.speed_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [speed_summary],
        )
        self.speed_button.get_popover().popdown()
        self.page.on_speed_changed(speed_value)

    def _on_crop_edit_toggled(self, button):
        """Toggle visual crop editor mode."""
        active = button.get_active()
        self.page.on_crop_edit_toggled(active)

    def _create_volume_button(self):
        """Creates the volume button with its popover."""
        volume_button = Gtk.MenuButton(
            icon_name='audio-volume-high-symbolic', tooltip_text=_("Volume")
        )
        volume_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Volume")],
        )

        volume_popover = Gtk.Popover()
        volume_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        self.volume_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.VERTICAL, 0.0, 1.0, 0.05
        )
        self.volume_scale.set_value(1.0)
        self.volume_scale.set_inverted(True)
        self.volume_scale.set_size_request(-1, 150)
        self.volume_scale.set_draw_value(True)
        self.volume_scale.set_value_pos(Gtk.PositionType.BOTTOM)
        self.volume_scale.set_tooltip_text(_("Volume"))
        self.volume_scale.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Volume")],
        )
        self.volume_scale.connect("value-changed", self.page.on_volume_changed)
        volume_box.append(self.volume_scale)

        volume_popover.set_child(volume_box)
        volume_button.set_popover(volume_popover)
        return volume_button

    def _create_nr_sidebar_group(self):
        """Creates the noise reduction group for the editing sidebar."""
        nr_group = Adw.PreferencesGroup()

        # Row to open the full noise dialog — mirrors main screen's Audio Cleaning row
        self.nr_settings_row = Adw.ActionRow(title=_("Audio Settings"))
        self.nr_settings_row.add_prefix(
            Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        )
        self.nr_settings_row.add_suffix(
            Gtk.Image.new_from_icon_name("go-next-symbolic")
        )
        self.nr_settings_row.set_activatable(True)
        self.nr_settings_row.connect("activated", self._on_nr_settings_activated)
        nr_group.add(self.nr_settings_row)

        # Set initial subtitle from the main screen's audio cleaning row
        self._sync_nr_subtitle()

        return nr_group

    def _sync_nr_subtitle(self):
        """Sync the editor's Audio Cleaning subtitle with the main sidebar row."""
        if not hasattr(self, "nr_settings_row"):
            return
        app = self.page.app
        if hasattr(app, "_audio_cleaning_row"):
            subtitle = app._audio_cleaning_row.get_subtitle()
            self.nr_settings_row.set_subtitle(subtitle or "")

    def _on_nr_settings_activated(self, _row):
        """Open the noise cleaning dialog from the editing sidebar."""
        from ui.noise_dialog import show_noise_dialog

        show_noise_dialog(self.page.app.window, self.page.app)

    def populate_sidebar(self, sidebar_box) -> None:
        """Populate the sidebar with video adjustment controls and trim marking"""
        while child := sidebar_box.get_first_child():
            sidebar_box.remove(child)

        # The controls stay usable in copy mode: freeze() re-encodes a file
        # once it is cropped, adjusted or turned, and this note says so.
        self.copy_mode_note = Gtk.Label(
            label=_("The video is being copied without re-encoding. Cropping, adjusting, turning or adding effects to the picture re-encodes this video."),
            wrap=True, xalign=0, css_classes=["dim-label"], visible=False,
        )
        sidebar_box.append(self.copy_mode_note)

        # --- Image ---
        adjust_group = Adw.PreferencesGroup()
        adjust_group.set_title(_("Image"))
        self._image_refreshers = []
        self.image_reset_button = self._group_reset_button(
            _("Reset image adjustments"), self._on_reset_image_clicked
        )
        adjust_group.set_header_suffix(self.image_reset_button)

        def percent(value):
            return f"{round(value * 100):+d}%" if round(value * 100) else "0%"

        self.brightness_scale, self.brightness_row = self._create_adjustment_row(
            adjust_group, _("Brightness"), -1.0, 1.0, 0.0,
            self.page.on_brightness_changed, self.page.reset_brightness, percent,
        )
        self.contrast_scale, self.contrast_row = self._create_adjustment_row(
            adjust_group, _("Contrast"), -1.0, 1.0, 0.0,
            self.page.on_contrast_changed, self.page.reset_contrast, percent,
        )
        self.saturation_scale, self.saturation_row = self._create_adjustment_row(
            adjust_group, _("Saturation"), 0.0, 2.0, 1.0,
            self.page.on_saturation_changed, self.page.reset_saturation,
            lambda value: percent(value - 1.0),
        )
        self.hue_scale, self.hue_row = self._create_adjustment_row(
            adjust_group, _("Hue"), -1.0, 1.0, 0.0,
            self.page.on_hue_changed, self.page.reset_hue, percent,
        )
        # For a file that does not say how its colours are coded: HDR read
        # as SDR looks grey and washed out, and is converted that way too.
        self.source_hdr_combo = Adw.ComboRow(
            title=_("Source colours"),
            model=Gtk.StringList.new([_("As the file says"), _("HDR10 (PQ)"), _("HDR (HLG)"),
                                      _("Standard (SDR)")]),
        )
        self.source_hdr_combo.connect("notify::selected", lambda row, _p: self.page.on_source_hdr_changed(
            SOURCE_HDR_MODES[row.get_selected()]))
        adjust_group.add(self.source_hdr_combo)
        self._refresh_image_reset()
        sidebar_box.append(adjust_group)

        # --- Orientation ---
        transform_group = Adw.PreferencesGroup()
        transform_group.set_title(_("Orientation"))
        self.transform_reset_button = self._group_reset_button(
            _("Reset Transform"), lambda _b: self.page.on_reset_transform()
        )
        transform_group.set_header_suffix(self.transform_reset_button)

        # One row: turning and mirroring are four buttons, not two sections.
        self.rotation_row = Adw.ActionRow(title=_("Rotation"))
        for icon, label, degrees in (
            ("object-rotate-left-symbolic", _("Rotate 90° Left"), -90),
            ("object-rotate-right-symbolic", _("Rotate 90° Right"), 90),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=label, valign=Gtk.Align.CENTER)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.add_css_class("flat")
            button.connect("clicked", lambda _b, d=degrees: self.page.on_rotate(d))
            self.rotation_row.add_suffix(button)
        self.flip_h_btn = self._flip_toggle(
            "object-flip-horizontal-symbolic", _("Flip Horizontal"), "horizontal"
        )
        self.flip_v_btn = self._flip_toggle(
            "object-flip-vertical-symbolic", _("Flip Vertical"), "vertical"
        )
        self.rotation_row.add_suffix(self.flip_h_btn)
        self.rotation_row.add_suffix(self.flip_v_btn)
        transform_group.add(self.rotation_row)
        self.update_transform_state(0, False, False)
        sidebar_box.append(transform_group)

        sidebar_box.append(self._create_effects_group())

        # --- Audio Cleaning Group (visible only when re-encode active) ---
        self.nr_sidebar_group = self._create_nr_sidebar_group()
        self.nr_sidebar_group.set_visible(False)
        sidebar_box.append(self.nr_sidebar_group)

        # --- Trim Segments ---
        trim_group = Adw.PreferencesGroup(title=_("Trim Segments"))
        self.trim_group = trim_group
        segment_actions = Gtk.Box(spacing=4)
        add_button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add_button.add_css_class("flat")
        add_button.set_tooltip_text(_("Add segment manually"))
        add_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Add segment manually")])
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

        self.segments_listbox = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"]
        )
        placeholder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6,
            margin_top=18, margin_bottom=18, margin_start=12, margin_end=12,
        )
        placeholder.append(Gtk.Image(
            icon_name="bookmark-new-symbolic", pixel_size=32, css_classes=["dim-label"]
        ))
        placeholder.append(Gtk.Label(
            label=_("Press M or use the mark button to add segments"),
            css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER,
        ))
        self.segments_listbox.set_placeholder(placeholder)
        self.segments_listbox.connect(
            "row-activated", lambda _box, row: self.page._on_goto_segment_clicked(None, row.segment_start)
        )
        trim_group.add(self.segments_listbox)
        if hasattr(self.page.app, "tooltip_helper"):
            self.page.app.tooltip_helper.add_tooltip(trim_group, "segments")
        sidebar_box.append(trim_group)

        # Join/split only means something with two or more segments.
        self.output_group = Adw.PreferencesGroup(visible=False)
        output_mode_model = Gtk.StringList.new([
            _("Join segments into a single file"),
            _("Save each segment as a separate file"),
        ])
        self.output_mode_combo = Adw.ComboRow(
            title=_("Segment output"), use_subtitle=True
        )
        self.output_mode_combo.set_model(output_mode_model)
        self.output_mode_combo.set_tooltip_text(_("Choose how marked segments are saved"))
        self.output_mode_combo.connect(
            "notify::selected", self.page._on_output_mode_changed
        )
        self.output_group.add(self.output_mode_combo)
        sidebar_box.append(self.output_group)

        self.apply_tooltips()

    def _create_effects_group(self):
        """Noise reduction, sharpening, stabilization and a look or shader."""
        group = Adw.PreferencesGroup(title=_("Effects"))
        self.effects_reset_button = self._group_reset_button(
            _("Reset effects"), lambda _b: self.page.on_reset_effects()
        )
        group.set_header_suffix(self.effects_reset_button)

        levels = Gtk.StringList.new([_("Off"), _("Light"), _("Medium"), _("Strong")])
        self.denoise_combo = Adw.ComboRow(title=_("Noise reduction"), model=levels, visible=False)
        self.sharpen_combo = Adw.ComboRow(title=_("Sharpening"), model=levels, visible=False)
        for combo, name in ((self.denoise_combo, "denoise"), (self.sharpen_combo, "sharpen")):
            combo.connect("notify::selected", lambda row, _p, n=name: self.page.on_effect_changed(
                n, EFFECT_LEVELS[row.get_selected()]))
            group.add(combo)

        self.stabilize_row = Adw.SwitchRow(
            title=_("Stabilization"), subtitle=_("Applied when converting, not in the preview"),
            visible=False,
        )
        self.stabilize_row.connect(
            "notify::active", lambda row, _p: self.page.on_effect_changed("stabilize", row.get_active())
        )
        group.add(self.stabilize_row)

        self.effect_file_row = Adw.ActionRow(title=_("Look or shader"), subtitle=_("None"))
        choose_label = _("Choose a colour look (.cube) or shader (.hook)")
        choose = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=choose_label,
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        choose.update_property([Gtk.AccessibleProperty.LABEL], [choose_label])
        choose.connect("clicked", self._on_choose_effect_file)
        clear_label = _("Remove the look or shader")
        self.effect_clear_button = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text=clear_label,
                                              valign=Gtk.Align.CENTER, css_classes=["flat"],
                                              sensitive=False)
        self.effect_clear_button.update_property([Gtk.AccessibleProperty.LABEL], [clear_label])
        self.effect_clear_button.connect(
            "clicked", lambda _b: self.page.on_effect_changed("effect_file", ""))
        self.effect_file_row.add_suffix(self.effect_clear_button)
        self.effect_file_row.add_suffix(choose)
        self.effect_file_row.set_activatable_widget(choose)
        group.add(self.effect_file_row)

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
            return GLib.SOURCE_REMOVE

        threading.Thread(target=probe, daemon=True).start()
        return group

    def _on_choose_effect_file(self, _button):
        dialog = Gtk.FileDialog(title=_("Choose a Look or Shader"))
        file_filter = Gtk.FileFilter()
        patterns = ["*.cube", "*.3dl"] + (["*.hook", "*.glsl"] if self._shaders_available else [])
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
        self.source_hdr_combo.set_selected(SOURCE_HDR_MODES.index(mode) if mode in SOURCE_HDR_MODES else 0)
        self._refresh_image_reset()

    def update_effects_state(self, denoise, sharpen, stabilize, effect_file) -> None:
        """Show the file's effects; enable the group's Reset only when set."""
        self.denoise_combo.set_selected(EFFECT_LEVELS.index(denoise) if denoise in EFFECT_LEVELS else 0)
        self.sharpen_combo.set_selected(EFFECT_LEVELS.index(sharpen) if sharpen in EFFECT_LEVELS else 0)
        self.stabilize_row.set_active(bool(stabilize))
        self.effect_file_row.set_subtitle(
            GLib.markup_escape_text(os.path.basename(effect_file)) if effect_file else _("None"))
        self.effect_clear_button.set_sensitive(bool(effect_file))
        self.effects_reset_button.set_sensitive(
            denoise != "off" or sharpen != "off" or bool(stabilize) or bool(effect_file))

    def _group_reset_button(self, label, on_clicked):
        button = Gtk.Button(
            icon_name="edit-undo-symbolic", tooltip_text=label,
            valign=Gtk.Align.CENTER, sensitive=False,
        )
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.add_css_class("flat")
        button.connect("clicked", on_clicked)
        return button

    def _flip_toggle(self, icon, label, direction):
        button = Gtk.ToggleButton(icon_name=icon, tooltip_text=label, valign=Gtk.Align.CENTER)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.add_css_class("flat")
        button.connect("clicked", lambda _b: self.page.on_flip(direction))
        return button

    def update_transform_state(self, rotation, flip_h, flip_v) -> None:
        """Show the current rotation and mirroring; enable Reset only when set."""
        self.rotation_row.set_subtitle(f"{rotation}°")
        self.flip_h_btn.set_active(flip_h)
        self.flip_v_btn.set_active(flip_v)
        self.transform_reset_button.set_sensitive(bool(rotation or flip_h or flip_v))

    def update_segment_actions(self, count) -> None:
        self.clear_segments_button.set_sensitive(count > 0)
        self.output_group.set_visible(count > 1)

    def _on_reset_image_clicked(self, _button):
        for reset in (self.page.reset_brightness, self.page.reset_contrast,
                      self.page.reset_saturation, self.page.reset_hue):
            reset()
        self.page.on_source_hdr_changed("auto")

    def _refresh_image_reset(self, *_args):
        source_set = getattr(self, "source_hdr_combo", None) is not None and self.source_hdr_combo.get_selected() != 0
        self.image_reset_button.set_sensitive(
            source_set or any(changed() for changed in self._image_refreshers))

    def update_for_force_copy_state(self, force_copy_enabled) -> None:
        """Say that editing the picture takes this video out of copy mode."""
        if hasattr(self, "copy_mode_note"):
            self.copy_mode_note.set_visible(force_copy_enabled)

    def _create_adjustment_row(
        self, container, title, min_val, max_val, default_val, on_change, on_reset, describe
    ):
        """Name, slider, current value and reset, all on one line."""
        # Not focusable: Tab goes straight to the slider, the row does nothing.
        row = Adw.PreferencesRow(title=title, activatable=False, focusable=False)
        box = Gtk.Box(spacing=6, margin_start=12, margin_end=6, margin_top=4, margin_bottom=4)
        name = Gtk.Label(label=title, xalign=0, width_chars=9, max_width_chars=12,
                         ellipsize=Pango.EllipsizeMode.END)
        box.append(name)

        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, round_digits=2)
        scale.set_adjustment(Gtk.Adjustment(
            value=default_val, lower=min_val, upper=max_val,
            step_increment=0.01, page_increment=0.1,
        ))
        scale.set_tooltip_text(title)
        scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self._forward_wheel_unless_focused(scale)
        box.append(scale)

        value_label = Gtk.Label(css_classes=["dim-label", "numeric"], width_chars=5, xalign=1)
        box.append(value_label)
        reset_label = _("Reset {setting} to default").format(setting=title)
        reset_button = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=reset_label,
                                  valign=Gtk.Align.CENTER)
        reset_button.update_property([Gtk.AccessibleProperty.LABEL], [reset_label])
        reset_button.connect("clicked", lambda _b: on_reset())
        reset_button.add_css_class("flat")
        reset_button.add_css_class("bvc-icon-button")
        box.append(reset_button)

        def is_changed():
            return abs(scale.get_value() - default_val) > 1e-6

        def refresh(*_args):
            text = describe(scale.get_value())
            value_label.set_text(text)
            scale.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [text])
            reset_button.set_sensitive(is_changed())
            self._refresh_image_reset()

        scale.connect("value-changed", on_change)
        scale.connect("value-changed", refresh)
        self._image_refreshers.append(is_changed)
        refresh()

        row.set_child(box)
        container.add(row)
        return scale, row

    @staticmethod
    def _forward_wheel_unless_focused(scale):
        """Scrolling the sidebar must not change a slider the pointer crosses."""
        controller = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_scroll(ctrl, _dx, dy):
            if scale.has_focus():
                return False
            scroller = scale.get_ancestor(Gtk.ScrolledWindow)
            if scroller is None:
                return True
            if ctrl.get_unit() == Gdk.ScrollUnit.WHEEL:
                dy *= 48
            adjustment = scroller.get_vadjustment()
            adjustment.set_value(adjustment.get_value() + dy)
            return True

        controller.connect("scroll", on_scroll)
        scale.add_controller(controller)

    def _draw_segment_markers(self, area, cr, width, height):
        """Draw segment markers on the progress bar"""
        if not self.page.current_video_path:
            return

        if not hasattr(self.page, "mpv_player") or not self.page.mpv_player:
            return

        duration = self.page.mpv_player.get_duration()
        if duration <= 0:
            return

        # Draw first mark point if it exists (before second mark is made)
        if self.page.first_segment_point is not None:
            first_x = (self.page.first_segment_point / duration) * width
            # Draw a bright marker line for the first point
            cr.set_source_rgba(1.0, 0.5, 0.0, 0.9)  # Orange color
            cr.set_line_width(3)
            cr.move_to(first_x, 0)
            cr.line_to(first_x, height)
            cr.stroke()

        # Draw completed segments
        if self.page.trim_segments:
            for segment in self.page.trim_segments:
                start = segment["start"]
                end = segment["end"]

                start_x = (start / duration) * width
                end_x = (end / duration) * width
                segment_width = end_x - start_x

                cr.set_source_rgba(0.2, 0.6, 1.0, 0.4)
                cr.rectangle(start_x, 0, segment_width, height)
                cr.fill()

                cr.set_source_rgba(0.2, 0.6, 1.0, 0.8)
                cr.set_line_width(2)
                cr.move_to(start_x, 0)
                cr.line_to(start_x, height)
                cr.stroke()
                cr.move_to(end_x, 0)
                cr.line_to(end_x, height)
                cr.stroke()

    def update_segment_markers(self) -> None:
        """Request redraw of segment markers"""
        if hasattr(self, "segment_markers_canvas"):
            self.segment_markers_canvas.queue_draw()

    def _setup_drag_controllers(self):
        """Setup drag controllers on the interactive DrawingArea."""
        self._dragging_segment = None
        self._drag_threshold = 10
        self._segment_drag_active = False
        self._drag_start_pos = None

        self.drag_gesture = Gtk.GestureDrag.new()
        self.drag_gesture.set_touch_only(False)
        self.drag_gesture.set_button(1)
        self.drag_gesture.connect("drag-begin", self._on_drag_begin)
        self.drag_gesture.connect("drag-update", self._on_drag_update)
        self.drag_gesture.connect("drag-end", self._on_drag_end)
        self.segment_markers_canvas.add_controller(self.drag_gesture)

        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("motion", self._on_motion)
        motion_controller.connect("leave", self._on_motion_leave)
        self.segment_markers_canvas.add_controller(motion_controller)

    def _find_segment_edge_at_position(self, x, width):
        if width <= 0:
            return None
        if not self.page.trim_segments or not hasattr(self.page, "mpv_player"):
            return None
        duration = self.page.mpv_player.get_duration()
        if duration <= 0:
            return None
        for idx, segment in enumerate(self.page.trim_segments):
            start_x = (segment["start"] / duration) * width
            end_x = (segment["end"] / duration) * width
            if abs(x - start_x) <= self._drag_threshold:
                return {"segment_index": idx, "edge": "start"}
            if abs(x - end_x) <= self._drag_threshold:
                return {"segment_index": idx, "edge": "end"}
        return None

    def _on_drag_begin(self, gesture, start_x, start_y):
        self.page.user_is_dragging_slider = True
        self._drag_start_pos = (start_x, start_y)
        width = self.segment_markers_canvas.get_allocated_width()
        if width <= 0:
            self.page.user_is_dragging_slider = False
            self._drag_start_pos = None
            return
        edge_info = self._find_segment_edge_at_position(start_x, width)
        if edge_info:
            self._dragging_segment = edge_info
            self._segment_drag_active = True
        else:
            self._segment_drag_active = False
            self._update_slider_drag(start_x, width)

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if not self._drag_start_pos:
            return
        start_x, _ = self._drag_start_pos
        current_x = start_x + offset_x
        width = self.segment_markers_canvas.get_allocated_width()
        if width <= 0:
            return
        current_x = max(0, min(width, current_x))
        if self._segment_drag_active:
            self._update_segment_drag(current_x, width)
        else:
            self._update_slider_drag(current_x, width)

    def _on_drag_end(self, gesture, offset_x, offset_y):
        if self._segment_drag_active:
            self.page._save_file_metadata()
        self.page.user_is_dragging_slider = False
        self._dragging_segment = None
        self._segment_drag_active = False
        self._drag_start_pos = None

    def _update_slider_drag(self, x, width):
        if width <= 0 or not hasattr(self.page, "mpv_player"):
            return
        duration = self.page.mpv_player.get_duration()
        if duration <= 0:
            return
        new_time = (x / width) * duration
        new_time = max(0, min(duration, new_time))
        self.position_scale.set_value(new_time)

    def _on_motion(self, controller, x, y):
        if not self.page.user_is_dragging_slider:
            width = self.segment_markers_canvas.get_allocated_width()
            edge_info = self._find_segment_edge_at_position(x, width)
            self.segment_markers_canvas.set_cursor_from_name(
                "ew-resize" if edge_info else None
            )

    def _on_motion_leave(self, controller):
        self.segment_markers_canvas.set_cursor(None)

    def _update_segment_drag(self, x, width):
        if width <= 0:
            return
        if not self._dragging_segment or not hasattr(self.page, "mpv_player"):
            return
        duration = self.page.mpv_player.get_duration()
        if duration <= 0:
            return
        new_time = (x / width) * duration
        new_time = max(0, min(duration, new_time))
        idx, edge = (
            self._dragging_segment["segment_index"],
            self._dragging_segment["edge"],
        )
        if edge == "start" and new_time < self.page.trim_segments[idx]["end"]:
            self.page.trim_segments[idx]["start"] = new_time
            self.position_scale.set_value(new_time)
        elif edge == "end" and new_time > self.page.trim_segments[idx]["start"]:
            self.page.trim_segments[idx]["end"] = new_time
            self.position_scale.set_value(new_time)
        # The page edits its own copy of the segments; store the change.
        self.page._save_file_metadata()
        self.update_segment_markers()
        self.page._update_segments_listbox()

    def _on_video_mouse_enter(self, c, x, y):
        self._show_controls()

    def _on_video_mouse_motion(self, c, x, y):
        self._show_controls()
        if self.page.is_playing:
            self._schedule_hide_controls()

    def _on_video_mouse_leave(self, c):
        if not self._any_popover_visible() and self.page.is_playing:
            self._schedule_hide_controls(delay=500)

    def _any_popover_visible(self):
        """Checks if any of the main popover-controlling buttons are active."""
        return (
            (hasattr(self, "volume_button") and self.volume_button.get_active())
            or (
                hasattr(self, "audio_track_button")
                and self.audio_track_button.get_active()
            )
            or (hasattr(self, "subtitle_button") and self.subtitle_button.get_active())
            or (
                hasattr(self, "speed_button")
                and self.speed_button.get_popover()
                and self.speed_button.get_popover().is_visible()
            )
        )

    def _show_controls(self):
        if getattr(self.page, "crop_edit_mode", False):
            return
        if hasattr(self, "overlay_controls"):
            self.overlay_controls.set_visible(True)
            if self.hide_timer_id is not None:
                GLib.source_remove(self.hide_timer_id)
                self.hide_timer_id = None

    def _schedule_hide_controls(self, delay=2000):
        if self._any_popover_visible():
            return
        if self.hide_timer_id is not None:
            GLib.source_remove(self.hide_timer_id)
        self.hide_timer_id = GLib.timeout_add(delay, self._hide_controls)

    def _hide_controls(self):
        if self._any_popover_visible():
            return True
        if hasattr(self, "overlay_controls"):
            self.overlay_controls.set_visible(False)
        self.hide_timer_id = None
        return False

    def cancel_scheduled_sources(self) -> None:
        """Remove UI-owned callbacks before the editor is hidden or destroyed."""
        if self.hide_timer_id is not None:
            GLib.source_remove(self.hide_timer_id)
            self.hide_timer_id = None
        if hasattr(self, "overlay_controls"):
            self.overlay_controls.set_visible(True)

    def apply_tooltips(self) -> None:
        """Apply tooltips to all video edit UI elements"""
        if not hasattr(self.page.app, "tooltip_helper"):
            return

        tooltip_helper = self.page.app.tooltip_helper

        # Apply tooltips to stored widget references
        if hasattr(self, "crop_grid") and self.crop_grid:
            tooltip_helper.add_tooltip(self.crop_grid, "crop")
        if hasattr(self, "brightness_row") and self.brightness_row:
            tooltip_helper.add_tooltip(self.brightness_row, "brightness")
        if hasattr(self, "saturation_row") and self.saturation_row:
            tooltip_helper.add_tooltip(self.saturation_row, "saturation")
        if hasattr(self, "hue_row") and self.hue_row:
            tooltip_helper.add_tooltip(self.hue_row, "hue")
        if hasattr(self, "trim_group") and self.trim_group:
            tooltip_helper.add_tooltip(self.trim_group, "segments")
        for widget, key in ((getattr(self, "denoise_combo", None), "denoise"),
                            (getattr(self, "sharpen_combo", None), "sharpen"),
                            (getattr(self, "stabilize_row", None), "stabilize"),
                            (getattr(self, "effect_file_row", None), "effect_file"),
                            (getattr(self, "source_hdr_combo", None), "source_hdr")):
            if widget is not None:
                tooltip_helper.add_tooltip(widget, key)

    def disconnect_all_handlers(self) -> None:
        """Disconnect handlers and remove callbacks owned by the editor UI."""
        self.cancel_scheduled_sources()
        # Handler ids come from connect(); disconnect() raises only for a
        # non-integer id and reports a stale one as a GLib warning.
        for widget, hid in self._handler_ids:
            widget.disconnect(hid)
        self._handler_ids.clear()
        # Reset the handler ID so stale references are not used after cleanup
        self.page.position_changed_handler_id = None

    def reconnect_handlers(self) -> None:
        """Reconnect signal handlers after cleanup."""
        if self.page.position_changed_handler_id is None:
            self.page.position_changed_handler_id = self.position_scale.connect(
                "value-changed", self.page.on_position_changed
            )
            self._handler_ids.append((
                self.position_scale,
                self.page.position_changed_handler_id,
            ))
