import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Setup translation
import gettext

from gi.repository import Adw, Gio, GLib, Gtk, Pango

_ = gettext.gettext

# Import MPVPlayer to check rendering mode
from ui.mpv_player import MPVPlayer
from ui.crop_overlay import CropOverlay


class VideoEditUI:
    def __init__(self, page):
        self.page = page
        self._handler_ids: list[tuple] = []  # [(widget, handler_id), ...]

    def create_page(self):
        """Create the main page layout and all UI elements"""
        # Main container with a vertical layout. Top (video) expands, bottom (toolbar) is fixed.
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_vexpand(True)

        # TOP: Video preview area
        self.video_overlay = Gtk.Overlay()
        self.video_overlay.set_vexpand(True)
        self.video_overlay.set_hexpand(True)

        video_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        video_box.set_vexpand(True)
        video_box.set_hexpand(True)
        video_box.set_size_request(-1, 300)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b".video-background { background-color: #000; }")
        display = (
            self.page.app.get_display()
            if hasattr(self.page.app, "get_display")
            else None
        )
        if display is None:
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
            )
        video_box.add_css_class("video-background")

        # Video player widget - choose based on rendering mode
        # X11 mode: Use DrawingArea for X11 window embedding (better VM compatibility)
        # OpenGL mode: Use GLArea for MPV OpenGL render API
        if MPVPlayer.use_x11_mode:
            # X11 mode: DrawingArea allows MPV to embed its window directly
            self.preview_video = Gtk.DrawingArea()
            self.preview_video.set_hexpand(True)
            self.preview_video.set_vexpand(True)
        else:
            # OpenGL mode: GLArea provides OpenGL context for MPV render API
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
        controls_container.add_css_class("osd")
        controls_container.add_css_class("toolbar")

        slider_overlay = Gtk.Overlay()

        adjustment = Gtk.Adjustment(value=0, lower=0, upper=100, step_increment=1)
        self.position_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment
        )
        self.position_scale.set_draw_value(False)
        self.position_scale.set_hexpand(True)
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

        self.speed_button = Gtk.MenuButton(
            label="1x",
            popover=speed_popover,
            tooltip_text=_("Playback Speed"),
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
        button_row.append(self.subtitle_button)

        # Spacer to push fullscreen to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        button_row.append(spacer)

        # Fullscreen button
        self.fullscreen_button = Gtk.Button()
        self.fullscreen_button.set_icon_name('view-fullscreen-symbolic')
        self.fullscreen_button.set_tooltip_text(_("Toggle Fullscreen"))
        self.fullscreen_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Toggle Fullscreen")],
        )
        self.fullscreen_button.connect("clicked", self.page.on_toggle_fullscreen)
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
        self.controls_visible = True
        self.hide_timer_id = None
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", self._on_video_mouse_enter)
        motion.connect("motion", self._on_video_mouse_motion)
        motion.connect("leave", self._on_video_mouse_leave)
        overlay.add_controller(motion)

    def _create_compact_toolbar(self):
        """Creates the unified, compact toolbar below the video."""
        toolbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toolbar_box.set_margin_start(12)
        toolbar_box.set_margin_end(12)
        toolbar_box.set_margin_top(6)
        toolbar_box.set_margin_bottom(6)
        toolbar_box.add_css_class("toolbar")

        # --- Crop Controls ---
        crop_grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        self.crop_grid = crop_grid  # Store reference for enable/disable
        crop_grid.set_valign(Gtk.Align.CENTER)

        crop_labels = [_("Left"), _("Right"), _("Top"), _("Bottom")]
        crop_keys = ["left", "right", "top", "bottom"]
        self.crop_spins = {}
        for i, (label_text, key) in enumerate(zip(crop_labels, crop_keys)):
            label = Gtk.Label(label=label_text, xalign=0)
            adjustment = Gtk.Adjustment(value=0, lower=0, upper=9999, step_increment=1)
            spin = Gtk.SpinButton(adjustment=adjustment, numeric=True, width_chars=5)
            self.crop_spins[key] = spin
            spin.connect("value-changed", self.page.on_crop_value_changed)

            col, row = (i % 2, i // 2)
            crop_grid.attach(label, col * 2, row, 1, 1)
            crop_grid.attach(spin, col * 2 + 1, row, 1, 1)

        setattr(self, "crop_left_spin", self.crop_spins["left"])
        setattr(self, "crop_right_spin", self.crop_spins["right"])
        setattr(self, "crop_top_spin", self.crop_spins["top"])
        setattr(self, "crop_bottom_spin", self.crop_spins["bottom"])

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
        crop_box.append(self.crop_edit_btn)

        # Add tooltip to crop grid
        if hasattr(self.page.app, "tooltip_helper"):
            self.page.app.tooltip_helper.add_tooltip(crop_grid, "crop")

        toolbar_box.append(crop_box)
        toolbar_box.append(
            Gtk.Separator(
                orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6
            )
        )

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

        # --- Video Adjustments Group ---
        adjust_group = Adw.PreferencesGroup()
        self.adjust_group = adjust_group  # Store reference for enable/disable

        self.brightness_scale, brightness_row = self._create_adjustment_row(
            adjust_group,
            _("Brightness"),
            -1.0,
            1.0,
            0.0,
            self.page.on_brightness_changed,
            self.page.reset_brightness,
        )
        self.brightness_row = (
            brightness_row  # Store reference for tooltip reapplication
        )
        # Add tooltip to brightness row
        if brightness_row and hasattr(self.page.app, "tooltip_helper"):
            self.page.app.tooltip_helper.add_tooltip(brightness_row, "brightness")

        self.saturation_scale, saturation_row = self._create_adjustment_row(
            adjust_group,
            _("Saturation"),
            0.0,
            2.0,
            1.0,
            self.page.on_saturation_changed,
            self.page.reset_saturation,
        )
        self.saturation_row = (
            saturation_row  # Store reference for tooltip reapplication
        )
        # Add tooltip to saturation row
        if saturation_row and hasattr(self.page.app, "tooltip_helper"):
            self.page.app.tooltip_helper.add_tooltip(saturation_row, "saturation")

        self.hue_scale, hue_row = self._create_adjustment_row(
            adjust_group,
            _("Hue"),
            -1.0,
            1.0,
            0.0,
            self.page.on_hue_changed,
            self.page.reset_hue,
        )
        self.hue_row = hue_row  # Store reference for tooltip reapplication
        # Add tooltip to hue row
        if hue_row and hasattr(self.page.app, "tooltip_helper"):
            self.page.app.tooltip_helper.add_tooltip(hue_row, "hue")

        sidebar_box.append(adjust_group)

        # --- Rotation / Flip Group ---
        transform_group = Adw.PreferencesGroup()
        self.transform_group = transform_group

        transform_row = Adw.ActionRow(title=_("Transform"))
        transform_buttons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)

        rotate_ccw_btn = Gtk.Button(
            icon_name="object-rotate-left-symbolic", tooltip_text=_("Rotate 90° Left")
        )
        rotate_ccw_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Rotate 90° Left")],
        )
        rotate_ccw_btn.add_css_class("flat")
        rotate_ccw_btn.connect("clicked", lambda b: self.page.on_rotate(-90))
        transform_buttons.append(rotate_ccw_btn)

        rotate_cw_btn = Gtk.Button(
            icon_name="object-rotate-right-symbolic", tooltip_text=_("Rotate 90° Right")
        )
        rotate_cw_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Rotate 90° Right")],
        )
        rotate_cw_btn.add_css_class("flat")
        rotate_cw_btn.connect("clicked", lambda b: self.page.on_rotate(90))
        transform_buttons.append(rotate_cw_btn)

        flip_h_btn = Gtk.Button(
            icon_name="object-flip-horizontal-symbolic",
            tooltip_text=_("Flip Horizontal"),
        )
        flip_h_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Flip Horizontal")],
        )
        flip_h_btn.add_css_class("flat")
        flip_h_btn.connect("clicked", lambda b: self.page.on_flip("horizontal"))
        self.flip_h_btn = flip_h_btn
        transform_buttons.append(flip_h_btn)

        flip_v_btn = Gtk.Button(
            icon_name="object-flip-vertical-symbolic", tooltip_text=_("Flip Vertical")
        )
        flip_v_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Flip Vertical")],
        )
        flip_v_btn.add_css_class("flat")
        flip_v_btn.connect("clicked", lambda b: self.page.on_flip("vertical"))
        self.flip_v_btn = flip_v_btn
        transform_buttons.append(flip_v_btn)

        reset_transform_btn = Gtk.Button(
            icon_name="edit-undo-symbolic", tooltip_text=_("Reset Transform")
        )
        reset_transform_btn.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Reset Transform")],
        )
        reset_transform_btn.add_css_class("flat")
        reset_transform_btn.connect("clicked", lambda b: self.page.on_reset_transform())
        transform_buttons.append(reset_transform_btn)

        transform_row.add_suffix(transform_buttons)
        transform_group.add(transform_row)
        sidebar_box.append(transform_group)

        # --- Audio Cleaning Group (visible only when re-encode active) ---
        self.nr_sidebar_group = self._create_nr_sidebar_group()
        self.nr_sidebar_group.set_visible(False)
        sidebar_box.append(self.nr_sidebar_group)

        # --- Trim Segments Group ---
        trim_group = Adw.PreferencesGroup(title=_("Trim Segments"))
        self.trim_group = trim_group  # Store reference for tooltip reapplication

        list_row = Adw.ActionRow(title=_("Segments List"))
        list_row.set_visible(False)

        self.segments_scrolled = Gtk.ScrolledWindow(
            min_content_height=120, vexpand=True
        )
        self.segments_scrolled.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self.segments_scrolled.set_max_content_height(300)
        self.segments_scrolled.set_propagate_natural_height(True)

        self.segments_listbox = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"]
        )

        self.segments_scrolled.set_child(self.segments_listbox)
        list_row.set_child(self.segments_scrolled)
        self.segments_list_row = list_row
        trim_group.add(list_row)

        # Placeholder shown when no segments
        self.segments_placeholder_row = Adw.ActionRow()
        placeholder_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=12,
            margin_bottom=12,
        )
        placeholder_box.set_halign(Gtk.Align.CENTER)
        placeholder_icon = Gtk.Image(
            icon_name="bookmark-new-symbolic", pixel_size=32, css_classes=["dim-label"]
        )
        placeholder_label = Gtk.Label(
            label=_("Press M or use the mark button to add segments"),
            css_classes=["dim-label"],
        )
        placeholder_box.append(placeholder_icon)
        placeholder_box.append(placeholder_label)
        self.segments_placeholder_row.set_child(placeholder_box)
        trim_group.add(self.segments_placeholder_row)

        # Action buttons row with + and trash icons
        actions_row = Adw.ActionRow()
        button_box = Gtk.Box(
            spacing=30, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER
        )
        button_box.set_hexpand(True)

        # Add segment button
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.add_css_class("circular")
        add_button.add_css_class("suggested-action")
        add_button.set_tooltip_text(_("Add segment manually"))
        add_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Add segment manually")],
        )
        add_button.connect("clicked", self.page._on_add_manual_segment_clicked)
        button_box.append(add_button)

        # Clear all segments button
        clear_button = Gtk.Button(icon_name="user-trash-symbolic")
        clear_button.add_css_class("circular")
        clear_button.add_css_class("destructive-action")
        clear_button.set_tooltip_text(_("Clear all segments"))
        clear_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Clear all segments")],
        )
        clear_button.connect(
            "clicked", self.page._on_clear_all_segments_with_confirmation
        )
        button_box.append(clear_button)

        actions_row.set_child(button_box)
        trim_group.add(actions_row)

        # Add tooltip to trim group
        if hasattr(self.page.app, "tooltip_helper"):
            self.page.app.tooltip_helper.add_tooltip(trim_group, "segments")

        # Output Mode dropdown (no title, just dropdown)
        output_mode_model = Gtk.StringList()
        output_mode_model.append(_("Join segments into a single file"))
        output_mode_model.append(_("Save each segment as a separate file"))

        # Create a simple row to contain the dropdown
        output_row = Adw.ActionRow()
        self.output_mode_combo = Gtk.DropDown()
        self.output_mode_combo.set_model(output_mode_model)
        self.output_mode_combo.set_hexpand(True)
        self.output_mode_combo.set_valign(Gtk.Align.CENTER)
        self.output_mode_combo.connect(
            "notify::selected", self.page._on_output_mode_changed
        )
        output_row.add_suffix(self.output_mode_combo)
        output_row.set_activatable_widget(self.output_mode_combo)
        trim_group.add(output_row)

        sidebar_box.append(trim_group)

    def update_for_force_copy_state(self, force_copy_enabled) -> None:
        """Enable/disable editing controls based on force copy state"""
        # When force copy is enabled, color adjustments and crop don't work
        # Only trim segments continue to work
        enable_editing_options = not force_copy_enabled

        # Disable/enable color adjustments group
        if hasattr(self, "adjust_group"):
            self.adjust_group.set_sensitive(enable_editing_options)

        # Disable/enable crop controls
        if hasattr(self, "crop_grid"):
            self.crop_grid.set_sensitive(enable_editing_options)
        if hasattr(self, "crop_edit_btn"):
            self.crop_edit_btn.set_sensitive(enable_editing_options)
            if force_copy_enabled and self.crop_edit_btn.get_active():
                self.crop_edit_btn.set_active(False)

        # Disable/enable transform controls
        if hasattr(self, "transform_group"):
            self.transform_group.set_sensitive(enable_editing_options)

        # Trim segments always stay enabled - they work with stream copy

    def _create_adjustment_row(
        self, container, title, min_val, max_val, default_val, on_change, on_reset
    ):
        """Helper to create a single adjustment row for the sidebar."""
        row = Adw.ActionRow(title=title)

        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)

        scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            digits=2,
            value_pos=Gtk.PositionType.RIGHT,
            hexpand=True,
        )
        scale.set_adjustment(
            Gtk.Adjustment(
                value=default_val, lower=min_val, upper=max_val, step_increment=0.05
            )
        )
        scale.connect("value-changed", on_change)

        reset_button = Gtk.Button(
            icon_name="edit-undo-symbolic", tooltip_text=_("Reset to default")
        )
        reset_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Reset to default")],
        )
        reset_button.connect("clicked", lambda b: on_reset())

        box.append(scale)
        box.append(reset_button)

        row.add_suffix(box)
        container.add(row)
        return scale, row

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
        if not hasattr(self.page, "mpv_player"):
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
            self.controls_visible = True
            if self.hide_timer_id:
                GLib.source_remove(self.hide_timer_id)
                self.hide_timer_id = None

    def _schedule_hide_controls(self, delay=2000):
        if self._any_popover_visible():
            return
        if self.hide_timer_id:
            GLib.source_remove(self.hide_timer_id)
        self.hide_timer_id = GLib.timeout_add(delay, self._hide_controls)

    def _hide_controls(self):
        if self._any_popover_visible():
            return True
        if hasattr(self, "overlay_controls"):
            self.overlay_controls.set_visible(False)
        self.controls_visible = False
        self.hide_timer_id = None
        return False

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

    def disconnect_all_handlers(self) -> None:
        """Disconnect all tracked signal handlers."""
        for widget, hid in self._handler_ids:
            try:
                widget.disconnect(hid)
            except Exception:
                pass
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
