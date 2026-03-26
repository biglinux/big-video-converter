#!/usr/bin/env python3
"""
Main entry point for Big Video Converter application.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# gi.require_version("Vte", "3.91")

import gettext
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

# Import local modules
from constants import APP_ID
from ui.conversion_page import ConversionPage
from ui.dependency_dialog import InstallDependencyDialog
from ui.header_bar import HeaderBar
from ui.progress_page import ProgressPage
from ui.video_edit_page import VideoEditPage
from ui.welcome_dialog import WelcomeDialog
from utils.dependency_checker import DependencyChecker
from utils.settings_manager import SettingsManager
from utils.tooltip_helper import TooltipHelper

# Mixins (extracted from this file to reduce god-object size)
from audio_settings import AudioSettingsMixin
from file_handler import FileHandlerMixin
from profile_manager import ProfileManagerMixin
from queue_manager import QueueManagerMixin
from sidebar_builder import SidebarBuilderMixin

_ = gettext.gettext


class VideoConverterApp(
    AudioSettingsMixin,
    ProfileManagerMixin,
    QueueManagerMixin,
    FileHandlerMixin,
    SidebarBuilderMixin,
    Adw.Application,
):
    def _window_buttons_on_left(self):
        """Detect if window buttons (close/min/max) are on the left side."""
        try:
            settings = Gio.Settings.new("org.gnome.desktop.wm.preferences")
            layout = settings.get_string("button-layout")
            if layout and ":" in layout:
                left, right = layout.split(":", 1)
                # Check for 'close' on the left side
                if "close" in left:
                    return True
                # Check for 'close' on the right side
                if "close" in right:
                    return False
            elif layout:
                # If no colon, treat as right side (default GNOME)
                if "close" in layout:
                    return False
        except Exception:
            # Logger not initialized yet, use print fallback or ignore
            pass
        # Default: right side
        return False

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
        self.dependency_checker = DependencyChecker()
        self.last_accessed_directory = self.settings_manager.load_setting(
            "last-accessed-directory", os.path.expanduser("~")
        )

        # Initialize tooltip helper
        self.tooltip_helper = TooltipHelper(self.settings_manager)

        # Initialize logger
        self.logger = logging.getLogger(__name__)

        # Make sure that the selected format is one of the available options
        # Indices: 0=MP4, 1=MKV, 2=MOV, 3=WebM
        current_format = self.settings_manager.load_setting("output-format-index", 0)
        if current_format > 3:
            self.settings_manager.save_setting("output-format-index", 0)  # Reset to MP4

        # Initialize state variables
        self.conversions_running = 0
        self.progress_widgets = []
        self.previous_page = "conversion"
        self.conversion_queue = deque()
        # Track active conversions for parallel processing
        self.active_conversions = []  # List of dictionaries with conversion info
        self.gpu_slots = deque()  # Queue of available GPU slots for parallel processing
        self.auto_convert = False
        self.queue_display_widgets = []
        self.is_cancellation_requested = False

        # Track completed conversions for completion screen
        self.completed_conversions = []

        # Thread safety locks
        self.conversions_lock = threading.Lock()
        self.completion_lock = threading.Lock()

        # Initialize missing attributes
        self._was_queue_processing = False
        self._processing_completion = False
        self.is_minimized = False

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
            "welcome": self.on_welcome_action,
            "quit": lambda a, p: self.quit(),
            "add_files": lambda a, p: self.select_files_for_queue(),
            "add_folder": lambda a, p: self.select_folder_for_queue(),
            "add_network_file": lambda a, p: self.show_network_file_dialog(),
            "start_conversion": lambda a, p: self.start_queue_processing(),
            "clear_queue": lambda a, p: self.clear_queue(),
            "restore_settings": lambda a, p: self._on_restore_settings(),
        }

        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        # Stateful toggle for tooltips (used in hamburger menu)
        show_tooltips = self.settings_manager.load_setting("show-tooltips", True)
        self.tooltip_action = Gio.SimpleAction.new_stateful(
            "toggle-tooltips",
            None,
            GLib.Variant.new_boolean(show_tooltips),
        )
        self.tooltip_action.connect("activate", self._on_tooltip_action_activated)
        self.add_action(self.tooltip_action)

        # Keyboard shortcuts
        self.set_accels_for_action("app.add_files", ["<Control>o"])
        self.set_accels_for_action("app.add_folder", ["<Control><Shift>o"])
        self.set_accels_for_action("app.start_conversion", ["<Control>Return"])
        self.set_accels_for_action("app.clear_queue", ["<Control>Delete"])
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _setup_icon_theme(self):
        """Setup custom icon theme path for bundled icons with PRIORITY"""
        try:
            # Get the application's directory
            script_dir = os.path.dirname(os.path.abspath(__file__))
            icons_dir = os.path.join(script_dir, "icons")

            # Check if icons directory exists
            if os.path.exists(icons_dir):
                # Get default icon theme
                icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())

                # Get current search paths
                current_paths = icon_theme.get_search_path()

                # Prepend our icons directory to ensure priority
                # This guarantees our icons are found FIRST
                new_paths = [icons_dir] + current_paths
                icon_theme.set_search_path(new_paths)

                if os.environ.get("BVC_DEBUG"):
                    self.logger.debug(
                        f"Custom icon theme path added with PRIORITY: {icons_dir}"
                    )
                    self.logger.debug(
                        f"Search paths order: {new_paths[:3]}..."
                    )  # Show first 3

        except OSError as e:
            if os.environ.get("BVC_DEBUG"):
                self.logger.error(
                    f"Error setting up icon theme: {type(e).__name__}: {e}"
                )

    def on_activate(self, app) -> None:
        # Setup custom icon theme for bundled icons
        self._setup_icon_theme()

        # Check if this is the first activation
        is_first_activation = not hasattr(self, "window") or self.window is None

        # Create window if it doesn't exist
        if is_first_activation:
            self._create_window()

        # Present the window early so dialogs can be transient for it
        self.window.present()

        # --- FFmpeg Dependency Check ---
        if not self.dependency_checker.are_dependencies_available():
            self._show_dependency_install_dialog()
            # Don't proceed with normal activation until ffmpeg is handled
            return
        # --- End of Check ---

        # Reset trim settings on startup
        self.reset_trim_settings()

        # Process any queued files
        if hasattr(self, "queued_files") and self.queued_files:
            for file_path in self.queued_files:
                self.add_to_conversion_queue(file_path)
            self.queued_files = []

        # Show welcome dialog only on first activation
        if is_first_activation and WelcomeDialog.should_show_welcome(
            self.settings_manager
        ):
            GLib.idle_add(self._show_welcome_dialog_startup)

    def _show_welcome_dialog_startup(self):
        """Show welcome dialog on startup (called via idle_add)"""
        self.welcome_dialog = WelcomeDialog(self.window, self.settings_manager)
        self.welcome_dialog.present()
        return False  # Remove idle callback

    def _create_window(self):
        """Create the main application window and UI components"""
        # Create main window
        self.window = Adw.ApplicationWindow(application=self)

        # Set minimum window size to prevent controls from being cut off
        # Left sidebar (300px) + right content (620px) = 920px minimum width
        self.window.set_size_request(920, 600)

        # Restore window size from settings
        width = self.settings_manager.load_setting("window-width", 1200)
        height = self.settings_manager.load_setting("window-height", 720)

        # Ensure default size is not smaller than minimum
        width = max(width, 920)
        height = max(height, 600)
        self.window.set_default_size(width, height)

        # Restore maximized state
        is_maximized = self.settings_manager.load_setting("window-maximized", False)
        if is_maximized:
            self.window.maximize()

        self.window.set_title("Big Video Converter")

        # Add close request handler to ensure processes are terminated and save window state
        self.window.connect("close-request", self._on_window_close_request)

        # Set application icon
        self.set_application_icon()

        # Setup drag and drop
        self._setup_drag_and_drop()

        # Create main content structure with ToastOverlay
        self.toast_overlay = Adw.ToastOverlay()

        # Create master ViewStack for main view and progress view
        self.main_stack = Adw.ViewStack()

        # Create horizontal paned layout for main view
        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_paned.set_vexpand(True)

        # Prevent panes from shrinking below their minimum size
        self.main_paned.set_shrink_start_child(False)
        self.main_paned.set_shrink_end_child(False)

        # Create CSS for sidebar styling
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(
            b"""
        .sidebar {
            background-color: @sidebar_bg_color;
        }
        .warning-banner {
            background-color: alpha(@warning_color, 0.25);
            color: @warning_color;
        }
        """,
            -1,
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Create left and right panes with ToolbarViews
        self._create_left_pane()
        self._create_right_pane()

        # Set initial paned position (restore from settings or use default 430px)
        sidebar_position = self.settings_manager.load_setting("sidebar-position", 430)
        self.main_paned.set_position(sidebar_position)

        # Add main_paned as first page of main_stack
        self.main_stack.add_titled(self.main_paned, "main_view", _("Main"))

        self.toast_overlay.set_child(self.main_stack)

        # Subtitle-only alert banner
        self.subtitle_banner = Adw.Banner()
        self.subtitle_banner.set_title(
            _("⚠ Subtitle extraction only mode is active — video will NOT be converted")
        )
        self.subtitle_banner.add_css_class("warning-banner")
        self.subtitle_banner.set_revealed(
            self.settings_manager.get_boolean("only-extract-subtitles", False)
        )

        # Wrap toast overlay + banner in a vertical box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(self.subtitle_banner)
        content_box.append(self.toast_overlay)
        self.toast_overlay.set_vexpand(True)
        self.window.set_content(content_box)

        # Create pages (including progress page)
        self._create_pages()

    def _on_window_close_request(self, window):
        """Handle window close event to clean up running processes"""
        # Save window state before closing
        self._save_window_state()

        # Check if we have active conversions
        if (
            hasattr(self, "progress_page")
            and self.progress_page
            and self.progress_page.has_active_conversions()
        ):
            # Terminate all running processes
            for (
                conversion_id,
                conversion,
            ) in self.progress_page.active_conversions.items():
                # Get the row (can be stored as "item" or "row" depending on implementation)
                conversion_item = conversion.get("row") or conversion.get("item")
                if conversion_item and conversion_item.process:
                    try:
                        self.logger.info(
                            f"Terminating process {conversion_item.process.pid} on application exit"
                        )
                        self.terminate_process_tree(conversion_item.process)
                    except Exception as e:
                        self.logger.error(f"Errorinating process on exit: {e}")

        # Continue with normal window close
        return False  # False means continue with close, True would prevent close

    def _save_window_state(self):
        """Save current window size, position and maximized state"""
        # Save maximized state
        is_maximized = self.window.is_maximized()
        self.settings_manager.save_setting("window-maximized", is_maximized)

        # Only save size if not maximized
        if not is_maximized:
            width = self.window.get_width()
            height = self.window.get_height()
            self.settings_manager.save_setting("window-width", width)
            self.settings_manager.save_setting("window-height", height)

        # Save sidebar position
        sidebar_position = self.main_paned.get_position()
        self.settings_manager.save_setting("sidebar-position", sidebar_position)

    def terminate_process_tree(self, process) -> bool:
        """Properly terminate a process and all its children using process groups"""
        if not process:
            return False

        pid = process.pid
        self.logger.info(f"Terminating process tree for PID {pid}")

        try:
            # First try: Kill the entire process group
            # This requires the process to have been started with start_new_session=True
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)

            # Wait briefly
            try:
                process.wait(timeout=0.5)
                self.logger.info(
                    f"Process {pid} terminated gracefully via process group"
                )
                return True
            except subprocess.TimeoutExpired:
                # Force kill if still running
                os.killpg(pgid, signal.SIGKILL)
                process.kill()
                self.logger.info(f"Process {pid} killed forcefully via process group")
                return True

        except ProcessLookupError:
            self.logger.info(f"Process {pid} already gone")
            return True
        except (subprocess.SubprocessError, OSError) as e:
            self.logger.warning(f"Error using process group termination: {e}")

            # Fallback to manual child cleanup
            try:
                # Kill any FFmpeg processes that might have been started by our process
                subprocess.run(
                    ["pkill", "-TERM", "-P", str(pid)],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )

                process.terminate()
                try:
                    process.wait(timeout=0.5)
                    self.logger.info(f"Process {pid} terminated via fallback")
                    return True
                except subprocess.TimeoutExpired:
                    process.kill()
                    self.logger.info(f"Process {pid} killed via fallback")
                    return True
            except (subprocess.SubprocessError, OSError) as e2:
                self.logger.error(f"Failed to terminate process {pid}: {e2}")
                return False

    def _create_right_pane(self):
        """Create right pane with stack for queue and editor views using ToolbarView"""
        # Create ToolbarView for right pane
        self.right_toolbar_view = Adw.ToolbarView()

        # Detect window button layout
        window_buttons_left = self._window_buttons_on_left()

        # Create HeaderBar for right pane
        self.header_bar = HeaderBar(self, window_buttons_left)
        self.right_toolbar_view.add_top_bar(self.header_bar)

        # Create ViewStack for queue and editor
        self.right_stack = Adw.ViewStack()
        self.right_stack.set_vexpand(True)
        self.right_stack.set_hexpand(True)

        # Connect to stack change signal
        self.right_stack.connect(
            "notify::visible-child-name", self.on_visible_child_changed
        )

        # The pages will be added here in _create_pages()
        # Queue view and editor view

        self.right_toolbar_view.set_content(self.right_stack)

        # Set minimum width for right content area
        self.right_toolbar_view.set_size_request(620, -1)

        self.main_paned.set_end_child(self.right_toolbar_view)

    def _create_pages(self):
        """Create and add all application pages"""
        # Initialize pages
        self.conversion_page = ConversionPage(self)

        # Create app_state wrapper for VideoEditPage
        class AppState:
            def __init__(self, conversion_page):
                self._conversion_page = conversion_page

            @property
            def file_metadata(self):
                return self._conversion_page.file_metadata

        self.app_state = AppState(self.conversion_page)
        self.video_edit_page = VideoEditPage(self, self.app_state)
        self.progress_page = ProgressPage(self)

        # Populate the sidebar now that video_edit_page is initialized
        self.video_edit_page.ui.populate_sidebar(self.editing_tools_box)

        # Add queue view to right stack
        self.right_stack.add_titled(
            self.conversion_page.get_page(), "queue_view", _("Queue")
        )

        # Add editor view to right stack
        self.right_stack.add_titled(
            self.video_edit_page.get_page(), "editor_view", _("Editor")
        )

        # Add progress page to main_stack
        self.main_stack.add_titled(
            self.progress_page.get_page(), "progress_view", _("Progress")
        )

    # ── Sidebar subtitle update methods ──

    def _apply_all_tooltips(self):
        """Apply tooltips to all UI elements"""
        if not hasattr(self, "tooltip_helper"):
            return

        # Apply tooltips to settings controls
        if hasattr(self, "gpu_combo"):
            self.tooltip_helper.add_tooltip(self.gpu_combo, "gpu")
        if hasattr(self, "video_quality_combo"):
            self.tooltip_helper.add_tooltip(self.video_quality_combo, "video_quality")
        if hasattr(self, "video_codec_combo"):
            self.tooltip_helper.add_tooltip(self.video_codec_combo, "video_codec")
        if hasattr(self, "audio_handling_combo"):
            self.tooltip_helper.add_tooltip(self.audio_handling_combo, "audio_handling")
        if hasattr(self, "subtitle_combo"):
            self.tooltip_helper.add_tooltip(self.subtitle_combo, "subtitles")
        if hasattr(self, "force_copy_video_check"):
            self.tooltip_helper.add_tooltip(self.force_copy_video_check, "force_copy")

        # Apply tooltips to video edit UI if it exists
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            if hasattr(self.video_edit_page, "ui") and self.video_edit_page.ui:
                self.video_edit_page.ui.apply_tooltips()

    def _on_tooltip_action_activated(self, action, _param):
        """Toggle the tooltip action state from the hamburger menu."""
        current = action.get_state().get_boolean()
        new_state = not current
        action.set_state(GLib.Variant.new_boolean(new_state))
        self._on_tooltips_toggle(new_state)

    def _on_tooltips_toggle(self, is_active):
        """Handle tooltip toggle change"""
        self.settings_manager.save_setting("show-tooltips", is_active)
        # Re-apply tooltips when re-enabled (they check is_enabled() before showing)
        if hasattr(self, "tooltip_helper") and is_active:
            self._apply_all_tooltips()

    # File handling methods

    def on_handle_local_options(self, app, options):
        """Handle command line parameters"""
        self.queued_files = []
        return -1  # Continue processing

    # Queue management

    # Conversion processing

    # UI Navigation
    def show_queue_view(self) -> None:
        """Show the file queue view"""
        self.right_stack.set_visible_child_name("queue_view")
        if hasattr(self.header_bar, "set_view"):
            self.header_bar.set_view("queue")
        if hasattr(self, "left_stack"):
            self.left_stack.set_visible_child_name("conversion_settings")
        if hasattr(self.video_edit_page, "cleanup"):
            self.video_edit_page.cleanup()
        if hasattr(self, "conversion_page"):
            self.conversion_page.update_queue_display()

    def show_editor_for_file(self, file_path: str) -> None:
        """The single, authoritative method to show the editor for a file."""
        if not file_path or not os.path.exists(file_path):
            self.show_error_dialog(_("File not found"))
            return

        self.logger.debug(f"Opening file in editor: {os.path.basename(file_path)}")

        self.right_stack.set_visible_child_name("editor_view")
        if hasattr(self.header_bar, "set_view"):
            self.header_bar.set_view("editor")
        if hasattr(self, "left_stack"):
            self.left_stack.set_visible_child_name("editing_tools")

        def load_video_action() -> None:
            if not self.video_edit_page.set_video(file_path):
                self.show_error_dialog(_("Could not load video file"))
                self.show_queue_view()

        GLib.idle_add(load_video_action)

    def show_progress_page(self) -> None:
        """Show progress page by switching to progress view"""
        if hasattr(self, "main_stack"):
            self.main_stack.set_visible_child_name("progress_view")
            self.logger.debug("Switched to progress view")

    def return_to_previous_page(self) -> None:
        """Return to queue view after conversion completes"""
        if hasattr(self, "main_stack"):
            current_view = self.main_stack.get_visible_child_name()
            if current_view == "progress_view":
                self.main_stack.set_visible_child_name("main_view")
        self.show_queue_view()

    def return_to_main_view(self) -> None:
        """Return to main view from progress view"""
        self.main_stack.set_visible_child_name("main_view")
        self.show_queue_view()
        if hasattr(self, "header_bar"):
            self.header_bar.set_buttons_sensitive(True)

    def on_visible_child_changed(self, stack, param) -> None:
        """Update UI when the visible right stack child changes"""
        visible_name = stack.get_visible_child_name()
        if visible_name != "editor_view":
            if hasattr(self.video_edit_page, "cleanup"):
                self.video_edit_page.cleanup()

    # Menu actions
    def on_about_action(self, action, param) -> None:
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

    def on_welcome_action(self, action, param) -> None:
        """Show the welcome dialog"""
        self.welcome_dialog = WelcomeDialog(self.window, self.settings_manager)
        self.welcome_dialog.present()

    def _on_restore_settings(self) -> None:
        """Delegate to settings_page reset (reuses existing implementation)."""
        self.settings_page._on_reset_button_clicked(None)

    # Application utilities
    def set_application_icon(self, icon_name=None) -> None:
        """Sets the application icon - improved for Wayland compatibility"""
        try:
            if icon_name:
                Gio.Application.set_application_icon(self, icon_name)

            if hasattr(self, "window"):
                self.window.set_icon_name("big-video-converter")

            GLib.set_prgname("big-video-converter")

            self.logger.debug("Application icon set successfully")
        except Exception as e:
            self.logger.error(f"Error setting application icon: {e}")
            try:
                if hasattr(self, "window"):
                    self.window.set_icon_name("video-x-generic")
                self.logger.debug("Using fallback icon")
            except Exception as e2:
                self.logger.error(f"Could not set fallback icon: {e2}")

    # Video editing parameters
    def get_trim_times(self):
        """Get the current trim start and end times"""
        start_time = self.settings_manager.load_setting("video-trim-start", 0.0)
        end_time_setting = self.settings_manager.load_setting("video-trim-end", -1.0)
        end_time = None if end_time_setting < 0 else end_time_setting

        self.logger.debug(f"get_trim_times: start={start_time}, end={end_time}")
        return start_time, end_time, self.video_duration

    def reset_trim_settings(self) -> None:
        """Reset trim settings in the app and in the settings storage"""
        self.trim_start_time = 0
        self.trim_end_time = None
        self.video_duration = 0

        self.settings_manager.save_setting("video-trim-start", 0.0)
        self.settings_manager.save_setting("video-trim-end", -1.0)

        if hasattr(self, "video_edit_page") and self.video_edit_page:
            if hasattr(self.video_edit_page, "trim_segments"):
                self.video_edit_page.trim_segments = []
                self.video_edit_page._save_file_metadata()
                if hasattr(self.video_edit_page, "_update_segments_listbox"):
                    self.video_edit_page._update_segments_listbox()

        self.logger.debug("Trim settings have been reset")

    def get_selected_format_extension(self):
        """Return the extension of the selected file format"""
        format_index = self.settings_manager.load_setting("output-format-index", 0)
        format_extensions = {0: ".mp4", 1: ".mkv", 2: ".mov", 3: ".webm"}
        return format_extensions.get(format_index, ".mp4")

    def get_selected_format_name(self):
        """Return the format name without the leading dot"""
        extension = self.get_selected_format_extension()
        return extension.lstrip(".")

    # Dialog helpers
    def show_error_dialog(self, message: str) -> None:
        """Shows an error dialog"""
        dialog = Gtk.AlertDialog()
        dialog.set_message(_("Error"))
        dialog.set_detail(message)
        dialog.show(self.window)

    def show_info_dialog(self, title: str, message: str) -> None:
        """Shows an information dialog"""
        dialog = Gtk.AlertDialog()
        dialog.set_message(title)
        dialog.set_detail(message)
        dialog.show(self.window)

    def send_system_notification(self, title: str, body) -> None:
        """Send a system notification"""
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.send_notification(None, notification)

    def show_completion_screen(self) -> None:
        """Show the completion summary on the progress page"""
        if hasattr(self, "progress_page"):
            self.progress_page.show_completion_summary()

        # Send final system notification
        count = len(self.completed_conversions) if self.completed_conversions else 0
        if count == 1:
            self.send_system_notification(
                _("All Conversions Complete"),
                _("1 video has been converted successfully!"),
            )
        elif count > 1:
            self.send_system_notification(
                _("All Conversions Complete"),
                _("{0} videos have been converted successfully!").format(count),
            )

    # GIO Application overrides
    def _present_window_and_request_focus(self, window):
        """Present the window and use a modal dialog hack to request focus if needed."""
        window.present()

        def check_and_apply_hack():
            if not window.is_active():
                self.logger.info(
                    "Window not active after present(), applying modal window hack."
                )
                hack_window = Gtk.Window(transient_for=window, modal=True)

                hack_window.set_default_size(1, 1)
                hack_window.set_decorated(False)

                hack_window.present()
                GLib.idle_add(hack_window.destroy)

            return GLib.SOURCE_REMOVE

        GLib.idle_add(check_and_apply_hack)

    def do_open(self, files: str, n_files: str, hint) -> None:
        """Handle files opened via file association or from another instance"""
        if not hasattr(self, "window") or self.window is None:
            self.activate()

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

        if files_added > 0:
            GLib.idle_add(self.show_queue_view)
            # Request window focus when files are added externally
            if hasattr(self, "window") and self.window:
                self._present_window_and_request_focus(self.window)

    def do_command_line(self, command_line):
        """Handle command line arguments"""
        args = command_line.get_arguments()

        if len(args) > 1:
            files = [
                Gio.File.new_for_path(arg) for arg in args[1:] if os.path.isfile(arg)
            ]
            if files:
                self.do_open(files, len(files), "")

        self.activate()
        return 0

    # File selection methods

    def _show_dependency_install_dialog(self):
        """Shows the dialog to install required dependencies (FFmpeg and MPV)."""
        install_info = self.dependency_checker.get_install_command()
        if not install_info:
            self.show_error_dialog(
                _("Dependencies Not Found"),
                _(
                    "FFmpeg and/or MPV are not installed and we could not determine how to install them for your system.\nPlease install them manually using your distribution's package manager."
                ),
            )
            # Disable the main window as the app is not usable
            self.window.set_sensitive(False)
            return

        dialog = InstallDependencyDialog(self.window, install_info)

        def on_dialog_close(widget) -> None:
            if dialog.installation_success:
                # Re-check for dependencies
                self.dependency_checker = DependencyChecker()
                if self.dependency_checker.are_dependencies_available():
                    self.show_info_dialog(
                        _("Installation Successful"),
                        _(
                            "Dependencies have been installed. The application will now restart."
                        ),
                    )
                    # Restart the application
                    self.quit()
                    subprocess.Popen([sys.executable] + sys.argv)
                else:
                    self.show_error_dialog(
                        _("Installation Failed"),
                        _(
                            "Dependencies were not found after installation. Please restart the application manually."
                        ),
                    )
                    self.quit()
            else:
                # User cancelled or installation failed, quit the app
                self.quit()

        dialog.connect("close-request", on_dialog_close)

        # Disable main window while this critical dialog is open
        self.window.set_sensitive(False)
        dialog.present()


def main():
    locale_dir = "/usr/share/locale"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    appimage_locale = os.path.join(os.path.dirname(script_dir), "locale")
    if os.path.isdir(appimage_locale):
        locale_dir = appimage_locale

    gettext.bindtextdomain("big-video-converter", locale_dir)
    gettext.textdomain("big-video-converter")

    # Refresh translated constants after gettext is initialized
    from constants import refresh_translations

    refresh_translations()

    # Configure logging
    log_level = logging.DEBUG if os.environ.get("BVC_DEBUG") else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    app = VideoConverterApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    main()
