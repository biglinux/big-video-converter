import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gtk, Adw, Gio

# Setup translation
import gettext

_ = gettext.gettext

# Import the modules we've split off
from ui.video_edit_ui import VideoEditUI
from ui.video_processing import VideoProcessor
from ui.mpv_player import MPVPlayer

# Import from the unified video_settings module instead of separate modules
from utils.video_settings import (
    VideoAdjustmentManager,
)


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
        # Load default output mode from settings (last used by user)
        self.output_mode = self.settings.get_value("multi-segment-output-mode", "join")
        self.processor = VideoProcessor(self)
        self.ui = VideoEditUI(self)
        self.page = self.ui.create_page()
        # RENAMED: self.gst_player is now self.mpv_player
        self.mpv_player = MPVPlayer(self.ui.preview_video)
        self.is_playing = False
        self.user_is_dragging_slider = False
        self.loading_video = False
        self.requested_video_path = None
        self._populate_track_menus_attempts = 0

        # Simple fullscreen support - just hide UI elements
        self.is_video_fullscreen = False
        
        # Debounce timer for saving metadata to avoid file I/O on every slider change
        self.metadata_save_timeout = None

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
            # MPV handles render updates internally

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
            "output_mode": self.output_mode,
        })
        self.app_state.file_metadata[self.current_video_path] = metadata

    def _save_file_metadata_debounced(self):
        """Debounced version of _save_file_metadata to avoid excessive file I/O during slider drag"""
        # Cancel any pending save
        if self.metadata_save_timeout:
            GLib.source_remove(self.metadata_save_timeout)
        
        # Schedule save with 500ms delay - will only execute after user stops dragging
        def do_save():
            self.metadata_save_timeout = None
            self._save_file_metadata()
            return False
        
        self.metadata_save_timeout = GLib.timeout_add(500, do_save)

    def set_video(self, file_path):
        if self.loading_video:
            return False
        if not file_path or not os.path.exists(file_path):
            return False
        if self.current_video_path == file_path:
            self._load_file_metadata(file_path)
            return True
        self.loading_video = True
        self.requested_video_path = file_path
        self.current_video_path = file_path
        # Reset cleanup flag when loading a new video
        self.cleanup_called = False
        self.ui.info_dimensions_label.set_text("...")
        self.ui.info_codec_label.set_text("...")
        self.ui.info_filesize_label.set_text("...")
        self.ui.info_duration_label.set_text("...")
        self.ui.position_scale.set_value(0)
        self.update_position_display(0)
        return self.processor.load_video(file_path)

    def get_page(self):
        return self.page

    def cleanup(self):
        """Clean up resources when leaving the edit page"""
        if getattr(self, "cleanup_called", False):
            return
        self.cleanup_called = True
        print("VideoEditPage: Starting cleanup")

        # Stop position updates
        if hasattr(self, "position_update_id") and self.position_update_id:
            print("VideoEditPage: Removing position update timer")
            GLib.source_remove(self.position_update_id)
            self.position_update_id = None

        # Update UI immediately before stopping playback
        self.is_playing = False
        if hasattr(self, "ui") and self.ui:
            self.ui.play_pause_button.set_icon_name('media-playback-start-symbolic')

        # Explicitly cleanup MPV player (non-blocking)
        if hasattr(self, "mpv_player") and self.mpv_player:
            print("VideoEditPage: Cleaning up MPV player")
            # Just call cleanup once - it handles everything
            self.mpv_player.cleanup()

        # Clear video path
        if hasattr(self, "current_video_path"):
            self.current_video_path = None
        
        print("VideoEditPage: Cleanup complete")

    def on_brightness_changed(self, scale):
        self.brightness = scale.get_value()
        self._save_file_metadata_debounced()  # Use debounced save to avoid file I/O during drag
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)
            # Don't refresh preview during drag - MPV updates automatically
            # if not self.is_playing:
            #     self._refresh_preview()

    def on_crop_value_changed(self, spinbutton):
        """Handle crop value changes - ensures preview updates immediately"""
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

        # Set crop in player - MPV should update automatically
        self.mpv_player.set_crop(left, right, top, bottom)

    def format_time_precise(self, seconds):
        if seconds is None:
            seconds = 0
        hours = int(seconds) // 3600
        minutes = (int(seconds) % 3600) // 60
        seconds_remainder = int(seconds) % 60
        milliseconds = int((seconds - int(seconds)) * 1000)
        return f"{hours}:{minutes:02d}:{seconds_remainder:02d}.{milliseconds:03d}"

    def on_mark_segment_point(self, button):
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

    def on_mark_cancel(self, button):
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
            row.set_title(f"Segment {i + 1}: {start_str} → {end_str}")
            row.set_subtitle(f"Duration: {duration_str}")
            button_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            goto_button = Gtk.Button(
                icon_name='media-playback-start-symbolic',
                css_classes=["flat"],
                tooltip_text=_("Go to segment start"),
            )
            goto_button.connect(
                "clicked", self._on_goto_segment_clicked, segment["start"]
            )
            button_box.append(goto_button)
            edit_button = Gtk.Button(
                icon_name='document-edit-symbolic',
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

    def on_clear_all_segments(self, button):
        self.trim_segments = []
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

    def update_crop_spinbuttons(self):
        if not hasattr(self.ui, "crop_left_spin"):
            return
        self.ui.crop_left_spin.set_value(self.crop_left)
        self.ui.crop_right_spin.set_value(self.crop_right)
        self.ui.crop_top_spin.set_value(self.crop_top)
        self.ui.crop_bottom_spin.set_value(self.crop_bottom)

    def reset_crop_value(self, position):
        if position == "left":
            self.crop_left = 0
        elif position == "right":
            self.crop_right = 0
        elif position == "top":
            self.crop_top = 0
        elif position == "bottom":
            self.crop_bottom = 0
        self._save_file_metadata()
        self.update_crop_spinbuttons()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_crop(
                self.crop_left, self.crop_right, self.crop_top, self.crop_bottom
            )
            # MPV handles render updates internally

    def on_saturation_changed(self, scale):
        self.saturation = scale.get_value()
        self._save_file_metadata_debounced()  # Use debounced save
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_saturation(self.saturation)
            # Don't refresh preview - MPV updates automatically
            # if not self.is_playing:
            #     self._refresh_preview()

    def on_hue_changed(self, scale):
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

    def reset_brightness(self):
        self.brightness = 0.0
        self.ui.brightness_scale.set_value(self.brightness)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)
            if not self.is_playing:
                self._refresh_preview()

    def reset_saturation(self):
        self.saturation = 1.0
        self.ui.saturation_scale.set_value(self.saturation)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_saturation(self.saturation)
            if not self.is_playing:
                self._refresh_preview()

    def reset_hue(self):
        self.hue = 0.0
        self.ui.hue_scale.set_value(self.hue)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_hue(self.hue)
            if not self.is_playing:
                self._refresh_preview()

    def update_position_display(self, position):
        if self.video_duration > 0:
            time_str = self.format_time_precise(position)
            duration_str = self.format_time_precise(self.video_duration)
            self.ui.position_label.set_text(f"{time_str} / {duration_str}")

    def update_frame_counter(self, position):
        if (
            self.video_duration > 0
            and hasattr(self, "video_fps")
            and self.video_fps > 0
        ):
            current_frame = int(position * self.video_fps)
            total_frames = int(self.video_duration * self.video_fps)
            self.ui.frame_label.set_text(f"Frame: {current_frame}/{total_frames}")

    def _refresh_preview(self):
        if (
            not hasattr(self, "mpv_player")
            or not self.mpv_player
            or not hasattr(self, "current_position")
        ):
            return
        current_pos = self.current_position
        self.mpv_player.seek(current_pos)

    def on_position_changed(self, scale):
        position = scale.get_value()
        if abs(position - self.current_position) < 0.001:
            return
        self.current_position = position
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.seek(position)
        self.update_position_display(position)
        self.update_frame_counter(position)

    def seek_relative(self, offset):
        new_position = self.current_position + offset
        new_position = max(0, min(new_position, self.video_duration))
        self.ui.position_scale.set_value(new_position)

    def on_play_pause_clicked(self, button):
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
        if self.user_is_dragging_slider:
            return True
        pos = self.mpv_player.get_position()
        self.current_position = pos
        if self.position_changed_handler_id:
            self.ui.position_scale.handler_block(self.position_changed_handler_id)
        self.ui.position_scale.set_value(pos)
        if self.position_changed_handler_id:
            self.ui.position_scale.handler_unblock(self.position_changed_handler_id)
        self.update_position_display(pos)
        self.update_frame_counter(pos)
        return True

    def reset_crop_values(self):
        self.settings.save_setting("preview-crop-left", 0)
        self.settings.save_setting("preview-crop-right", 0)
        self.settings.save_setting("preview-crop-top", 0)
        self.settings.save_setting("preview-crop-bottom", 0)
        self.settings.save_setting("video-trim-start", 0.0)
        self.settings.save_setting("video-trim-end", -1.0)

    def on_volume_changed(self, scale):
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

    def on_audio_track_changed(self, track_index):
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_audio_track(track_index)
            audio_tracks = self.mpv_player.get_audio_tracks()
            for track in audio_tracks:
                action = self.app.window.lookup_action(f"audio-track-{track['index']}")
                if action:
                    action.set_state(
                        GLib.Variant.new_boolean(track["index"] == track_index)
                    )

    def on_subtitle_track_changed(self, track_index):
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

    def update_audio_subtitle_controls(self):
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

    def on_toggle_fullscreen(self, button):
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
