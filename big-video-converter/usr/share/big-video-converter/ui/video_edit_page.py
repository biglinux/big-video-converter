import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
# Setup translation
import gettext

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

_ = gettext.gettext

# Import the modules we've split off
from ui.mpv_player import MPVPlayer
from ui.video_edit_ui import VideoEditUI
from ui.video_processing import VideoProcessor

# Import from the unified video_settings module instead of separate modules
from utils.video_settings import (
    VideoAdjustmentManager,
)

import logging

logger = logging.getLogger(__name__)


class VideoEditPage:
    def __init__(self, app, app_state):
        self.app = app
        self.app_state = app_state
        self.settings = app.settings_manager
        self.current_video_path = None
        self.video_duration = 0
        self.current_position = 0
        self.trim_segments = []
        self.first_segment_point = None  # For the new single-button marking logic
        self.reset_crop_values()
        self.position_update_id = None
        self.position_changed_handler_id = None
        self.cleanup_called = False
        self.video_width = 0
        self.video_height = 0
        self.video_fps = 25
        self.adjustment_manager = VideoAdjustmentManager(self.settings, self)
        self.crop_left = 0
        self.crop_right = 0
        self.crop_top = 0
        self.crop_bottom = 0
        self.brightness = 0.0
        self.saturation = 1.0
        self.hue = 0.0
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self.crop_edit_mode = False
        # Load default output mode from settings (last used by user)
        self.output_mode = self.settings.get_value("multi-segment-output-mode", "join")
        self.processor = VideoProcessor(self)
        self.ui = VideoEditUI(self)
        self.page = self.ui.create_page()
        # RENAMED: self.gst_player is now self.mpv_player
        self.mpv_player = MPVPlayer(self.ui.preview_video)
        self.is_playing = False
        self.user_is_dragging_slider = False
        self._seek_cooldown = False
        self._seek_cooldown_timer_id = None
        self.loading_video = False
        self.requested_video_path = None
        self._populate_track_menus_attempts = 0

        # Simple fullscreen support - just hide UI elements
        self.is_video_fullscreen = False

        # Debounce timer for saving metadata to avoid file I/O on every slider change
        self.metadata_save_timeout = None

        # Keyboard shortcuts
        self._setup_keyboard_shortcuts()

    def _setup_keyboard_shortcuts(self):
        """Add keyboard shortcuts to the editor page."""
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.page.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts for the video editor."""
        if keyval == Gdk.KEY_space:
            self.on_play_pause_clicked(None)
            return True
        if keyval == Gdk.KEY_Left:
            self.seek_relative(-1)
            return True
        if keyval == Gdk.KEY_Right:
            self.seek_relative(1)
            return True
        if keyval == Gdk.KEY_comma:
            frame = -1 / self.video_fps if self.video_fps > 0 else -1 / 25
            self.seek_relative(frame)
            return True
        if keyval == Gdk.KEY_period:
            frame = 1 / self.video_fps if self.video_fps > 0 else 1 / 25
            self.seek_relative(frame)
            return True
        if keyval == Gdk.KEY_f or keyval == Gdk.KEY_F:
            self.on_toggle_fullscreen(None)
            return True
        if keyval == Gdk.KEY_m or keyval == Gdk.KEY_M:
            self.on_mark_segment_point(None)
            return True
        return False

    def __del__(self):
        try:
            self.cleanup()
        except AttributeError:
            pass

    def _load_file_metadata(self, file_path):
        if not hasattr(self.app, "conversion_page"):
            return
        metadata = self.app_state.file_metadata.get(file_path)
        if not metadata:
            # Load the default output mode from settings (last used by user)
            default_output_mode = self.settings.get_value(
                "multi-segment-output-mode", "join"
            )
            self.app_state.file_metadata[file_path] = {
                "trim_segments": [],
                "crop_left": 0,
                "crop_right": 0,
                "crop_top": 0,
                "crop_bottom": 0,
                "brightness": 0.0,
                "contrast": 0.0,
                "saturation": 1.0,
                "hue": 0.0,
                "rotation": 0,
                "flip_h": False,
                "flip_v": False,
                "output_mode": default_output_mode,
            }
            metadata = self.app_state.file_metadata[file_path]
        # Load the default output mode from settings for fallback
        default_output_mode = self.settings.get_value(
            "multi-segment-output-mode", "join"
        )
        self.trim_segments = metadata.get("trim_segments", [])
        self.crop_left = metadata.get("crop_left", 0)
        self.crop_right = metadata.get("crop_right", 0)
        self.crop_top = metadata.get("crop_top", 0)
        self.crop_bottom = metadata.get("crop_bottom", 0)
        self.brightness = metadata.get("brightness", 0.0)
        self.saturation = metadata.get("saturation", 1.0)
        self.hue = metadata.get("hue", 0.0)
        self.rotation = metadata.get("rotation", 0)
        self.flip_h = metadata.get("flip_h", False)
        self.flip_v = metadata.get("flip_v", False)
        self.output_mode = metadata.get("output_mode", default_output_mode)
        self._update_ui_from_metadata()
        self._update_segments_listbox()
        self.ui.update_segment_markers()

    def _update_ui_from_metadata(self):
        # Update UI sliders to reflect current video's values
        if hasattr(self.ui, "brightness_scale"):
            self.ui.brightness_scale.set_value(self.brightness)
        if hasattr(self.ui, "saturation_scale"):
            self.ui.saturation_scale.set_value(self.saturation)
        if hasattr(self.ui, "hue_scale"):
            self.ui.hue_scale.set_value(self.hue)

        # Update output mode combo
        if hasattr(self.ui, "output_mode_combo"):
            output_mode_index = {"join": 0, "split": 1}.get(self.output_mode, 0)
            self.ui.output_mode_combo.set_selected(output_mode_index)

        # Update crop spinbuttons
        self.update_crop_spinbuttons()

        # Apply values to MPV player
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)
            self.mpv_player.set_saturation(self.saturation)
            self.mpv_player.set_hue(self.hue)
            self.mpv_player.set_crop(
                self.crop_left, self.crop_right, self.crop_top, self.crop_bottom
            )
            self.mpv_player.set_rotation(self.rotation)
            self.mpv_player.set_video_flip(self.flip_h, self.flip_v)
            # MPV handles render updates internally

        # Update flip button state
        self._update_flip_button_state()

    def _save_file_metadata(self):
        """Save metadata immediately - use _save_file_metadata_debounced for slider changes"""
        if not self.current_video_path or not hasattr(self.app, "conversion_page"):
            return
        metadata = self.app_state.file_metadata.get(self.current_video_path, {})
        metadata.update({
            "trim_segments": self.trim_segments,
            "crop_left": self.crop_left,
            "crop_right": self.crop_right,
            "crop_top": self.crop_top,
            "crop_bottom": self.crop_bottom,
            "brightness": self.brightness,
            "saturation": self.saturation,
            "hue": self.hue,
            "rotation": self.rotation,
            "flip_h": self.flip_h,
            "flip_v": self.flip_v,
            "output_mode": self.output_mode,
        })
        self.app_state.file_metadata[self.current_video_path] = metadata

    def _save_file_metadata_debounced(self):
        """Debounced version of _save_file_metadata to avoid excessive file I/O during slider drag"""
        # Cancel any pending save
        if self.metadata_save_timeout:
            GLib.source_remove(self.metadata_save_timeout)

        # Schedule save with 500ms delay - will only execute after user stops dragging
        def do_save() -> bool:
            self.metadata_save_timeout = None
            self._save_file_metadata()
            return False

        self.metadata_save_timeout = GLib.timeout_add(500, do_save)

    def set_video(self, file_path: str):
        if self.loading_video:
            return False
        if not file_path or not os.path.exists(file_path):
            return False
        if self.current_video_path == file_path:
            self._load_file_metadata(file_path)
            self.update_nr_button_visibility()
            return True
        self.loading_video = True
        self.requested_video_path = file_path
        self.current_video_path = file_path
        # Reset cleanup flag when loading a new video
        self.cleanup_called = False
        # Reconnect signal handlers if they were disconnected during cleanup
        self.ui.reconnect_handlers()
        # Refresh NR button visibility based on current sidebar state
        self.update_nr_button_visibility()
        self.ui.info_dimensions_label.set_text("...")
        self.ui.info_codec_label.set_text("...")
        self.ui.info_filesize_label.set_text("...")
        self.ui.info_duration_label.set_text("...")
        self.ui.position_scale.set_value(0)
        self.update_position_display(0)
        return self.processor.load_video(file_path)

    def get_page(self):
        return self.page

    def cleanup(self) -> None:
        """Clean up resources when leaving the edit page"""
        if getattr(self, "cleanup_called", False):
            return
        self.cleanup_called = True
        logger.debug("VideoEditPage: Starting cleanup")

        # Exit crop edit mode if active
        if self.crop_edit_mode:
            self.crop_edit_mode = False
            self.ui.crop_overlay.set_visible(False)
            self.ui.crop_edit_btn.set_active(False)
            self.ui.video_overlay.set_margin_start(0)
            self.ui.video_overlay.set_margin_end(0)
            self.ui.video_overlay.set_margin_top(0)
            self.ui.video_overlay.set_margin_bottom(0)

        # Stop position updates
        if hasattr(self, "position_update_id") and self.position_update_id:
            logger.debug("VideoEditPage: Removing position update timer")
            GLib.source_remove(self.position_update_id)
            self.position_update_id = None

        # Update UI immediately before stopping playback
        self.is_playing = False
        if hasattr(self, "ui") and self.ui:
            self.ui.play_pause_button.set_icon_name("media-playback-start-symbolic")

        # Explicitly cleanup MPV player (non-blocking)
        if hasattr(self, "mpv_player") and self.mpv_player:
            logger.debug("VideoEditPage: Cleaning up MPV player")
            # Just call cleanup once - it handles everything
            self.mpv_player.cleanup()

        # Disconnect tracked signal handlers
        if hasattr(self, "ui") and self.ui:
            self.ui.disconnect_all_handlers()

        # Clear video path
        if hasattr(self, "current_video_path"):
            self.current_video_path = None

        logger.debug("VideoEditPage: Cleanup complete")

    def on_brightness_changed(self, scale) -> None:
        self.brightness = scale.get_value()
        self._save_file_metadata_debounced()  # Use debounced save to avoid file I/O during drag
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)
            # Don't refresh preview during drag - MPV updates automatically
            # if not self.is_playing:
            #     self._refresh_preview()

    def on_crop_value_changed(self, spinbutton) -> None:
        """Handle crop value changes - ensures preview updates immediately"""
        if getattr(self, "_updating_crop_spins", False):
            return
        if not hasattr(self, "mpv_player") or not hasattr(self.ui, "crop_left_spin"):
            return

        left = self.ui.crop_left_spin.get_value()
        right = self.ui.crop_right_spin.get_value()
        top = self.ui.crop_top_spin.get_value()
        bottom = self.ui.crop_bottom_spin.get_value()

        # Update instance variables
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom

        # Save metadata so values persist
        self._save_file_metadata()

        # Update crop overlay if visible
        if self.crop_edit_mode:
            self.ui.crop_overlay.set_crop_values(
                int(left), int(right), int(top), int(bottom)
            )
        else:
            # Apply crop to MPV only when not in edit mode
            self.mpv_player.set_crop(left, right, top, bottom)

    def on_crop_edit_toggled(self, active: bool) -> None:
        """Toggle visual crop editor mode."""
        self.crop_edit_mode = active
        self.ui.crop_overlay.set_visible(active)

        _CROP_MARGIN = 20

        if active:
            # Hide overlay controls (seekbar, transport buttons) during crop edit
            if hasattr(self.ui, "overlay_controls"):
                self.ui.overlay_controls.set_visible(False)

            # Add margin around the video area to prevent accidental window resize
            self.ui.video_overlay.set_margin_start(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_end(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_top(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_bottom(_CROP_MARGIN)

            # Enter crop edit mode: remove crop from MPV, show overlay
            self.mpv_player.clear_crop()

            # Set video dimensions on overlay
            dims = self.mpv_player.get_video_dimensions()
            if dims:
                self.ui.crop_overlay.set_video_dimensions(dims[0], dims[1])

            # Set current crop values on overlay
            self.ui.crop_overlay.set_crop_values(
                int(self.crop_left),
                int(self.crop_right),
                int(self.crop_top),
                int(self.crop_bottom),
            )

            # Connect overlay drag callback to update spinbuttons
            self.ui.crop_overlay.set_on_crop_changed(self._on_crop_overlay_changed)
        else:
            # Remove margins
            self.ui.video_overlay.set_margin_start(0)
            self.ui.video_overlay.set_margin_end(0)
            self.ui.video_overlay.set_margin_top(0)
            self.ui.video_overlay.set_margin_bottom(0)

            # Restore overlay controls
            if hasattr(self.ui, "overlay_controls"):
                self.ui.overlay_controls.set_visible(True)

            # Exit crop edit mode: re-apply crop to MPV
            self.ui.crop_overlay.set_on_crop_changed(None)
            self.mpv_player.set_crop(
                self.crop_left,
                self.crop_right,
                self.crop_top,
                self.crop_bottom,
            )

    def _on_crop_overlay_changed(
        self, left: int, right: int, top: int, bottom: int
    ) -> None:
        """Called when user drags crop boundaries on the overlay."""
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom
        self._save_file_metadata()

        # Update spinbuttons without triggering their changed signal back
        self._updating_crop_spins = True
        self.ui.crop_left_spin.set_value(left)
        self.ui.crop_right_spin.set_value(right)
        self.ui.crop_top_spin.set_value(top)
        self.ui.crop_bottom_spin.set_value(bottom)
        self._updating_crop_spins = False

    def on_rotate(self, degrees: int) -> None:
        """Rotate video preview by given degrees (cumulative)."""
        self.rotation = (getattr(self, "rotation", 0) + degrees) % 360
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_rotation(self.rotation)

    def on_flip(self, direction: str) -> None:
        """Toggle horizontal or vertical flip."""
        if direction == "horizontal":
            self.flip_h = not getattr(self, "flip_h", False)
        else:
            self.flip_v = not getattr(self, "flip_v", False)
        self._save_file_metadata()
        self._update_flip_button_state()
        self._apply_video_flip()

    def on_reset_transform(self) -> None:
        """Reset rotation and flip to default."""
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_rotation(0)
            self.mpv_player.set_video_flip(False, False)
        self._update_flip_button_state()

    def _update_flip_button_state(self) -> None:
        """Update flip button appearance to show active state."""
        if hasattr(self.ui, "flip_h_btn"):
            if getattr(self, "flip_h", False):
                self.ui.flip_h_btn.add_css_class("accent")
            else:
                self.ui.flip_h_btn.remove_css_class("accent")
        if hasattr(self.ui, "flip_v_btn"):
            if getattr(self, "flip_v", False):
                self.ui.flip_v_btn.add_css_class("accent")
            else:
                self.ui.flip_v_btn.remove_css_class("accent")

    def _apply_video_flip(self) -> None:
        """Apply flip state to MPV preview."""
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_video_flip(
                getattr(self, "flip_h", False), getattr(self, "flip_v", False)
            )

    def format_time_precise(self, seconds):
        if seconds is None:
            seconds = 0
        hours = int(seconds) // 3600
        minutes = (int(seconds) % 3600) // 60
        seconds_remainder = int(seconds) % 60
        milliseconds = int((seconds - int(seconds)) * 1000)
        return f"{hours}:{minutes:02d}:{seconds_remainder:02d}.{milliseconds:03d}"

    def on_mark_segment_point(self, button) -> None:
        if self.first_segment_point is None:
            # First click: store point and show feedback
            self.first_segment_point = self.current_position
            self.ui.mark_time_label.set_text(
                self.format_time_precise(self.first_segment_point)
            )
            self.ui.mark_time_label.set_visible(True)
            self.ui.mark_cancel_button.set_visible(True)
            self.ui.update_segment_markers()  # Show first mark immediately
        else:
            # Second click: create segment
            second_point = self.current_position
            start_time = min(self.first_segment_point, second_point)
            end_time = max(self.first_segment_point, second_point)

            if end_time > start_time:
                new_segment = {"start": start_time, "end": end_time}
                self.trim_segments.append(new_segment)
                self.trim_segments.sort(key=lambda s: s["start"])
                self._save_file_metadata()
                self._update_segments_listbox()
                self.ui.update_segment_markers()

            # Reset for next marking
            self.on_mark_cancel(None)

    def on_mark_cancel(self, button) -> None:
        self.first_segment_point = None
        self.ui.mark_time_label.set_visible(False)
        self.ui.mark_cancel_button.set_visible(False)
        self.ui.update_segment_markers()  # Hide first mark line

    def _update_segments_listbox(self):
        if not hasattr(self.ui, "segments_listbox"):
            return
        while row := self.ui.segments_listbox.get_first_child():
            self.ui.segments_listbox.remove(row)
        for i, segment in enumerate(self.trim_segments):
            row = Adw.ActionRow()
            start_str = self.format_time_precise(segment["start"])
            end_str = self.format_time_precise(segment["end"])
            duration = segment["end"] - segment["start"]
            duration_str = self.format_time_precise(duration)
            row.set_title(
                _("Segment {num}: {start} → {end}").format(
                    num=i + 1, start=start_str, end=end_str
                )
            )
            row.set_subtitle(_("Duration: {duration}").format(duration=duration_str))
            button_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            goto_button = Gtk.Button(
                icon_name="media-playback-start-symbolic",
                css_classes=["flat"],
                tooltip_text=_("Go to segment start"),
            )
            goto_button.connect(
                "clicked", self._on_goto_segment_clicked, segment["start"]
            )
            button_box.append(goto_button)
            edit_button = Gtk.Button(
                icon_name="document-edit-symbolic",
                css_classes=["flat"],
                tooltip_text=_("Edit segment times"),
            )
            edit_button.connect("clicked", self._on_edit_segment_clicked, i)
            button_box.append(edit_button)
            remove_button = Gtk.Button(
                icon_name="edit-delete-symbolic",
                css_classes=["flat"],
                tooltip_text=_("Remove segment"),
            )
            remove_button.connect(
                "clicked", self._on_remove_segment_clicked, segment["start"]
            )
            button_box.append(remove_button)
            row.add_suffix(button_box)
            self.ui.segments_listbox.append(row)
        # Toggle visibility: show list when segments exist, placeholder when empty
        has_segments = len(self.trim_segments) > 0
        if hasattr(self.ui, "segments_list_row"):
            self.ui.segments_list_row.set_visible(has_segments)
        if hasattr(self.ui, "segments_placeholder_row"):
            self.ui.segments_placeholder_row.set_visible(not has_segments)
        self.ui.update_segment_markers()

    def _on_goto_segment_clicked(self, button, start_time):
        self.ui.position_scale.set_value(start_time)

    def _create_time_input_fields(self, time_seconds=0):
        """Create separate input fields for hours, minutes, seconds, centiseconds"""
        # Parse time into components
        hours = int(time_seconds // 3600)
        remaining = time_seconds % 3600
        minutes = int(remaining // 60)
        seconds = int(remaining % 60)
        centiseconds = int((time_seconds - int(time_seconds)) * 100)

        # Create container
        fields_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Hours
        fields_box.append(Gtk.Label(label="h"))
        h_spin = Gtk.SpinButton()
        h_spin.set_range(0, 99)
        h_spin.set_increments(1, 1)
        h_spin.set_value(hours)
        h_spin.set_width_chars(3)
        fields_box.append(h_spin)

        # Minutes
        fields_box.append(Gtk.Label(label="m"))
        m_spin = Gtk.SpinButton()
        m_spin.set_range(0, 59)
        m_spin.set_increments(1, 1)
        m_spin.set_value(minutes)
        m_spin.set_width_chars(3)
        fields_box.append(m_spin)

        # Seconds
        fields_box.append(Gtk.Label(label="s"))
        s_spin = Gtk.SpinButton()
        s_spin.set_range(0, 59)
        s_spin.set_increments(1, 1)
        s_spin.set_value(seconds)
        s_spin.set_width_chars(3)
        fields_box.append(s_spin)

        # Centiseconds (hundredths)
        fields_box.append(Gtk.Label(label="cs"))
        cs_spin = Gtk.SpinButton()
        cs_spin.set_range(0, 99)
        cs_spin.set_increments(1, 10)
        cs_spin.set_value(centiseconds)
        cs_spin.set_width_chars(3)
        fields_box.append(cs_spin)

        return fields_box, (h_spin, m_spin, s_spin, cs_spin)

    def _get_time_from_spinbuttons(self, spinbuttons):
        """Convert spinbutton values to total seconds"""
        h_spin, m_spin, s_spin, cs_spin = spinbuttons
        hours = h_spin.get_value()
        minutes = m_spin.get_value()
        seconds = s_spin.get_value()
        centiseconds = cs_spin.get_value()

        total_seconds = hours * 3600 + minutes * 60 + seconds + (centiseconds / 100.0)
        return total_seconds

    def _on_edit_segment_clicked(self, button, segment_index):
        """Show dialog to edit segment start and end times"""
        if segment_index >= len(self.trim_segments):
            return

        segment = self.trim_segments[segment_index]

        dialog = Adw.MessageDialog.new(
            self.app.window,
            _("Edit Segment"),
        )
        dialog.set_body(_("Edit start and end times for this segment"))

        # Create content with separate time input fields
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        # Start time fields
        start_label = Gtk.Label(label=_("Start Time:"), xalign=0)
        content_box.append(start_label)
        start_fields_box, start_spinbuttons = self._create_time_input_fields(
            segment["start"]
        )
        content_box.append(start_fields_box)

        # End time fields
        end_label = Gtk.Label(label=_("End Time:"), xalign=0)
        end_label.set_margin_top(6)
        content_box.append(end_label)
        end_fields_box, end_spinbuttons = self._create_time_input_fields(segment["end"])
        content_box.append(end_fields_box)

        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        # Connect response handler with segment index and spinbuttons
        dialog.connect(
            "response",
            self._on_edit_segment_dialog_response_spinbuttons,
            segment_index,
            start_spinbuttons,
            end_spinbuttons,
        )
        dialog.present()

    def _on_edit_segment_dialog_response_spinbuttons(
        self, dialog, response, segment_index, start_spinbuttons, end_spinbuttons
    ):
        """Handle the edit segment dialog response with spinbuttons"""
        if response == "save":
            try:
                start_time = self._get_time_from_spinbuttons(start_spinbuttons)
                end_time = self._get_time_from_spinbuttons(end_spinbuttons)

                if start_time < 0 or end_time < 0:
                    self._show_error_dialog(
                        _("Invalid Time"), _("Time values cannot be negative")
                    )
                    return

                if start_time >= end_time:
                    self._show_error_dialog(
                        _("Invalid Time Range"), _("Start time must be before end time")
                    )
                    return

                # Update the segment
                self.trim_segments[segment_index]["start"] = start_time
                self.trim_segments[segment_index]["end"] = end_time
                self.trim_segments.sort(key=lambda s: s["start"])
                self._update_segments_listbox()
                self._save_file_metadata()

            except (ValueError, IndexError) as e:
                self._show_error_dialog(_("Error"), str(e))

    def _show_error_dialog(self, title, message):
        """Show a simple error dialog"""
        error_dialog = Adw.MessageDialog.new(
            self.app.window,
            title,
        )
        error_dialog.set_body(message)
        error_dialog.add_response("ok", _("OK"))
        error_dialog.set_default_response("ok")
        error_dialog.present()

    def _on_remove_segment_clicked(self, button, start_time):
        self.trim_segments = [s for s in self.trim_segments if s["start"] != start_time]
        self._save_file_metadata()
        self._update_segments_listbox()

    def _on_clear_all_segments_with_confirmation(self, button):
        """Show confirmation dialog before clearing all segments"""
        if not self.trim_segments:
            return  # Nothing to clear

        dialog = Adw.MessageDialog.new(
            self.app.window,
            _("Clear All Segments?"),
        )
        dialog.set_body(
            _("This will remove all trim segments. This action cannot be undone.")
        )

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear All"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        dialog.connect("response", self._on_clear_confirmation_response)
        dialog.present()

    def _on_clear_confirmation_response(self, dialog, response):
        """Handle the clear confirmation dialog response"""
        if response == "clear":
            self.trim_segments = []
            self._save_file_metadata()
            self._update_segments_listbox()

    def _on_add_manual_segment_clicked(self, button):
        """Show dialog to manually add a new segment"""
        dialog = Adw.MessageDialog.new(
            self.app.window,
            _("Add Segment Manually"),
        )
        dialog.set_body(_("Enter start and end times for the new segment"))

        # Create content with separate time input fields
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        # Start time fields
        start_label = Gtk.Label(label=_("Start Time:"), xalign=0)
        content_box.append(start_label)
        start_fields_box, start_spinbuttons = self._create_time_input_fields(0)
        content_box.append(start_fields_box)

        # End time fields
        end_label = Gtk.Label(label=_("End Time:"), xalign=0)
        end_label.set_margin_top(6)
        content_box.append(end_label)
        end_fields_box, end_spinbuttons = self._create_time_input_fields(0)
        content_box.append(end_fields_box)

        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add Segment"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")

        # Connect response handler with spinbuttons
        dialog.connect(
            "response",
            self._on_add_manual_segment_response_spinbuttons,
            start_spinbuttons,
            end_spinbuttons,
        )
        dialog.present()

    def _on_add_manual_segment_response_spinbuttons(
        self, dialog, response, start_spinbuttons, end_spinbuttons
    ):
        """Handle the add manual segment dialog response with spinbuttons"""
        if response == "add":
            try:
                start_time = self._get_time_from_spinbuttons(start_spinbuttons)
                end_time = self._get_time_from_spinbuttons(end_spinbuttons)

                if start_time < 0 or end_time < 0:
                    self._show_error_dialog(
                        _("Invalid Time"), _("Time values cannot be negative")
                    )
                    return

                if start_time >= end_time:
                    self._show_error_dialog(
                        _("Invalid Time Range"), _("Start time must be before end time")
                    )
                    return

                # Add the new segment
                new_segment = {"start": start_time, "end": end_time}
                self.trim_segments.append(new_segment)
                self.trim_segments.sort(key=lambda s: s["start"])
                self._update_segments_listbox()
                self._save_file_metadata()

            except (ValueError, IndexError) as e:
                self._show_error_dialog(_("Error"), str(e))

    def update_crop_spinbuttons(self) -> None:
        if not hasattr(self.ui, "crop_left_spin"):
            return
        self.ui.crop_left_spin.set_value(self.crop_left)
        self.ui.crop_right_spin.set_value(self.crop_right)
        self.ui.crop_top_spin.set_value(self.crop_top)
        self.ui.crop_bottom_spin.set_value(self.crop_bottom)

    def on_saturation_changed(self, scale) -> None:
        self.saturation = scale.get_value()
        self._save_file_metadata_debounced()  # Use debounced save
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_saturation(self.saturation)
            # Don't refresh preview - MPV updates automatically
            # if not self.is_playing:
            #     self._refresh_preview()

    def on_hue_changed(self, scale) -> None:
        self.hue = scale.get_value()
        self._save_file_metadata_debounced()  # Use debounced save
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_hue(self.hue)
            # Don't refresh preview - MPV updates automatically
            # if not self.is_playing:
            #     self._refresh_preview()

    def _on_output_mode_changed(self, combo, pspec):
        """Handle output mode combo box change"""
        selected = combo.get_selected()
        self.output_mode = "join" if selected == 0 else "split"
        # Save to per-video metadata
        self._save_file_metadata()
        # Save to global settings as the new default for future videos
        self.settings.save_setting("multi-segment-output-mode", self.output_mode)

    def reset_brightness(self) -> None:
        self.brightness = 0.0
        self.ui.brightness_scale.set_value(self.brightness)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)
            if not self.is_playing:
                self._refresh_preview()

    def reset_saturation(self) -> None:
        self.saturation = 1.0
        self.ui.saturation_scale.set_value(self.saturation)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_saturation(self.saturation)
            if not self.is_playing:
                self._refresh_preview()

    def reset_hue(self) -> None:
        self.hue = 0.0
        self.ui.hue_scale.set_value(self.hue)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_hue(self.hue)
            if not self.is_playing:
                self._refresh_preview()

    def update_position_display(self, position) -> None:
        if self.video_duration > 0:
            time_str = self.format_time_precise(position)
            duration_str = self.format_time_precise(self.video_duration)
            self.ui.position_label.set_text(f"{time_str} / {duration_str}")

    def update_frame_counter(self, position) -> None:
        if (
            self.video_duration > 0
            and hasattr(self, "video_fps")
            and self.video_fps > 0
        ):
            current_frame = int(position * self.video_fps)
            total_frames = int(self.video_duration * self.video_fps)
            self.ui.frame_label.set_text(
                _("Frame: {current}/{total}").format(
                    current=current_frame, total=total_frames
                )
            )

    def _refresh_preview(self):
        if (
            not hasattr(self, "mpv_player")
            or not self.mpv_player
            or not hasattr(self, "current_position")
        ):
            return
        current_pos = self.current_position
        self.mpv_player.seek(current_pos)

    def on_position_changed(self, scale) -> None:
        position = scale.get_value()
        if abs(position - self.current_position) < 0.001:
            return
        self.current_position = position
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.seek(position)
            # Prevent position polling from overriding this seek
            self._seek_cooldown = True
            if self._seek_cooldown_timer_id:
                GLib.source_remove(self._seek_cooldown_timer_id)
            self._seek_cooldown_timer_id = GLib.timeout_add(
                500, self._end_seek_cooldown
            )
        self.update_position_display(position)
        self.update_frame_counter(position)

    def _end_seek_cooldown(self) -> bool:
        self._seek_cooldown = False
        self._seek_cooldown_timer_id = None
        return False

    def seek_relative(self, offset) -> None:
        new_position = self.current_position + offset
        new_position = max(0, min(new_position, self.video_duration))
        self.ui.position_scale.set_value(new_position)

    def on_play_pause_clicked(self, button) -> None:
        if not self.mpv_player or self.loading_video:
            return
        if self.is_playing:
            self.mpv_player.pause()
            self.is_playing = False
            self.ui.play_pause_button.set_icon_name('media-playback-start-symbolic')
            if self.position_update_id:
                GLib.source_remove(self.position_update_id)
                self.position_update_id = None
        else:
            self.mpv_player.play()
            self.is_playing = True
            self.ui.play_pause_button.set_icon_name('media-playback-pause-symbolic')
            if not self.position_update_id:
                self.position_update_id = GLib.timeout_add(
                    100, self._update_position_callback
                )

    def _update_position_callback(self):
        if not self.is_playing or not self.mpv_player:
            self.position_update_id = None
            return False
        if self.user_is_dragging_slider or self._seek_cooldown:
            return True
        pos = self.mpv_player.get_position()
        if pos is None:
            return True
        self.current_position = pos
        if self.position_changed_handler_id:
            self.ui.position_scale.handler_block(self.position_changed_handler_id)
        self.ui.position_scale.set_value(pos)
        if self.position_changed_handler_id:
            self.ui.position_scale.handler_unblock(self.position_changed_handler_id)
        self.update_position_display(pos)
        self.update_frame_counter(pos)
        return True

    def reset_crop_values(self) -> None:
        self.settings.save_setting("preview-crop-left", 0)
        self.settings.save_setting("preview-crop-right", 0)
        self.settings.save_setting("preview-crop-top", 0)
        self.settings.save_setting("preview-crop-bottom", 0)
        self.settings.save_setting("video-trim-start", 0.0)
        self.settings.save_setting("video-trim-end", -1.0)

    def on_volume_changed(self, scale) -> None:
        volume = scale.get_value()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_volume(volume)
            if hasattr(self.ui, "volume_button"):
                if volume == 0:
                    self.ui.volume_button.set_icon_name('audio-volume-muted-symbolic')
                elif volume < 0.33:
                    self.ui.volume_button.set_icon_name('audio-volume-low-symbolic')
                elif volume < 0.66:
                    self.ui.volume_button.set_icon_name('audio-volume-medium-symbolic')
                else:
                    self.ui.volume_button.set_icon_name('audio-volume-high-symbolic')

    def on_speed_changed(self, speed: float) -> None:
        """Handle playback speed change."""
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_speed(speed)

    # --- Noise Reduction Preview ---

    def update_nr_button_visibility(self) -> None:
        """Show/hide the NR sidebar group and auto-toggle audio filter preview.

        The group is visible when the audio handling is set to re-encode,
        giving the user access to audio cleaning configuration from the editor.
        Audio filters are applied whenever any filter is enabled and audio
        is set to re-encode — NR is just one optional filter in the chain.
        """
        if not hasattr(self.ui, "nr_sidebar_group"):
            return
        audio_reencode = self.app.audio_handling_combo.get_selected() == 1
        self.ui.nr_sidebar_group.set_visible(audio_reencode)
        # Auto-apply or remove audio filters
        if audio_reencode:
            self._apply_audio_filters()
        else:
            if hasattr(self, "mpv_player") and self.mpv_player:
                self.mpv_player.set_audio_filter("")

    def on_nr_preview_toggled(self, switch, state) -> bool:
        """Handle noise reduction preview toggle."""
        if state:
            self._apply_audio_filters()
        else:
            if hasattr(self, "mpv_player") and self.mpv_player:
                self.mpv_player.set_audio_filter("")
        return False

    def on_nr_strength_changed(self, scale) -> None:
        """Handle noise reduction strength slider change in preview."""
        self._apply_audio_filters()

    def _apply_audio_filters(self) -> None:
        """Debounced wrapper — schedules actual filter rebuild after 150ms.

        Prevents rapid-fire micro-seeks that can break the mpv audio pipeline.
        """
        if hasattr(self, "_audio_filter_timer") and self._audio_filter_timer:
            GLib.source_remove(self._audio_filter_timer)
        self._audio_filter_timer = GLib.timeout_add(150, self._do_apply_audio_filters)

    def _do_apply_audio_filters(self) -> bool:
        """Build and apply the full audio filter chain to the mpv player.

        Chain order: HPF → Compressor → Normalize → [NR] → Gate → EQ
        Each filter works independently — NR is optional.
        """
        self._audio_filter_timer = None
        if not (hasattr(self, "mpv_player") and self.mpv_player):
            return False

        sm = self.app.settings_manager
        filters = []

        # 1. High-Pass Filter
        if sm.get_boolean("hpf-enabled", False):
            freq = sm.load_setting("hpf-frequency", 80)
            filters.append(f"highpass=f={freq}:poles=2")

        # 2. Compressor
        if sm.get_boolean("compressor-enabled", False):
            import math

            intensity = float(sm.load_setting("compressor-intensity", 1.0))
            threshold_db = -20 - intensity * 20
            ratio = 3 + intensity * 7
            makeup_db = 6 + intensity * 12
            knee_db = 12 + intensity * 4
            threshold_lin = math.exp(threshold_db / 20 * math.log(10))
            makeup_lin = math.exp(makeup_db / 20 * math.log(10))
            knee_lin = math.exp(knee_db / 20 * math.log(10))
            filters.append(
                f"acompressor=threshold={threshold_lin:.6f}:ratio={ratio:.6f}"
                f":attack=150:release=800:makeup={makeup_lin:.6f}"
                f":knee={knee_lin:.6f}:detection=rms"
            )

        # 3. Volume Normalization (before NR for consistent input level)
        if sm.get_boolean("normalize-enabled", False):
            filters.append("speechnorm=e=12.5:r=0.0001:l=1")

        # 4. GTCRN Noise Reduction (only if NR enabled AND plugin exists)
        if sm.get_boolean("noise-reduction", False) and os.path.exists(
            "/usr/lib/ladspa/libgtcrn_ladspa.so"
        ):
            strength = sm.load_setting("noise-reduction-strength", 1.0)
            model = sm.load_setting("noise-model", 0)
            speech = sm.load_setting("noise-speech-strength", 1.0)
            lookahead = sm.load_setting("noise-lookahead", 50)
            blend = 1 if sm.get_boolean("noise-model-blend", False) else 0
            voice_recovery = sm.load_setting("noise-voice-recovery", 0.75)
            filters.append(
                f"ladspa=file=libgtcrn_ladspa:plugin=gtcrn_mono:"
                f"controls=c0=1|c1={strength}|c2={model}|"
                f"c3={speech}|c4={lookahead}|c5={blend}|c6={voice_recovery}"
            )

        # 5. Noise Gate (after NR — post-NR audio is mostly speech, so full-band detection is effective)
        if sm.get_boolean("noise-gate-enabled", False):
            import math

            intensity = float(sm.load_setting("noise-gate-intensity", 0.5))
            threshold_db = -50 + math.sqrt(intensity) * 35
            range_db = -40 - math.sqrt(intensity) * 50
            threshold_lin = math.exp(threshold_db / 20 * math.log(10))
            range_lin = math.exp(range_db / 20 * math.log(10))
            filters.append(
                f"agate=threshold={threshold_lin:.6f}:range={range_lin:.6f}"
                f":attack=10:release=250:ratio=4:detection=rms"
            )

        # 6. Equalizer
        if sm.get_boolean("eq-enabled", False):
            bands_str = sm.load_setting("eq-bands", "0,0,0,0,0,0,0,0,0,0")
            bands = [float(b) for b in str(bands_str).split(",")]
            freqs = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
            for i, freq in enumerate(freqs):
                gain = bands[i] if i < len(bands) else 0.0
                if gain != 0.0:
                    filters.append(f"equalizer=f={freq}:width_type=o:w=1.5:g={gain}")

        if filters:
            # Each filter wrapped in its own lavfi=[] to avoid mpv comma-splitting
            lavfi_filter = ",".join(f"lavfi=[{f}]" for f in filters)
            self.mpv_player.set_audio_filter(lavfi_filter)
        else:
            self.mpv_player.set_audio_filter("")
        return False

    def on_audio_track_changed(self, track_index: int) -> None:
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_audio_track(track_index)
            audio_tracks = self.mpv_player.get_audio_tracks()
            for track in audio_tracks:
                action = self.app.window.lookup_action(f"audio-track-{track['index']}")
                if action:
                    action.set_state(
                        GLib.Variant.new_boolean(track["index"] == track_index)
                    )

    def on_subtitle_track_changed(self, track_index: int) -> None:
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_subtitle_track(track_index)
            disabled_action = self.app.window.lookup_action("subtitle-track-disabled")
            if disabled_action:
                disabled_action.set_state(GLib.Variant.new_boolean(track_index == -1))
            subtitle_tracks = self.mpv_player.get_subtitle_tracks()
            for track in subtitle_tracks:
                action = self.app.window.lookup_action(
                    f"subtitle-track-{track['index']}"
                )
                if action:
                    action.set_state(
                        GLib.Variant.new_boolean(track["index"] == track_index)
                    )

    def update_audio_subtitle_controls(self) -> None:
        if not hasattr(self, "mpv_player") or not self.mpv_player:
            return
        self._populate_track_menus_attempts = 0
        GLib.timeout_add(200, self._populate_track_menus)

    def _populate_track_menus(self):
        if not hasattr(self, "mpv_player") or not self.mpv_player:
            return False
        audio_tracks = self.mpv_player.get_audio_tracks()
        subtitle_tracks = self.mpv_player.get_subtitle_tracks()
        if (
            not audio_tracks
            and not subtitle_tracks
            and self._populate_track_menus_attempts < 15
        ):
            self._populate_track_menus_attempts += 1
            return True
        self.ui.audio_track_menu.remove_all()
        self.ui.subtitle_menu.remove_all()
        if len(audio_tracks) > 1:
            for track in audio_tracks:
                action_name = f"audio-track-{track['index']}"
                if not self.app.window.lookup_action(action_name):
                    action = Gio.SimpleAction.new_stateful(
                        action_name,
                        None,
                        GLib.Variant.new_boolean(
                            track["index"] == self.mpv_player.current_audio_track
                        ),
                    )
                    action.connect(
                        "activate",
                        lambda a, p, idx=track["index"]: self.on_audio_track_changed(
                            idx
                        ),
                    )
                    self.app.window.add_action(action)
                self.ui.audio_track_menu.append(track["label"], f"win.{action_name}")
            self.ui.audio_track_button.set_visible(True)
        else:
            self.ui.audio_track_button.set_visible(False)
        if subtitle_tracks:
            action_name = "subtitle-track-disabled"
            if not self.app.window.lookup_action(action_name):
                action = Gio.SimpleAction.new_stateful(
                    action_name,
                    None,
                    GLib.Variant.new_boolean(
                        self.mpv_player.current_subtitle_track == -1
                    ),
                )
                action.connect(
                    "activate", lambda a, p: self.on_subtitle_track_changed(-1)
                )
                self.app.window.add_action(action)
            self.ui.subtitle_menu.append(_("Disabled"), f"win.{action_name}")
            for track in subtitle_tracks:
                action_name = f"subtitle-track-{track['index']}"
                if not self.app.window.lookup_action(action_name):
                    action = Gio.SimpleAction.new_stateful(
                        action_name,
                        None,
                        GLib.Variant.new_boolean(
                            track["index"] == self.mpv_player.current_subtitle_track
                        ),
                    )
                    action.connect(
                        "activate",
                        lambda a, p, idx=track["index"]: self.on_subtitle_track_changed(
                            idx
                        ),
                    )
                    self.app.window.add_action(action)
                self.ui.subtitle_menu.append(track["label"], f"win.{action_name}")
            self.ui.subtitle_button.set_visible(True)
        else:
            self.ui.subtitle_button.set_visible(False)
        return False

    def on_toggle_fullscreen(self, button) -> None:
        """Toggle video-only fullscreen (hides sidebar and toolbar)"""
        if self.is_video_fullscreen:
            self._exit_video_fullscreen()
        else:
            self._enter_video_fullscreen()

    def _on_fullscreen_changed(self):
        """Update fullscreen button icon based on current state"""
        if self.is_video_fullscreen:
            self.ui.fullscreen_button.set_icon_name('view-restore-symbolic')
        else:
            self.ui.fullscreen_button.set_icon_name('view-fullscreen-symbolic')

    def _enter_video_fullscreen(self):
        """Enter video-only fullscreen mode by hiding UI and fullscreening window"""
        if self.is_video_fullscreen:
            return

        # Store sidebar position before hiding
        if hasattr(self.app, 'main_paned'):
            self._saved_sidebar_position = self.app.main_paned.get_position()

        # Hide toolbar
        if hasattr(self.ui, "toolbar") and self.ui.toolbar:
            self.ui.toolbar.set_visible(False)

        # Hide the sidebar (left pane of main_paned)
        if hasattr(self.app, 'main_paned'):
            # Get the start child (sidebar)
            start_child = self.app.main_paned.get_start_child()
            if start_child:
                start_child.set_visible(False)
            # Set paned position to 0 to maximize video area
            self.app.main_paned.set_position(0)

        # Hide the header bar using Adw.ToolbarView's reveal property
        if hasattr(self.app, 'right_toolbar_view') and self.app.right_toolbar_view:
            self.app.right_toolbar_view.set_reveal_top_bars(False)

        # Fullscreen the main window
        self.app.window.fullscreen()

        # Update state
        self.is_video_fullscreen = True
        self._on_fullscreen_changed()
        
        # Force controls to be visible initially
        if hasattr(self.ui, 'overlay_controls'):
            self.ui.overlay_controls.set_visible(True)

    def _exit_video_fullscreen(self):
        """Exit video-only fullscreen mode by showing UI and unfullscreening window"""
        if not self.is_video_fullscreen:
            return

        # Unfullscreen the main window
        self.app.window.unfullscreen()

        # Show the header bar
        if hasattr(self.app, 'right_toolbar_view') and self.app.right_toolbar_view:
            self.app.right_toolbar_view.set_reveal_top_bars(True)

        # Show the sidebar (left pane of main_paned)
        if hasattr(self.app, 'main_paned'):
            # Get the start child (sidebar)
            start_child = self.app.main_paned.get_start_child()
            if start_child:
                start_child.set_visible(True)
            # Restore sidebar position
            if hasattr(self, '_saved_sidebar_position'):
                self.app.main_paned.set_position(self._saved_sidebar_position)
            else:
                # Default position if not saved
                self.app.main_paned.set_position(430)

        # Show toolbar
        if hasattr(self.ui, "toolbar") and self.ui.toolbar:
            self.ui.toolbar.set_visible(True)

        # Update state
        self.is_video_fullscreen = False
        self._on_fullscreen_changed()
