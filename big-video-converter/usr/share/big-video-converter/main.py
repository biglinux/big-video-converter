#!/usr/bin/env python3
"""
Main entry point for Big Video Converter application.
"""

import os
import subprocess
import sys
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Setup translation
import gettext

# Import local modules
from constants import APP_ID, VIDEO_FILE_MIME_TYPES
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from ui.conversion_page import ConversionPage
from ui.header_bar import HeaderBar
from ui.progress_page import ProgressPage
from ui.settings_page import SettingsPage
from ui.video_edit_page import VideoEditPage
from utils.settings_manager import SettingsManager

_ = gettext.gettext


class VideoConverterApp(Adw.Application):
    def __init__(self):
        # Initialize with proper single-instance flags
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN
            | Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )

        # No need to call with parameter in constructor - will be called later
        self.set_resource_base_path("/org/communitybig/converter")
        GLib.set_prgname("big-video-converter")

        # Connect signals
        self.connect("activate", self.on_activate)
        self.connect("handle-local-options", self.on_handle_local_options)

        # Initialize settings
        self.settings_manager = SettingsManager(APP_ID)
        self.last_accessed_directory = self.settings_manager.load_setting(
            "last-accessed-directory", os.path.expanduser("~")
        )

        # Make sure that the selected format is one of the available options
        current_format = self.settings_manager.load_setting("output-format-index", 0)
        if current_format > 1:  # We now only have indices 0 and 1
            self.settings_manager.save_setting("output-format-index", 0)  # Reset to MP4

        # Initialize state variables
        self.conversions_running = 0
        self.progress_widgets = []
        self.previous_page = "conversion"
        self.conversion_queue = deque()
        self.currently_converting = False
        self.auto_convert = False
        self.queue_display_widgets = []

        # Video editing state - initialize with reset values
        self.trim_start_time = 0
        self.trim_end_time = None
        self.video_duration = 0
        self.crop_x = self.crop_y = self.crop_width = self.crop_height = 0
        self.crop_enabled = False

        # Reset trim settings in the actual settings storage
        self.reset_trim_settings()

        # Add a tracking variable to prevent double loading during previews
        self.previewing_specific_file = False
        self.preview_file_path = None

        # Setup application actions
        self._setup_actions()

    def _setup_actions(self):
        """Setup application actions for the menu"""
        actions = {
            "about": self.on_about_action,
            "help": self.on_help_action,
            "quit": lambda a, p: self.quit(),
            "add_files": lambda a, p: self.select_files_for_queue(),
            "add_folder": lambda a, p: self.select_folder_for_queue(),
        }

        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def on_activate(self, app):
        # Create window if it doesn't exist
        if not hasattr(self, "window") or self.window is None:
            self._create_window()

        # Reset trim settings on startup
        self.reset_trim_settings()

        # Present window and process any queued files
        self.window.present()

        if hasattr(self, "queued_files") and self.queued_files:
            for file_path in self.queued_files:
                self.add_to_conversion_queue(file_path)
            self.queued_files = []

    def _create_window(self):
        """Create the main application window and UI components"""
        # Create main window
        self.window = Adw.ApplicationWindow(application=self)
        self.window.set_default_size(900, 620)
        self.window.set_title("Big Video Converter")

        # Add close request handler to ensure processes are terminated
        self.window.connect("close-request", self._on_window_close_request)

        # Set application icon
        self.set_application_icon()

        # Setup drag and drop
        self._setup_drag_and_drop()

        # Create main content structure with ToastOverlay
        self.toast_overlay = Adw.ToastOverlay()
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.header_bar = HeaderBar(self)
        main_box.append(self.header_bar)

        # Create and add stack
        self.stack = Adw.ViewStack()
        self.stack.set_vexpand(True)
        main_box.append(self.stack)

        self.toast_overlay.set_child(main_box)
        self.window.set_content(self.toast_overlay)

        # Create pages
        self._create_pages()

        # Connect stack signals
        self.stack.connect("notify::visible-child", self.on_visible_child_changed)

    def _on_window_close_request(self, window):
        """Handle window close event to clean up running processes"""
        # Check if we have active conversions
        if self.progress_page and self.progress_page.has_active_conversions():
            # Terminate all running processes
            for (
                conversion_id,
                conversion,
            ) in self.progress_page.active_conversions.items():
                conversion_item = conversion["item"]
                if conversion_item and conversion_item.process:
                    try:
                        print(
                            f"Terminating process {conversion_item.process.pid} on application exit"
                        )
                        self.terminate_process_tree(conversion_item.process)
                    except Exception as e:
                        print(f"Error terminating process on exit: {e}")

        # Continue with normal window close
        return False  # False means continue with close, True would prevent close

    def terminate_process_tree(self, process):
        """Properly terminate a process and all its children"""
        if not process:
            return

        try:
            pid = process.pid
            print(f"Terminating process tree for PID {pid}")

            # First try: Use more reliable signal handling through pkill
            try:
                # Kill any FFmpeg processes that might have been started by our process
                # This helps catch alternative commands or FFmpeg processes that might be orphaned
                subprocess.run(
                    ["pkill", "-TERM", "-P", str(pid)],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                )

                # Also attempt to kill any FFmpeg processes by name, but only those started by our app
                # This is safer than killing every FFmpeg process system-wide
                try:
                    subprocess.run(
                        ["pgrep", "-P", str(pid), "ffmpeg"],
                        stderr=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        text=True,
                        check=False,
                    )
                except Exception as e:
                    print(f"Error finding ffmpeg processes: {e}")

                # Terminate the parent process
                process.terminate()

                # Wait briefly for termination
                try:
                    process.wait(timeout=0.5)
                    print(f"Process {pid} terminated gracefully")
                    return True
                except subprocess.TimeoutExpired:
                    # If still running, use SIGKILL on the process group
                    subprocess.run(
                        ["pkill", "-KILL", "-P", str(pid)],
                        stderr=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                    )

                    # Kill the parent process
                    process.kill()

                    # Wait briefly to verify termination
                    try:
                        process.wait(timeout=0.5)
                        print(f"Process {pid} killed forcefully")
                        return True
                    except subprocess.TimeoutExpired:
                        print(f"Warning: Process {pid} still running after SIGKILL")
                        return False

            except Exception as e:
                # Fallback to direct process termination
                print(f"Error using pkill, trying direct process termination: {e}")

                # Try to find and kill FFmpeg processes that might be associated with this conversion
                try:
                    # Find potential child processes more safely using ps
                    ps_output = subprocess.check_output(
                        ["ps", "-o", "pid", "--ppid", str(pid)],
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )

                    # Parse the output to get child PIDs
                    child_pids = []
                    for line in ps_output.strip().split("\n")[1:]:  # Skip header
                        if line.strip().isdigit():
                            child_pids.append(int(line.strip()))

                    # Kill each child process
                    for child_pid in child_pids:
                        try:
                            os.kill(child_pid, 15)  # SIGTERM
                            print(f"Sent SIGTERM to child process {child_pid}")
                        except ProcessLookupError:
                            pass  # Process already gone
                        except Exception as e:
                            print(f"Error killing child {child_pid}: {e}")

                except Exception as e:
                    print(f"Error finding child processes: {e}")

                # Terminate the main process
                process.terminate()

                try:
                    process.wait(timeout=0.5)
                    return True
                except subprocess.TimeoutExpired:
                    process.kill()

                    try:
                        process.wait(timeout=0.5)
                        return True
                    except subprocess.TimeoutExpired:
                        print(f"Warning: Process {pid} could not be killed")
                        return False
        except Exception as e:
            print(f"Error terminating process tree: {e}")
            return False

    def _create_pages(self):
        """Create and add all application pages"""
        # Initialize pages
        self.conversion_page = ConversionPage(self)
        self.video_edit_page = VideoEditPage(self)
        self.settings_page = SettingsPage(self)
        self.progress_page = ProgressPage(self)

        # Add pages to stack
        pages = [
            ("conversion", _("Conversion"), self.conversion_page),
            ("edit", _("Video Edit"), self.video_edit_page),
            ("settings", _("Settings"), self.settings_page),
            ("progress", _("Progress"), self.progress_page),
        ]

        for id, title, page in pages:
            self.stack.add_titled(page.get_page(), id, title)

    def _setup_drag_and_drop(self):
        """Set up drag and drop support for the window"""
        # Handle single files
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("drop", self.on_drop_file)
        self.window.add_controller(drop_target)

        # Handle multiple files
        filelist_drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        filelist_drop_target.connect("drop", self.on_drop_filelist)
        self.window.add_controller(filelist_drop_target)

    # File handling methods
    def is_valid_video_file(self, file_path):
        """Check if file has a valid video extension"""
        if not file_path:
            return False

        valid_extensions = [
            ".mp4",
            ".mkv",
            ".webm",
            ".mov",
            ".avi",
            ".wmv",
            ".mpeg",
            ".m4v",
            ".ts",
            ".flv",
        ]

        ext = os.path.splitext(file_path)[1].lower()
        return ext in valid_extensions

    def process_path_recursively(self, path):
        """Process a path (file or folder) recursively adding all valid video files to the queue"""
        if not os.path.exists(path):
            print(f"Path does not exist: {path}")
            return 0

        files_added = 0

        if os.path.isfile(path):
            # If it's a single file, just add it if valid
            if self.is_valid_video_file(path):
                if self.add_file_to_queue(path):
                    files_added += 1
        elif os.path.isdir(path):
            # If it's a directory, walk through it recursively
            print(f"Processing directory recursively: {path}")
            for root, dirs, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(root, file)
                    if self.is_valid_video_file(file_path):
                        if self.add_file_to_queue(file_path):
                            files_added += 1

        return files_added

    def on_drop_file(self, drop_target, value, x, y):
        """Handle single dropped file or folder"""
        if isinstance(value, Gio.File):
            file_path = value.get_path()
            if file_path and os.path.exists(file_path):
                files_added = self.process_path_recursively(file_path)
                return files_added > 0
        return False

    def on_drop_filelist(self, drop_target, value, x, y):
        """Handle multiple dropped files or folders"""
        if isinstance(value, Gdk.FileList):
            files_added = 0
            for file in value.get_files():
                if (
                    file
                    and (file_path := file.get_path())
                    and os.path.exists(file_path)
                ):
                    files_added += self.process_path_recursively(file_path)
            return files_added > 0
        return False

    def on_handle_local_options(self, app, options):
        """Handle command line parameters"""
        self.queued_files = []
        return -1  # Continue processing

    # Queue management
    def add_file_to_queue(self, file_path):
        """Add a file to the conversion queue"""
        if file_path and os.path.exists(file_path):
            # Update last accessed directory
            input_dir = os.path.dirname(file_path)
            self.last_accessed_directory = input_dir
            self.settings_manager.save_setting("last-accessed-directory", input_dir)

            # Add to queue if not already present
            if file_path not in self.conversion_queue:
                self.conversion_queue.append(file_path)
                print(
                    f"Added file to queue: {os.path.basename(file_path)}, Queue size: {len(self.conversion_queue)}"
                )

                # Update UI
                if hasattr(self, "conversion_page"):
                    GLib.idle_add(self.conversion_page.update_queue_display)
                return True
            else:
                print(f"File already in queue: {file_path}")
        return False

    def add_to_conversion_queue(self, file_path):
        """Add a file to the conversion queue without starting conversion"""
        return self.add_file_to_queue(file_path)

    def clear_queue(self):
        """Clear the conversion queue"""
        self.conversion_queue.clear()
        if hasattr(self, "conversion_page"):
            self.conversion_page.update_queue_display()
        print("Conversion queue cleared")

    def remove_from_queue(self, file_path):
        """Remove a specific file from the queue"""
        if file_path in self.conversion_queue:
            self.conversion_queue.remove(file_path)
            if hasattr(self, "conversion_page"):
                self.conversion_page.update_queue_display()
            print(f"Removed {os.path.basename(file_path)} from queue")
            return True
        return False

    # Conversion processing
    def start_queue_processing(self):
        """Start processing the conversion queue"""
        if not self.conversion_queue:
            return

        print("Starting queue processing")
        self._was_queue_processing = True
        self.header_bar.set_tabs_sensitive(False)

        # Reset conversion state and start processing
        self.currently_converting = False
        self.show_progress_page()
        GLib.timeout_add(300, self.process_next_in_queue)

    def process_next_in_queue(self):
        """Process the next file in the conversion queue"""
        # Check if we can proceed
        if not self.conversion_queue:
            print("Queue is empty, nothing to process")
            self.currently_converting = False
            return False

        if self.currently_converting:
            print("Already converting, not starting another conversion")
            return False

        # Start processing
        print("Processing next item in queue...")
        self.currently_converting = True

        # Get next file
        file_path = self.conversion_queue[0]
        self.current_processing_file = file_path
        print(f"Processing file: {os.path.basename(file_path)}")

        # Set file and start conversion
        if hasattr(self, "conversion_page"):
            self.conversion_page.set_file(file_path)
            GLib.timeout_add(300, self._force_start_conversion)

        return False  # Don't repeat

    def _force_start_conversion(self):
        """Helper to force start conversion with proper error handling"""
        try:
            print("Forcing conversion to start automatically...")
            if hasattr(self, "conversion_page"):
                self.conversion_page.force_start_conversion()
        except Exception as e:
            print(f"Error starting automatic conversion: {e}")
            self.currently_converting = False
        return False  # Don't repeat

    def conversion_completed(self, success):
        """Called when a conversion is completed"""
        print(f"Conversion completed with success={success}")
        self.currently_converting = False

        # Re-enable convert button
        if hasattr(self, "conversion_page"):
            GLib.idle_add(
                lambda: self.conversion_page.convert_button.set_sensitive(True)
            )

        # Handle the file that was just processed (success or fail)
        if self.conversion_queue and hasattr(self, "current_processing_file"):
            try:
                # Always remove the processed file from the queue to prevent loops
                self.conversion_queue.remove(self.current_processing_file)
                print(
                    f"Removed processed file from queue: {os.path.basename(self.current_processing_file)}"
                )
            except ValueError:
                print("File not found in queue, may have been removed already")

            self.current_processing_file = None

            # Update UI
            if hasattr(self, "conversion_page"):
                GLib.idle_add(self.conversion_page.update_queue_display)

        # Process next file or finish
        if self.conversion_queue:
            print(
                f"Queue has {len(self.conversion_queue)} file(s) remaining, processing next file"
            )
            GLib.timeout_add(500, self.process_next_in_queue)
        else:
            print("Queue is now empty")
            GLib.idle_add(self.header_bar.set_tabs_sensitive, True)

            # Show completion notification if we were processing a queue
            if hasattr(self, "_was_queue_processing") and self._was_queue_processing:
                GLib.idle_add(
                    lambda: self.show_info_dialog(
                        _("Queue Processing Complete"),
                        _("All files in the queue have been processed."),
                    )
                )
                self._was_queue_processing = False
                GLib.idle_add(self.return_to_previous_page)

    # UI Navigation
    def activate_tab(self, tab_name):
        """Switch to the specified tab and update button styling"""
        # Special handling for edit tab - need to load a video
        if tab_name == "edit" and self.conversion_page:
            # Check if we're already previewing a specific file
            if self.previewing_specific_file and self.preview_file_path:
                # We're already handling a preview request, just switch to the tab
                print(
                    f"Already previewing a specific file, skipping auto-load: {self.preview_file_path}"
                )
                pass  # Just switch to the tab below
            else:
                # Normal auto-select behavior
                file_path = self.conversion_page.get_selected_file_path()
                if not file_path:
                    self.show_error_dialog(_("Please select a video file first"))
                    return

                if not self.video_edit_page.set_video(file_path):
                    self.show_error_dialog(_("Could not load the selected video file"))
                    return

        # Remember previous page unless switching to progress
        if tab_name != "progress" and self.stack.get_visible_child_name() != "progress":
            self.previous_page = tab_name

        # Update UI
        self.stack.set_visible_child_name(tab_name)
        self.header_bar.activate_tab(tab_name)

        # Reset the preview tracking after the tab is switched
        if tab_name != "edit":
            self.previewing_specific_file = False
            self.preview_file_path = None

    def show_progress_page(self):
        """Show progress page and disable tab navigation"""
        self.header_bar.set_tabs_sensitive(False)
        self.stack.set_visible_child_name("progress")

    def return_to_previous_page(self):
        """Return to the previous page after conversion completes"""
        self.header_bar.set_tabs_sensitive(True)
        self.stack.set_visible_child_name(self.previous_page)
        self.header_bar.activate_tab(self.previous_page)

    def on_visible_child_changed(self, stack, param):
        """Update button styling when the visible stack child changes"""
        visible_name = stack.get_visible_child_name()
        self.header_bar.activate_tab(visible_name)

    # Menu actions
    def on_about_action(self, action, param):
        """Show about dialog"""
        from constants import APP_DEVELOPERS, APP_NAME, APP_VERSION

        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name=APP_NAME,
            application_icon="big-video-converter",
            version=APP_VERSION,
            developers=APP_DEVELOPERS,
            license_type=Gtk.License.GPL_3_0,
            website="https://www.biglinux.com.br",
        )
        about.present()

    def on_help_action(self, action, param):
        """Show help information"""
        self.show_info_dialog(
            _("Help"),
            _(
                "This application helps you convert video files between different formats.\n\n"
                "• Use the Conversion tab to select and convert video files\n"
                "• Use the Video Edit tab to trim, crop, and adjust video properties\n"
                "• Access settings through the gear icon\n\n"
                "For more help, visit the website: communitybig.org"
            ),
        )

    # Application utilities
    def set_application_icon(self, icon_name=None):
        """Sets the application icon - improved for Wayland compatibility"""
        try:
            # If icon name is provided, use it
            if icon_name:
                # Set on the application itself using Gio.Application method
                Gio.Application.set_application_icon(self, icon_name)

            # Always set for window if it exists
            if hasattr(self, "window"):
                self.window.set_icon_name("big-video-converter")

            # Ensure program name is correctly set
            GLib.set_prgname("big-video-converter")

            print("Application icon set successfully")
        except Exception as e:
            print(f"Error setting application icon: {e}")
            try:
                # Fallback to generic video icon
                if hasattr(self, "window"):
                    self.window.set_icon_name("video-x-generic")
                print("Using fallback icon")
            except Exception as e2:
                print(f"Could not set fallback icon: {e2}")

    # Video editing parameters
    def set_trim_times(self, start_time, end_time, duration):
        """Set the trim start and end times for video cutting"""
        self.trim_start_time = start_time
        self.trim_end_time = end_time
        self.video_duration = duration

        # Save trim times to settings for use by conversion
        self.settings_manager.save_setting("video-trim-start", start_time)
        # Use -1 to indicate no end time (full video)
        end_value = -1.0 if end_time is None else end_time
        self.settings_manager.save_setting("video-trim-end", end_value)

        print(f"Saved trim times to settings: start={start_time}, end={end_value}")

    def get_trim_times(self):
        """Get the current trim start and end times"""
        # Always use the latest values from settings to ensure consistency
        start_time = self.settings_manager.load_setting("video-trim-start", 0.0)
        end_time_setting = self.settings_manager.load_setting("video-trim-end", -1.0)
        end_time = None if end_time_setting < 0 else end_time_setting

        print(f"get_trim_times: start={start_time}, end={end_time}")
        return start_time, end_time, self.video_duration

    def set_crop_params(self, x, y, width, height, enabled=True):
        """Set the crop parameters for video cropping"""
        self.crop_x, self.crop_y = x, y
        self.crop_width, self.crop_height = width, height
        self.crop_enabled = enabled

    def get_crop_params(self):
        """Get the current crop parameters"""
        return {
            "x": self.crop_x,
            "y": self.crop_y,
            "width": self.crop_width,
            "height": self.crop_height,
            "enabled": self.crop_enabled,
        }

    def reset_crop_params(self):
        """Reset crop parameters"""
        self.crop_x = self.crop_y = self.crop_width = self.crop_height = 0
        self.crop_enabled = False

    def reset_trim_settings(self):
        """Reset trim settings in the app and in the settings storage"""
        # Reset in-memory values
        self.trim_start_time = 0
        self.trim_end_time = None
        self.video_duration = 0

        # Reset persistent settings
        self.settings_manager.save_setting("video-trim-start", 0.0)
        self.settings_manager.save_setting("video-trim-end", -1.0)

        # Notify any attached pages that trim settings have been reset
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            if hasattr(self.video_edit_page, "start_time"):
                self.video_edit_page.start_time = 0
            if hasattr(self.video_edit_page, "end_time"):
                self.video_edit_page.end_time = None
            if hasattr(self.video_edit_page, "update_trim_display"):
                self.video_edit_page.update_trim_display()

        print("Trim settings have been reset")

    def get_selected_format_extension(self):
        """Return the extension of the selected file format"""
        # Obter o valor mais recente da configuração
        format_index = self.settings_manager.load_setting("output-format-index", 0)

        # Debug para ver o valor sendo recuperado
        print(f"DEBUG: output-format-index = {format_index}")

        format_extensions = {
            0: ".mp4",  # MP4
            1: ".mkv",  # MKV
        }
        return format_extensions.get(format_index, ".mp4")  # Return .mp4 as default

    def get_selected_format_name(self):
        """Return the format name without the leading dot"""
        extension = self.get_selected_format_extension()
        return extension.lstrip(".")

    # Dialog helpers
    def show_error_dialog(self, message):
        """Shows an error dialog"""
        dialog = Gtk.AlertDialog()
        dialog.set_message(_("Error"))
        dialog.set_detail(message)
        dialog.show(self.window)

    def show_info_dialog(self, title, message):
        """Shows an information dialog"""
        dialog = Gtk.AlertDialog()
        dialog.set_message(title)
        dialog.set_detail(message)
        dialog.show(self.window)

    def show_question_dialog(self, title, message, callback):
        """Shows a question dialog"""
        dialog = Gtk.AlertDialog()
        dialog.set_message(title)
        dialog.set_detail(message)
        dialog.set_buttons(["Cancel", "Continue"])
        dialog.set_default_button(0)
        dialog.set_cancel_button(0)

        dialog.connect("response", lambda d, r: callback(r == 1))
        dialog.show(self.window)

    def show_toast(self, message):
        """Shows a toast notification."""
        if hasattr(self, "toast_overlay"):
            toast = Adw.Toast(title=message)
            self.toast_overlay.add_toast(toast)

    def show_file_details(self, file_path):
        """Preview a file in the editor"""
        if not file_path or not os.path.exists(file_path):
            print(f"Cannot preview - invalid file path: {file_path}")
            self.show_error_dialog(_("Could not preview this video file"))
            return False

        if self.video_edit_page:
            try:
                print(f"Attempting to preview file: {file_path}")

                # Set the preview tracking variables BEFORE any loading happens
                self.previewing_specific_file = True
                self.preview_file_path = file_path

                # Ensure no other file is being loaded at the same time
                if (
                    hasattr(self.video_edit_page, "loading_video")
                    and self.video_edit_page.loading_video
                ):
                    print("Another video is currently loading, will retry in 1 second")
                    # Schedule a retry after a short delay
                    GLib.timeout_add(
                        1000, lambda fp=file_path: self.show_file_details(fp)
                    )
                    return True

                # Try to set the video and verify success
                if self.video_edit_page.set_video(file_path):
                    # Switch to edit tab - slightly delayed to ensure UI updates
                    GLib.idle_add(lambda: self.activate_tab("edit"))
                    return True
                else:
                    print(f"Failed to set video for preview: {file_path}")
                    self.previewing_specific_file = False  # Reset on failure
                    self.preview_file_path = None
                    self.show_error_dialog(_("Could not preview this video file"))
                    return False
            except Exception as e:
                print(f"Error previewing file: {e}")
                import traceback

                traceback.print_exc()
                self.previewing_specific_file = False  # Reset on exception
                self.preview_file_path = None
                self.show_error_dialog(_("Error previewing file: {0}").format(str(e)))
                return False
        else:
            print("Video edit page not initialized")
            return False

    # GIO Application overrides
    def do_open(self, files, n_files, hint):
        """Handle files opened via file association or from another instance"""
        # Ensure window exists
        if not hasattr(self, "window") or self.window is None:
            self.activate()

        # Add files to queue
        files_added = 0
        for file in files:
            file_path = file.get_path()
            if (
                file_path
                and os.path.exists(file_path)
                and self.is_valid_video_file(file_path)
            ):
                if self.add_file_to_queue(file_path):
                    files_added += 1

        # Switch to conversion tab if files were added
        if files_added > 0:
            GLib.idle_add(self._activate_conversion_tab)

    def _activate_conversion_tab(self):
        """Activate conversion tab safely from idle callback"""
        if hasattr(self, "stack") and hasattr(self, "header_bar"):
            self.activate_tab("conversion")
        return False

    def do_command_line(self, command_line):
        """Handle command line arguments"""
        args = command_line.get_arguments()

        # Process file arguments
        if len(args) > 1:
            files = [
                Gio.File.new_for_path(arg) for arg in args[1:] if os.path.isfile(arg)
            ]
            if files:
                self.do_open(files, len(files), "")

        # Show window
        self.activate()
        return 0

    # File selection methods
    def select_files_for_queue(self):
        """Open file chooser to select video files for the queue"""
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Video Files"))
        dialog.set_modal(True)

        # Set initial folder to last accessed directory
        if hasattr(self, "last_accessed_directory") and self.last_accessed_directory:
            try:
                initial_folder = Gio.File.new_for_path(self.last_accessed_directory)
                dialog.set_initial_folder(initial_folder)
            except Exception as e:
                print(f"Error setting initial folder: {e}")

        # Create filter for video files
        filter = Gtk.FileFilter()
        filter.set_name(_("Video Files"))
        for mime_type in VIDEO_FILE_MIME_TYPES:
            filter.add_mime_type(mime_type)

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter)
        dialog.set_filters(filters)

        # Allow multiple file selection
        dialog.open_multiple(self.window, None, self._on_files_selected)

    def select_folder_for_queue(self):
        """Open folder chooser to select a folder with video files"""
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Folder with Video Files"))
        dialog.set_modal(True)

        # Try to set a filter that only shows directories
        folder_filter = Gtk.FileFilter()
        folder_filter.set_name(_("Folders"))
        folder_filter.add_mime_type("inode/directory")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(folder_filter)

        try:
            # This might not be supported on all GTK versions
            dialog.set_filters(filters)
        except Exception as e:
            print(f"Could not set folder filter: {e}")

        # Set initial folder to last accessed directory
        if hasattr(self, "last_accessed_directory") and self.last_accessed_directory:
            try:
                initial_folder = Gio.File.new_for_path(self.last_accessed_directory)
                dialog.set_initial_folder(initial_folder)
            except Exception as e:
                print(f"Error setting initial folder: {e}")

        dialog.select_folder(self.window, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        """Handle selected folder from folder chooser dialog"""
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                folder_path = folder.get_path()
                if folder_path and os.path.exists(folder_path):
                    # Verificar explicitamente se é um diretório
                    if os.path.isdir(folder_path):
                        files_added = self.process_path_recursively(folder_path)

                        # Update last accessed directory
                        if files_added > 0:
                            self.last_accessed_directory = folder_path
                            self.settings_manager.save_setting(
                                "last-accessed-directory", self.last_accessed_directory
                            )

                            # Provide feedback about the number of files added
                            message = _(
                                "{} video files have been added to the queue."
                            ).format(files_added)
                            GLib.idle_add(
                                lambda msg=message: self.show_info_dialog(
                                    _("Files Added"), msg
                                )
                            )
                        elif files_added == 0:
                            # Não encontrou nenhum arquivo de vídeo válido
                            GLib.idle_add(
                                lambda: self.show_info_dialog(
                                    _("No Files Found"),
                                    _(
                                        "No valid video files were found in the selected folder."
                                    ),
                                )
                            )
                    else:
                        # Foi selecionado um arquivo, não uma pasta
                        GLib.idle_add(
                            lambda: self.show_error_dialog(
                                _("Please select a folder, not a file.")
                            )
                        )
        except Exception as e:
            print(f"Error selecting folder: {e}")
            error_msg = str(e)
            GLib.idle_add(
                lambda msg=error_msg: self.show_error_dialog(
                    _("Error selecting folder: {}").format(msg)
                )
            )

    def _on_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
            files_added = 0
            first_file = None

            if files:
                for file in files:
                    file_path = file.get_path()
                    if file_path:
                        if self.add_file_to_queue(file_path):
                            files_added += 1
                            if not first_file:
                                first_file = file_path

                # Show feedback message
                if files_added > 0:
                    # Update UI
                    self.conversion_page.update_queue_display()

                    # If in edit tab, load the first file into the editor
                    if (
                        first_file
                        and hasattr(self, "stack")
                        and self.stack.get_visible_child_name() == "edit"
                    ):
                        self.video_edit_page.set_video(first_file)
                else:
                    GLib.idle_add(
                        lambda: self.show_info_dialog(
                            _("No Files Added"),
                            _("No valid video files were found in your selection."),
                        )
                    )
        except Exception as error:
            print(f"Files not selected: {error}")


def main():
    # Set up internationalization
    gettext.bindtextdomain("big-video-converter")
    gettext.textdomain("big-video-converter")

    # Create and run application
    app = VideoConverterApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    main()
