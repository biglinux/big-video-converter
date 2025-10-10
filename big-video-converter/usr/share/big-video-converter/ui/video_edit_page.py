import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gdk, Gtk

# Setup translation
import gettext

_ = gettext.gettext

# Import the modules we've split off
from ui.video_edit_ui import VideoEditUI
from ui.video_processing import VideoProcessor

# Import from the unified video_settings module instead of separate modules
from utils.video_settings import (
    get_adjustment_value,
    save_adjustment_value,
    reset_adjustment,
    generate_video_filters,
    generate_all_filters,
    VideoAdjustmentManager,  # Import from video_settings instead of video_adjustment_manager
)


class VideoEditPage:
    def __init__(self, app):
        self.app = app

        self.settings = app.settings_manager

        self.current_video_path = None
        self.video_duration = 0  # Duration in seconds
        self.current_position = 0  # Current position in seconds

        # Reset crop values at initialization
        self.reset_crop_values()

        # Load trim settings from settings manager
        self.start_time = self.settings.load_setting("video-trim-start", 0.0)
        end_time_setting = self.settings.load_setting("video-trim-end", -1.0)
        self.end_time = None if end_time_setting < 0 else end_time_setting

        self.position_update_id = None
        self.position_changed_handler_id = None  # Store handler ID for blocking
        self.crop_update_timeout_id = None  # Timer ID for delayed crop updates

        # Video dimensions
        self.video_width = 0
        self.video_height = 0
        self.video_fps = 25  # Default fps value

        # Initialize the adjustment manager
        self.adjustment_manager = VideoAdjustmentManager(self.settings, self)

        # Set properties directly from the adjustment manager
        self.crop_left = self.adjustment_manager.get_value("crop_left")
        self.crop_right = self.adjustment_manager.get_value("crop_right")
        self.crop_top = self.adjustment_manager.get_value("crop_top")
        self.crop_bottom = self.adjustment_manager.get_value("crop_bottom")
        self.brightness = self.adjustment_manager.get_value("brightness")
        self.contrast = self.adjustment_manager.get_value("contrast")
        self.saturation = self.adjustment_manager.get_value("saturation")
        self.gamma = self.adjustment_manager.get_value("gamma")
        self.gamma_r = self.adjustment_manager.get_value("gamma_r")
        self.gamma_g = self.adjustment_manager.get_value("gamma_g")
        self.gamma_b = self.adjustment_manager.get_value("gamma_b")
        self.gamma_weight = self.adjustment_manager.get_value("gamma_weight")
        self.hue = self.adjustment_manager.get_value("hue")

        # Create a dictionary to store tooltips for different sliders and buttons
        self.adjustment_tooltips = {}
        self.button_tooltips = {}

        # Initialize the video processor
        self.processor = VideoProcessor(self)

        # Initialize the UI component
        self.ui = VideoEditUI(self)
        self.page = self.ui.create_page()

        # Add loading lock to prevent multiple simultaneous video loads
        self.loading_video = False
        self.requested_video_path = None

    # Add a destructor to ensure cleanup is called
    def __del__(self):
        self.cleanup()

    def set_video(self, file_path):
        """Set the video file from the main application and load it"""
        print(f"VideoEditPage.set_video called with file_path: {file_path}")

        # Verify path exists before loading
        if not file_path or not os.path.exists(file_path):
            print(f"Invalid file path for video: {file_path}")
            return False

        # Don't reload if it's already the current video
        if self.current_video_path == file_path:
            print(f"Video already loaded: {file_path}")
            return True

        # Store the requested path - do this first to prevent race conditions
        self.requested_video_path = file_path

        # If we're currently loading a video, don't start another load
        if self.loading_video:
            print("Already loading a video, can't load another one now")
            print(f"Will try again in 500ms: {file_path}")
            # Schedule another attempt after a short delay
            GLib.timeout_add(500, lambda: self._retry_load_video(file_path))
            return True

        # Start the loading process
        self.loading_video = True

        # Clear any existing video data
        self.current_video_path = None

        # Reset frame cache and position
        self.current_position = 0

        # Load video with a slight delay to ensure UI updates properly
        GLib.idle_add(lambda: self._delayed_load_video(file_path))
        return True

    def _retry_load_video(self, file_path):
        """Retry loading video after a short delay"""
        print(f"Retrying video load: {file_path}")

        # If still loading, reschedule
        if self.loading_video:
            print("Still loading previous video, will try again in 500ms")
            return True  # Continue trying

        # Check if the file still exists
        if not file_path or not os.path.exists(file_path):
            print(f"File no longer exists: {file_path}")
            return False

        # Try loading the video again
        return self.set_video(file_path)

    def _delayed_load_video(self, file_path):
        """Load video with a slight delay to ensure UI updates properly"""
        try:
            # Double-check requested path is still valid
            if not file_path or not os.path.exists(file_path):
                print(f"File no longer exists: {file_path}")
                self.loading_video = False
                return False

            # Make sure this is still the file we want to load
            if hasattr(self.app, "preview_file_path") and self.app.preview_file_path:
                if file_path != self.app.preview_file_path:
                    print(
                        f"Ignoring conflicting video load: requested={self.app.preview_file_path}, attempted={file_path}"
                    )
                    self.loading_video = False
                    return False

            # Make sure this is the file we're supposed to load
            if file_path != self.requested_video_path:
                print(
                    f"Conflicting load requests. Requested: {self.requested_video_path}, Loading: {file_path}"
                )
                # Don't reset loading flag here - let the proper requested file load
                return False

            # Update UI to indicate loading
            self.ui.info_filename_label.set_text(_("Loading..."))

            # Perform the actual loading
            result = self.processor.load_video(file_path)
            print(f"Video load result for {os.path.basename(file_path)}: {result}")

            # Always reset the loading flag when done, regardless of success
            self.loading_video = False

            return False  # Don't repeat
        except Exception as e:
            print(f"Error in delayed video loading: {e}")
            import traceback

            traceback.print_exc()
            # Make sure to reset loading flag even on error
            self.loading_video = False
            return False

    def get_page(self):
        return self.page

    def format_resolution(self, width, height):
        """
        Format resolution string with the correct separator.
        FFmpeg requires width:height format (not width×height).

        Args:
            width (int): Video width
            height (int): Video height

        Returns:
            str: Formatted resolution string (e.g. "1920:1080")
        """
        return f"{width}:{height}"

    def cleanup(self):
        """Clean up resources when the page is destroyed"""
        # Cancel any pending crop update
        if hasattr(self, "crop_update_timeout_id") and self.crop_update_timeout_id:
            GLib.source_remove(self.crop_update_timeout_id)
            self.crop_update_timeout_id = None

        # Clear any update timers
        if hasattr(self, "position_update_id") and self.position_update_id:
            GLib.source_remove(self.position_update_id)
            self.position_update_id = None

    def invalidate_current_frame_cache(self):
        """Invalidate the cache for the current position - no longer needed"""
        # This is now a no-op since we're not caching frames
        pass

    def extract_frame(self, position):
        """Extract a frame at the specified position using FFmpeg"""
        if not self.current_video_path:
            print("Cannot extract frame - no video loaded")
            return None

        try:
            # Get the filters using our shared utility
            # Pass current video path for H.265 10-bit detection
            filters = generate_video_filters(
                self.settings,
                video_width=self.video_width,
                video_height=self.video_height,
                input_file=self.current_video_path,
            )

            filter_arg = ",".join(filters) if filters else "null"

            # Print the filter for debugging
            print(f"FFmpeg filter: {filter_arg}")

        except Exception as e:
            print(f"Error extracting frame: {e}")
            import traceback

            traceback.print_exc()
            return False

        # Use the centralized filter utilities
        filters = generate_all_filters(
            self.settings, video_width=self.video_width, video_height=self.video_height
        )

        # Add video resolution filter if needed
        # video_resolution = self.settings.load_setting("video-resolution", "")
        # if video_resolution:
        #     filters.insert(0, f"scale={video_resolution}")

        filter_arg = ",".join(filters) if filters else "null"

        # Print the filter for debugging
        print(f"FFmpeg filter for frame extraction: {filter_arg}")

    def on_brightness_changed(self, scale, value_label=None):
        """Handle brightness slider changes"""
        brightness = scale.get_value()

        # Save to settings using the centralized utility
        save_adjustment_value(self.settings, "brightness", brightness)

        if value_label:
            value_label.set_text(f"{brightness:.2f}")

        # Update current frame with new settings
        self.extract_frame(self.current_position)

    def on_contrast_changed(self, scale, value_label=None):
        """Handle contrast slider changes"""
        self.contrast = scale.get_value()
        self.adjustment_manager.set_value("contrast", self.contrast, update_ui=False)

        if value_label:
            value_label.set_text(f"{self.contrast:.2f}")

        # Debug message for contrast value
        print(
            f"Contrast value set to: {self.contrast:.2f} (FFmpeg eq filter value in range 0.0-2.0)"
        )

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()

        # Update current frame with new settings
        self.processor.extract_frame(self.current_position)

    def reset_brightness(self):
        """Reset brightness to default"""
        reset_adjustment(self.settings, "brightness")
        self.brightness = get_adjustment_value(self.settings, "brightness")
        self.brightness_scale.set_value(self.brightness)
        self.extract_frame(self.current_position)

    def on_crop_value_changed(self, widget):
        """Handle changes to crop spinbutton values with delayed update"""
        # Get and store all crop values
        self.crop_left = int(self.crop_left_spin.get_value())
        self.crop_right = int(self.crop_right_spin.get_value())
        self.crop_top = int(self.crop_top_spin.get_value())
        self.crop_bottom = int(self.crop_bottom_spin.get_value())

        # Save using the centralized utility
        save_adjustment_value(self.settings, "crop_left", self.crop_left)
        save_adjustment_value(self.settings, "crop_right", self.crop_right)
        save_adjustment_value(self.settings, "crop_top", self.crop_top)
        save_adjustment_value(self.settings, "crop_bottom", self.crop_bottom)

    # ========= VIDEO EDIT HANDLERS METHODS =========

    def format_time_precise(self, seconds):
        """Format time in seconds to HH:MM:SS.mmm format"""
        if seconds is None:
            seconds = 0

        hours = int(seconds) // 3600
        minutes = (int(seconds) % 3600) // 60
        seconds_remainder = int(seconds) % 60
        milliseconds = int((seconds - int(seconds)) * 1000)

        return f"{hours}:{minutes:02d}:{seconds_remainder:02d}.{milliseconds:03d}"

    def on_set_start_time(self, button):
        """Set the current position as the start time for trimming"""
        new_start_time = self.current_position

        # Validate that start_time is less than end_time (if end_time is set)
        if self.end_time is not None and new_start_time >= self.end_time:
            # Show warning to user
            secondary_text = _(
                "Start time must be less than end time. Please select an earlier position."
            )

            dialog = Gtk.MessageDialog(
                transient_for=self.app.window,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=_("Invalid trim start time"),
            )

            # Create a box for the secondary text
            content_area = dialog.get_content_area()
            secondary_label = Gtk.Label(label=secondary_text)
            secondary_label.set_wrap(True)
            secondary_label.set_xalign(0)
            secondary_label.add_css_class("dim-label")
            secondary_label.set_margin_start(18)
            secondary_label.set_margin_end(18)
            secondary_label.set_margin_bottom(12)
            content_area.append(secondary_label)

            dialog.connect("response", lambda dialog, response: dialog.destroy())
            dialog.show()
            return

        # Set the valid start time
        self.start_time = new_start_time
        # Save to settings
        self.settings.save_setting("video-trim-start", self.start_time)
        self.update_trim_display()

    def on_set_end_time(self, button):
        """Set the current position as the end time for trimming"""
        new_end_time = self.current_position

        # Validate that end_time is greater than start_time
        if new_end_time <= self.start_time:
            # Show warning to user
            secondary_text = _(
                "End time must be greater than start time. Please select a later position."
            )

            dialog = Gtk.MessageDialog(
                transient_for=self.app.window,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=_("Invalid trim end time"),
            )

            # Create a box for the secondary text
            content_area = dialog.get_content_area()
            secondary_label = Gtk.Label(label=secondary_text)
            secondary_label.set_wrap(True)
            secondary_label.set_xalign(0)
            secondary_label.add_css_class("dim-label")
            secondary_label.set_margin_start(18)
            secondary_label.set_margin_end(18)
            secondary_label.set_margin_bottom(12)
            content_area.append(secondary_label)

            dialog.connect("response", lambda dialog, response: dialog.destroy())
            dialog.show()
            return

        # Set the valid end time
        self.end_time = new_end_time
        # Save to settings
        self.settings.save_setting("video-trim-end", self.end_time)
        self.update_trim_display()

    def on_reset_trim_points(self, button):
        """Reset trim points to full video"""
        self.start_time = 0
        self.end_time = None  # None means end of video
        # Save to settings
        self.settings.save_setting("video-trim-start", 0.0)
        self.settings.save_setting("video-trim-end", -1.0)  # -1 means no end trim
        self.update_trim_display()

    def update_trim_display(self):
        """Update the trim time displays"""
        # Format start time
        self.ui.start_time_label.set_text(self.format_time_precise(self.start_time))

        # Format end time (handle None as video duration)
        end_time = self.end_time if self.end_time is not None else self.video_duration
        self.ui.end_time_label.set_text(self.format_time_precise(end_time))

        # Calculate and format duration
        duration = end_time - self.start_time
        self.ui.duration_label.set_text(self.format_time_precise(duration))

    def update_crop_spinbuttons(self):
        """Update crop spinbutton values from settings"""
        # Only update if UI is initialized
        if not hasattr(self.ui, "crop_left_spin"):
            return

        # Set spinbutton values from current crop settings
        self.ui.crop_left_spin.set_value(self.crop_left)
        self.ui.crop_right_spin.set_value(self.crop_right)
        self.ui.crop_top_spin.set_value(self.crop_top)
        self.ui.crop_bottom_spin.set_value(self.crop_bottom)

        # Calculate the resulting dimensions
        crop_width = self.video_width - self.crop_left - self.crop_right
        crop_height = self.video_height - self.crop_top - self.crop_bottom

        # Update the result size label
        self.ui.crop_result_label.set_markup(
            f"<small>{_('Final size')}: {crop_width}×{crop_height}</small>"
        )

    def on_crop_value_changed(self, widget):
        """Handle changes to crop spinbutton values with delayed update"""
        # Store the new crop values using the adjustment manager
        self.crop_left = int(self.ui.crop_left_spin.get_value())
        self.crop_right = int(self.ui.crop_right_spin.get_value())
        self.crop_top = int(self.ui.crop_top_spin.get_value())
        self.crop_bottom = int(self.ui.crop_bottom_spin.get_value())

        # Save via manager - don't update UI since we're already doing that
        self.adjustment_manager.set_value("crop_left", self.crop_left, update_ui=False)
        self.adjustment_manager.set_value(
            "crop_right", self.crop_right, update_ui=False
        )
        self.adjustment_manager.set_value("crop_top", self.crop_top, update_ui=False)
        self.adjustment_manager.set_value(
            "crop_bottom", self.crop_bottom, update_ui=False
        )

        # Calculate the resulting dimensions
        crop_width = self.video_width - self.crop_left - self.crop_right
        crop_height = self.video_height - self.crop_top - self.crop_bottom

        # Update the result size label immediately
        self.ui.crop_result_label.set_markup(
            f"<small>{_('Final size')}: {crop_width}×{crop_height}</small>"
        )

        # Cancel any existing timeout to avoid multiple updates
        if self.crop_update_timeout_id:
            GLib.source_remove(self.crop_update_timeout_id)

        # Set a new timeout for 300ms
        self.crop_update_timeout_id = GLib.timeout_add(300, self._delayed_crop_update)

    def _delayed_crop_update(self):
        """Handle the delayed update after crop values have changed"""
        # Clear the timeout ID since it has completed
        self.crop_update_timeout_id = None

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()

        # Refresh the preview with the new crop settings
        self.processor.extract_frame(self.current_position)

        # Return False to ensure the timer doesn't repeat
        return False

    def reset_crop_value(self, position):
        """Reset a specific crop value to 0"""
        if position == "left":
            self.adjustment_manager.reset_value("crop_left")
            self.crop_left = 0
        elif position == "right":
            self.adjustment_manager.reset_value("crop_right")
            self.crop_right = 0
        elif position == "top":
            self.adjustment_manager.reset_value("crop_top")
            self.crop_top = 0
        elif position == "bottom":
            self.adjustment_manager.reset_value("crop_bottom")
            self.crop_bottom = 0

    # Video adjustment handlers
    def on_brightness_changed(self, scale, value_label=None):
        """Handle brightness slider changes"""
        self.brightness = scale.get_value()
        self.adjustment_manager.set_value(
            "brightness", self.brightness, update_ui=False
        )

        if value_label:
            value_label.set_text(f"{self.brightness:.1f}")

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()

        # Update current frame with new settings
        self.processor.extract_frame(self.current_position)

    def on_contrast_changed(self, scale, value_label=None):
        """Handle contrast slider changes"""
        self.contrast = scale.get_value()
        self.adjustment_manager.set_value("contrast", self.contrast, update_ui=False)

        if value_label:
            value_label.set_text(f"{self.contrast:.2f}")

        # Debug message for contrast value
        print(
            f"Contrast value set to: {self.contrast:.2f} (FFmpeg eq filter value in range 0.0-2.0)"
        )

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()

        # Update current frame with new settings
        self.processor.extract_frame(self.current_position)

    def on_saturation_changed(self, scale, value_label=None):
        """Handle saturation slider changes"""
        self.saturation = scale.get_value()
        self.adjustment_manager.set_value(
            "saturation", self.saturation, update_ui=False
        )

        if value_label:
            value_label.set_text(f"{self.saturation:.1f}")

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()

        # Update current frame with new settings
        self.processor.extract_frame(self.current_position)

    def on_gamma_changed(self, scale, value_label=None):
        """Handle gamma slider changes"""
        self.gamma = scale.get_value()
        self.adjustment_manager.set_value("gamma", self.gamma, update_ui=False)

        if value_label:
            value_label.set_text(f"{self.gamma:.1f}")

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def on_gamma_r_changed(self, scale, value_label=None):
        """Handle red gamma slider changes"""
        self.gamma_r = scale.get_value()
        self.adjustment_manager.set_value("gamma_r", self.gamma_r, update_ui=False)

        if value_label:
            value_label.set_text(f"{self.gamma_r:.1f}")

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def on_gamma_g_changed(self, scale, value_label=None):
        """Handle green gamma slider changes"""
        self.gamma_g = scale.get_value()
        self.adjustment_manager.set_value("gamma_g", self.gamma_g, update_ui=False)

        if value_label:
            value_label.set_text(f"{self.gamma_g:.1f}")

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def on_gamma_b_changed(self, scale, value_label=None):
        """Handle blue gamma slider changes"""
        self.gamma_b = scale.get_value()
        self.adjustment_manager.set_value("gamma_b", self.gamma_b, update_ui=False)

        if value_label:
            value_label.set_text(f"{self.gamma_b:.1f}")

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def on_gamma_weight_changed(self, scale, value_label=None):
        """Handle gamma weight slider changes"""
        self.gamma_weight = scale.get_value()
        self.adjustment_manager.set_value(
            "gamma_weight", self.gamma_weight, update_ui=False
        )

        if value_label:
            value_label.set_text(f"{self.gamma_weight:.2f}")

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def on_hue_changed(self, scale, value_label=None):
        """Handle hue slider changes"""
        self.hue = scale.get_value()
        self.adjustment_manager.set_value("hue", self.hue, update_ui=False)

        if value_label:
            value_label.set_text(f"{self.hue:.2f}")

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    # Reset functions for adjustments
    def reset_brightness(self):
        """Reset brightness to default"""
        self.adjustment_manager.reset_value("brightness")
        self.brightness = 0.0
        self.ui.brightness_scale.set_value(self.brightness)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_contrast(self):
        """Reset contrast to default"""
        self.adjustment_manager.reset_value("contrast")
        self.contrast = 1.0
        self.ui.contrast_scale.set_value(self.contrast)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_saturation(self):
        """Reset saturation to default"""
        self.adjustment_manager.reset_value("saturation")
        self.saturation = 1.0
        self.ui.saturation_scale.set_value(self.saturation)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_gamma(self):
        """Reset gamma to default"""
        self.adjustment_manager.reset_value("gamma")
        self.gamma = 1.0
        self.ui.gamma_scale.set_value(self.gamma)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_gamma_r(self):
        """Reset red gamma to default"""
        self.adjustment_manager.reset_value("gamma_r")
        self.gamma_r = 1.0
        self.ui.red_gamma_scale.set_value(self.gamma_r)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_gamma_g(self):
        """Reset green gamma to default"""
        self.adjustment_manager.reset_value("gamma_g")
        self.gamma_g = 1.0
        self.ui.green_gamma_scale.set_value(self.gamma_g)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_gamma_b(self):
        """Reset blue gamma to default"""
        self.adjustment_manager.reset_value("gamma_b")
        self.gamma_b = 1.0
        self.ui.blue_gamma_scale.set_value(self.gamma_b)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_gamma_weight(self):
        """Reset gamma weight to default"""
        self.adjustment_manager.reset_value("gamma_weight")
        self.gamma_weight = 1.0
        self.ui.gamma_weight_scale.set_value(self.gamma_weight)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_hue(self):
        """Reset hue to default"""
        self.adjustment_manager.reset_value("hue")
        self.hue = 0.0
        self.ui.hue_scale.set_value(self.hue)

        # Invalidate cache for current position before extracting new frame
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    # ========= ENHANCED TOOLTIP HANDLERS WITH GTKGIZMO SUPPORT =========

    def find_slider_gizmo(self, slider):
        """Find the GtkGizmo child that represents the actual slider track"""
        # Try to find the slider's primary GtkGizmo - the track/trough component
        if not slider or not hasattr(slider, "get_first_child"):
            return None

        # In GTK4 scale implementation, the slider track is typically the first GtkGizmo
        # child or grandchild of the scale widget
        def find_gizmo(widget):
            if not widget:
                return None

            # Check if this widget is a GtkGizmo
            widget_type = type(widget).__name__
            if "Gizmo" in widget_type:
                return widget

            # Check first child
            child = (
                widget.get_first_child() if hasattr(widget, "get_first_child") else None
            )
            gizmo = find_gizmo(child)
            if gizmo:
                return gizmo

            # Check next sibling
            sibling = (
                widget.get_next_sibling()
                if hasattr(widget, "get_next_sibling")
                else None
            )
            return find_gizmo(sibling)

        return find_gizmo(slider.get_first_child())

    def get_slider_value_at_position(self, slider, x):
        """Calculate value at a given x-position for a slider, using GtkGizmo if possible"""
        # Get slider adjustment values
        adjustment = slider.get_adjustment()
        min_value = adjustment.get_lower()
        max_value = adjustment.get_upper()

        # Try to find the slider track GtkGizmo
        gizmo = self.find_slider_gizmo(slider)

        if gizmo:
            # Let's use the GtkGizmo directly to calculate the position
            # Get GtkGizmo allocation coordinates relative to the slider
            gizmo_alloc = [0, 0]  # [x, width]

            # In GTK4, we need to traverse upward to get the allocation
            # relative to the slider widget
            parent = gizmo
            while parent and parent != slider:
                if hasattr(parent, "get_allocation"):
                    allocation = parent.get_allocation()
                    gizmo_alloc[0] += allocation.x if hasattr(allocation, "x") else 0
                parent = parent.get_parent() if hasattr(parent, "get_parent") else None

            # Get the GtkGizmo dimensions
            if hasattr(gizmo, "get_width"):
                gizmo_width = gizmo.get_width()
            else:
                # Fallback - use the slider width
                gizmo_width = slider.get_width()

            # Calculate the relative position within the GtkGizmo
            gizmo_relative_x = x - gizmo_alloc[0]

            # Clamp to GtkGizmo boundaries
            if gizmo_relative_x <= 0:
                return min_value
            elif gizmo_relative_x >= gizmo_width:
                return max_value
            else:
                # Calculate ratio within the GtkGizmo bounds
                ratio = gizmo_relative_x / gizmo_width
                return min_value + (ratio * (max_value - min_value))
        else:
            # Fallback if we couldn't find the GtkGizmo
            width = slider.get_width()
            if width <= 0:
                return 0

            # Simple ratio calculation
            ratio = max(0.0, min(1.0, x / width))
            return min_value + (ratio * (max_value - min_value))

    def on_slider_motion(self, controller, x, y):
        """Show tooltip for timeline slider using direct GtkGizmo handling"""
        if self.video_duration <= 0:
            return

        slider = controller.get_widget()
        if slider is None or not isinstance(slider, Gtk.Widget):
            return

        try:
            # Calculate hover time directly using the GtkGizmo approach
            hover_time = self.get_slider_value_at_position(slider, x)

            # Snap to frame boundaries if possible
            if hasattr(self, "video_fps") and self.video_fps > 0:
                frame_time = 1.0 / self.video_fps
                frame = round(hover_time / frame_time)
                hover_time = frame * frame_time

            # Update tooltip
            tooltip_text = self.format_time_precise(hover_time)
            self.ui.tooltip_label.set_text(tooltip_text)

            # Position tooltip
            rect = Gdk.Rectangle()
            rect.x = x
            rect.y = 0
            rect.width = 1
            rect.height = 1

            # Ensure proper parent-child relationship for popover
            popover = self.ui.tooltip_popover
            if popover.get_parent() != slider:
                popover.set_parent(slider)

            popover.set_pointing_to(rect)

            # Only show if slider is mapped and visible
            if slider.get_mapped() and slider.get_visible():
                popover.popup()

            # Store hover position
            self.hover_position = hover_time

        except Exception as e:
            print(f"Error showing slider tooltip: {e}")

    def on_slider_click(self, gesture, n_press, x, y):
        """Jump to position when slider is clicked - using same GtkGizmo handling"""
        if self.video_duration <= 0 or n_press != 1:
            return

        slider = gesture.get_widget()

        # Calculate click time using the same GtkGizmo approach for consistency
        click_time = self.get_slider_value_at_position(slider, x)

        # Snap to frame boundaries
        if hasattr(self, "video_fps") and self.video_fps > 0:
            frame_time = 1.0 / self.video_fps
            frame = round(click_time / frame_time)
            click_time = frame * frame_time

        # Set position - this will trigger on_position_changed
        slider.set_value(click_time)

    def on_adjustment_motion(self, controller, x, y):
        """Show tooltip for adjustment sliders using direct GtkGizmo handling"""
        slider = controller.get_widget()

        if slider is None or not isinstance(slider, Gtk.Widget):
            return

        # Check if we have a tooltip for this slider
        if slider not in self.adjustment_tooltips:
            return

        try:
            tooltip_data = self.adjustment_tooltips[slider]
            tooltip_popover = tooltip_data["popover"]
            tooltip_label = tooltip_data["label"]

            # Get the hover value directly using the GtkGizmo approach
            hover_value = self.get_slider_value_at_position(slider, x)

            # Format and display tooltip
            if hasattr(slider, "format_func") and callable(slider.format_func):
                tooltip_text = slider.format_func(hover_value)
            else:
                tooltip_text = f"{hover_value:.1f}"

            tooltip_label.set_text(tooltip_text)

            # Position tooltip above cursor
            rect = Gdk.Rectangle()
            rect.x = x
            rect.y = 0
            rect.width = 1
            rect.height = 1

            # Ensure proper parent for the popover
            if tooltip_popover.get_parent() != slider:
                tooltip_popover.set_parent(slider)

            tooltip_popover.set_pointing_to(rect)

            # Only show if slider is mapped and visible
            if slider.get_mapped() and slider.get_visible():
                tooltip_popover.popup()

        except Exception as e:
            print(f"Error showing adjustment tooltip: {e}")

    def on_adjustment_leave(self, controller, x=None, y=None):
        """Hide tooltip when mouse leaves slider"""
        slider = controller.get_widget()
        if slider and slider in self.adjustment_tooltips:
            try:
                self.adjustment_tooltips[slider]["popover"].popdown()
            except Exception as e:
                print(f"Error hiding adjustment tooltip: {e}")

    def on_button_enter(self, controller, x, y):
        """Show tooltip when mouse enters a button"""
        button = controller.get_widget()
        if button is None or not isinstance(button, Gtk.Widget):
            return

        if button in self.button_tooltips:
            tooltip_data = self.button_tooltips[button]
            try:
                # Get popover
                popover = tooltip_data["popover"]

                # Set parent if needed
                if popover.get_parent() is None:
                    popover.set_parent(button)

                # Create a rectangle at the center top of the button
                width = button.get_width()
                rect = Gdk.Rectangle()
                rect.x = width // 2  # Center horizontally
                rect.y = 0  # Top of button
                rect.width = 1
                rect.height = 1

                popover.set_pointing_to(rect)

                # Show popover
                if button.get_mapped() and button.get_visible():
                    popover.popup()
            except Exception as e:
                print(f"Error showing button tooltip: {e}")

    def on_button_leave(self, controller, x=None, y=None):
        """Hide tooltip when mouse leaves a button"""
        button = controller.get_widget()
        if button and button in self.button_tooltips:
            try:
                self.button_tooltips[button]["popover"].popdown()
            except Exception as e:
                print(f"Error hiding button tooltip: {e}")

    def on_slider_motion(self, controller, x, y):
        """Handle mouse motion over position slider"""
        # Store the hover position for tooltips
        slider = controller.get_widget()
        if slider is None or not isinstance(slider, Gtk.Widget):
            return

        adjustment = slider.get_adjustment()

        # Calculate value at mouse position
        width = slider.get_width()
        if width <= 0:
            return

        min_val = adjustment.get_lower()
        max_val = adjustment.get_upper()

        # Handle RTL layouts
        if slider.get_direction() == Gtk.TextDirection.RTL:
            pos = width - x
        else:
            pos = x

        self.hover_position = min_val + (pos / width) * (max_val - min_val)

        # Show tooltip with time at hover position
        if hasattr(self.ui, "tooltip_popover") and hasattr(self.ui, "tooltip_label"):
            try:
                # Format time for display
                time_str = self.format_time_precise(self.hover_position)
                self.ui.tooltip_label.set_text(time_str)

                # Get the popover
                popover = self.ui.tooltip_popover

                # Set parent if needed
                if popover.get_parent() is None:
                    popover.set_parent(slider)

                # Create a rectangle pointing to mouse X position but at top of slider
                rect = Gdk.Rectangle()
                rect.x = int(x)  # X position of mouse
                rect.y = 0  # Top of slider
                rect.width = 1
                rect.height = 1

                popover.set_pointing_to(rect)

                # Show popover if slider is visible
                if slider.get_mapped() and slider.get_visible():
                    popover.popup()
            except Exception as e:
                print(f"Error showing slider tooltip: {e}")

    def on_slider_leave(self, controller, x=None, y=None):
        """Handle mouse leaving position slider"""
        if hasattr(self.ui, "tooltip_popover"):
            try:
                self.ui.tooltip_popover.popdown()
            except Exception as e:
                print(f"Error hiding slider tooltip: {e}")

    def update_position_display(self, position):
        """Update the position display with current time and total time"""
        if self.video_duration > 0:
            time_str = self.format_time_precise(position)
            duration_str = self.format_time_precise(self.video_duration)
            self.ui.position_label.set_text(f"{time_str} / {duration_str}")

    def update_frame_counter(self, position):
        """Update the frame counter display"""
        if (
            self.video_duration > 0
            and hasattr(self, "video_fps")
            and self.video_fps > 0
        ):
            # Calculate current frame number
            current_frame = int(position * self.video_fps)

            # Calculate total frames
            total_frames = int(self.video_duration * self.video_fps)

            # Update the frame counter label
            self.ui.frame_label.set_text(f"Frame: {current_frame}/{total_frames}")

    def on_position_changed(self, scale):
        """Handle position slider changes"""
        position = scale.get_value()

        # Don't update if it's the same position
        if abs(position - self.current_position) < 0.001:
            return

        # Update current position
        self.current_position = position

        # Extract frame at new position
        self.processor.extract_frame(position)

    def seek_relative(self, offset):
        """Seek by a relative amount of seconds from current position"""
        new_position = self.current_position + offset

        # Clamp to valid range
        new_position = max(0, min(new_position, self.video_duration))

        # Update UI
        self.ui.position_scale.set_value(new_position)

        # Current position will be updated by the slider's value-changed handler

    def on_reset_all_settings(self, button):
        """Reset all video adjustment settings to defaults"""
        # Reset all values through the adjustment manager
        self.adjustment_manager.reset_all_values()

        # Reset trim points
        self.start_time = 0
        self.end_time = None

        # Save reset trim values to settings
        self.settings.save_setting("video-trim-start", 0.0)
        self.settings.save_setting("video-trim-end", -1.0)

        self.update_trim_display()

        # We don't need to update UI - already done by adjustment_manager
        # Just extract a new frame with reset values
        self.invalidate_current_frame_cache()
        self.processor.extract_frame(self.current_position)

    def reset_crop_values(self):
        """Reset crop values to 0 and update settings"""
        # Reset crop values to zero
        self.settings.save_setting("preview-crop-left", 0)
        self.settings.save_setting("preview-crop-right", 0)
        self.settings.save_setting("preview-crop-top", 0)
        self.settings.save_setting("preview-crop-bottom", 0)

        # Reset trim values while we're at it
        self.settings.save_setting("video-trim-start", 0.0)
        self.settings.save_setting("video-trim-end", -1.0)

        print("Crop and trim values have been reset on program start")
