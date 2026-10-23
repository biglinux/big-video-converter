import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import gettext

from gi.repository import Gio, GLib, Graphene, Gtk, Pango

_ = gettext.gettext

from ui.crop_overlay import CropOverlay
from ui.video_edit_sidebar import VideoEditSidebarMixin


class VideoEditUI(VideoEditSidebarMixin):
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
        """Playback controls overlaid on the bottom of the video."""
        controls_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        controls_container.set_valign(Gtk.Align.END)
        controls_container.set_halign(Gtk.Align.FILL)
        controls_container.set_margin_start(12)
        controls_container.set_margin_end(12)
        controls_container.set_margin_bottom(12)
        # Not "toolbar": desktop themes recolor that class (BigStyle paints it
        # with the header bar colors), and this bar sits on video, so it keeps
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
        self._handler_ids.append(
            (
                self.position_scale,
                self.page.position_changed_handler_id,
            )
        )
        self.position_scale.set_can_target(False)

        slider_overlay.set_child(self.position_scale)

        # The slider seeks and nothing else: hundreds of cut edges under it
        # turned almost every click into a moved cut. Cuts have their own
        # track above it.
        self.seek_area = Gtk.DrawingArea(can_target=True)
        self.seek_area.set_tooltip_text(_("Video position"))
        slider_overlay.add_overlay(self.seek_area)

        self.cuts_track = Gtk.DrawingArea(
            content_height=10, hexpand=True, visible=False, margin_bottom=2
        )
        self.cuts_track.set_draw_func(self._draw_cuts_track)
        cuts_hint = _("Drag to seek or adjust segment edges")
        self.cuts_track.set_tooltip_text(cuts_hint)
        self.cuts_track.update_property([Gtk.AccessibleProperty.LABEL], [cuts_hint])
        self._setup_drag_controllers()

        controls_container.append(self.cuts_track)
        controls_container.append(slider_overlay)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        button_row.set_halign(Gtk.Align.CENTER)

        # Mark button
        mark_button = Gtk.Button(icon_name="bookmark-new-symbolic")
        mark_button.set_tooltip_text(_("Mark segment point"))
        mark_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Mark segment point")],
        )
        mark_button.connect("clicked", self.page.on_mark_segment_point)
        mark_button.add_css_class("bvc-icon-button")
        button_row.append(mark_button)

        # Feedback for marking
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

        # The preview of what a joined export keeps, like the audio
        # converter's "Selection only".
        self.segments_only_button = Gtk.ToggleButton(
            icon_name="selection-mode-symbolic", visible=False
        )
        self.segments_only_button.set_tooltip_text(_("Play only the segments"))
        self.segments_only_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Play only the segments")]
        )
        self.segments_only_button.add_css_class("flat")
        self.segments_only_button.add_css_class("bvc-icon-button")
        self.segments_only_button.connect("toggled", self.page.on_segments_only_toggled)
        button_row.append(self.segments_only_button)

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
        self.play_pause_button.set_icon_name("media-playback-start-symbolic")
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

        speed_summary = _("Playback speed: {speed}x").format(speed=self._current_speed)
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
        self.fullscreen_button.set_icon_name("view-fullscreen-symbolic")
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

        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", self._on_video_mouse_enter)
        motion.connect("motion", self._on_video_mouse_motion)
        motion.connect("leave", self._on_video_mouse_leave)
        overlay.add_controller(motion)

    def _create_compact_toolbar(self):
        """Creates the unified, compact toolbar below the video."""
        toolbar_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=12,
            row_spacing=8,
            min_children_per_line=1,
            max_children_per_line=2,
        )
        toolbar_box.set_margin_start(12)
        toolbar_box.set_margin_end(12)
        toolbar_box.set_margin_top(6)
        toolbar_box.set_margin_bottom(6)
        toolbar_box.add_css_class("bvc-editor-inspector")

        # Crop Controls
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

        aspect_model = Gtk.StringList.new(
            [
                _("Free"),
                _("Original"),
                "16:9",
                "9:16",
                "1:1",
                "4:5",
                "3:2",
                "2.39:1",
            ]
        )
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

        if hasattr(self.page.app, "help_tooltips"):
            self.page.app.help_tooltips.add_tooltip(crop_grid, "crop")

        toolbar_box.append(crop_box)
        # Info Display
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
        title_label = Gtk.Label(label=title, xalign=1, css_classes=["dim-label"])
        value_label = Gtk.Label(
            label="...", xalign=0, selectable=True, ellipsize=Pango.EllipsizeMode.END
        )
        grid.attach(title_label, 0, row_index, 1, 1)
        grid.attach(value_label, 1, row_index, 1, 1)
        return value_label

    def _on_speed_btn_clicked(self, button, speed_value):
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
        volume_button = Gtk.MenuButton(
            icon_name="audio-volume-high-symbolic", tooltip_text=_("Volume")
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

    def _draw_cuts_track(self, area, cr, width, height):
        """The marked segments and a pending mark, above the slider."""
        duration = self.page.video_duration
        if not self.page.current_video_path or duration <= 0:
            return
        left, width = self._slider_span(area)
        cr.translate(left, 0)
        scale = width / duration
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if self.page.output_mode == "remove":
            # Segments to cut out: red, so the gaps read as what is kept.
            cr.set_source_rgba(0.88, 0.11, 0.14, 1)
        else:
            cr.set_source_rgba(0.21, 0.52, 0.89, 1)
        for segment in self.page.trim_segments:
            start_x = segment["start"] * scale
            cr.rectangle(start_x, 0, max(1.5, segment["end"] * scale - start_x), height)
        cr.fill()

        if self.page.first_segment_point is not None:
            # The start of a segment being marked.
            cr.set_source_rgba(1.0, 0.6, 0.1, 1)
            cr.rectangle(self.page.first_segment_point * scale - 1.5, 0, 3, height)
            cr.fill()

    def update_segment_markers(self) -> None:
        """Show the cuts track while there is something on it."""
        page = self.page
        self.cuts_track.set_visible(
            bool(page.trim_segments) or page.first_segment_point is not None
        )
        self.cuts_track.queue_draw()

    def _setup_drag_controllers(self):
        """Seeking on the slider; seeking or moving cut edges on the cuts track."""
        self._dragging_segment = None
        self._edge_reach = 6  # pixels either side of an edge that pick it up

        seek = Gtk.GestureDrag(button=1)
        seek.connect(
            "drag-begin", lambda _g, x, _y: self._begin_seek(self.seek_area, x)
        )
        seek.connect(
            "drag-update",
            lambda g, dx, _dy: self._seek_to(
                self.seek_area, g.get_start_point()[1] + dx
            ),
        )
        seek.connect("drag-end", self._end_drag)
        self.seek_area.add_controller(seek)

        cuts = Gtk.GestureDrag(button=1)
        cuts.connect("drag-begin", self._on_cuts_drag_begin)
        cuts.connect("drag-update", self._on_cuts_drag_update)
        cuts.connect("drag-end", self._on_cuts_drag_end)
        self.cuts_track.add_controller(cuts)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_cuts_motion)
        self.cuts_track.add_controller(motion)

    def _slider_span(self, widget):
        """Where the slider travels, in widget's coordinates.

        The scale is narrower than the bar around it (theme padding), so
        clicks and cuts are placed along the slider, not the whole bar.
        """
        width = self.position_scale.get_width()
        found, origin = self.position_scale.compute_point(widget, Graphene.Point())
        if not found or width <= 0:
            return 0, widget.get_width()
        return origin.x, width

    def _time_at(self, widget, x):
        left, width = self._slider_span(widget)
        duration = self.page.video_duration
        if width <= 0 or duration <= 0:
            return None
        return max(0, min(duration, (x - left) / width * duration))

    def _edge_at(self, x):
        """The segment edge nearest to x, if one is within reach."""
        left, width = self._slider_span(self.cuts_track)
        duration = self.page.video_duration
        if width <= 0 or duration <= 0:
            return None
        nearest, distance = None, self._edge_reach + 1
        for index, segment in enumerate(self.page.trim_segments):
            for edge in ("start", "end"):
                gap = abs(x - left - segment[edge] / duration * width)
                if gap < distance:
                    nearest, distance = (index, edge), gap
        return nearest

    def _begin_seek(self, widget, x):
        self.page.user_is_dragging_slider = True
        self._seek_to(widget, x)

    def _seek_to(self, widget, x):
        time = self._time_at(widget, x)
        if time is not None:
            self.position_scale.set_value(time)

    def _end_drag(self, *_args):
        self.page.user_is_dragging_slider = False

    def _on_cuts_drag_begin(self, _gesture, x, _y):
        self._dragging_segment = self._edge_at(x)
        if self._dragging_segment is None:
            self._begin_seek(self.cuts_track, x)
        else:
            self.page.user_is_dragging_slider = True

    def _on_cuts_drag_update(self, gesture, offset_x, _offset_y):
        x = gesture.get_start_point()[1] + offset_x
        if self._dragging_segment is None:
            self._seek_to(self.cuts_track, x)
            return
        time = self._time_at(self.cuts_track, x)
        if time is None:
            return
        index, edge = self._dragging_segment
        segment = self.page.trim_segments[index]
        if (edge == "start" and time < segment["end"]) or (
            edge == "end" and time > segment["start"]
        ):
            segment[edge] = time
            self.position_scale.set_value(time)
            self.update_segment_markers()

    def _on_cuts_drag_end(self, *_args):
        if self._dragging_segment is not None:
            # An edge dragged past a neighbor changes the order. The page
            # edits its own copy of the segments; store the change once.
            self.page.trim_segments.sort(key=lambda s: s["start"])
            self.page._save_file_metadata()
            self.page._update_segments_listbox()
        self._dragging_segment = None
        self._end_drag()

    def _on_cuts_motion(self, _controller, x, _y):
        if not self.page.user_is_dragging_slider:
            self.cuts_track.set_cursor_from_name(
                "ew-resize" if self._edge_at(x) else None
            )

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
            self.volume_button.get_active()
            or self.audio_track_button.get_active()
            or self.subtitle_button.get_active()
            or self.speed_button.get_popover().is_visible()
        )

    def _show_controls(self):
        if getattr(self.page, "crop_edit_mode", False):
            return
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
        self.hide_timer_id = None
        # The next pointer motion over the video schedules the hide again.
        if not self._any_popover_visible():
            self.overlay_controls.set_visible(False)
        return False

    def cancel_scheduled_sources(self) -> None:
        """Remove UI-owned callbacks before the editor is hidden or destroyed."""
        if self.hide_timer_id is not None:
            GLib.source_remove(self.hide_timer_id)
            self.hide_timer_id = None
        self.overlay_controls.set_visible(True)

    def apply_tooltips(self) -> None:
        """Apply tooltips to all video edit UI elements"""
        if not hasattr(self.page.app, "help_tooltips"):
            return

        help_tooltips = self.page.app.help_tooltips

        # The sidebar's rows say theirs in their cards.
        help_tooltips.add_tooltip(self.crop_grid, "crop")
        if hasattr(self, "trim_group") and self.trim_group:
            help_tooltips.add_tooltip(self.trim_group, "segments")

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
            self._handler_ids.append(
                (
                    self.position_scale,
                    self.page.position_changed_handler_id,
                )
            )
