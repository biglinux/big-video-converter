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
from constants import (
    APP_ID,
    AUDIO_VALUES,
    GPU_VALUES,
    SUBTITLE_VALUES,
    VIDEO_CODEC_VALUES,
    VIDEO_FILE_MIME_TYPES,
    VIDEO_QUALITY_VALUES,
)
from ui.conversion_page import ConversionPage
from ui.dependency_dialog import InstallDependencyDialog
from ui.header_bar import HeaderBar
from ui.progress_page import ProgressPage
from ui.settings_page import SettingsPage
from ui.video_edit_page import VideoEditPage
from ui.welcome_dialog import WelcomeDialog
from utils.dependency_checker import DependencyChecker
from utils.settings_manager import SettingsManager
from utils.tooltip_helper import TooltipHelper

_ = gettext.gettext


class VideoConverterApp(Adw.Application):
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
        current_format = self.settings_manager.load_setting("output-format-index", 0)
        if current_format > 1:  # We now only have indices 0 and 1
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
        }

        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

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

        except Exception as e:
            if os.environ.get("BVC_DEBUG"):
                self.logger.error(
                    f"Error setting up icon theme: {type(e).__name__}: {e}"
                )

    def on_activate(self, app):
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

    def terminate_process_tree(self, process):
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
        except Exception as e:
            self.logger.warning(f"Error using process group termination: {e}")

            # Fallback to manual child cleanup
            try:
                # Kill any FFmpeg processes that might have been started by our process
                subprocess.run(
                    ["pkill", "-TERM", "-P", str(pid)],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    check=False,
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
            except Exception as e2:
                self.logger.error(f"Failed to terminate process {pid}: {e2}")
                return False

    def _create_left_pane(self):
        """Create left settings pane with contextual ViewStack"""
        # Create ToolbarView for left pane to get the correct sidebar style
        left_toolbar_view = Adw.ToolbarView()
        left_toolbar_view.add_css_class("sidebar")

        # Detect window button layout
        window_buttons_left = self._window_buttons_on_left()

        # Create a HeaderBar for the left pane
        left_header = Adw.HeaderBar()
        left_header.add_css_class("sidebar")
        left_header.set_show_title(True)
        # Configure left header bar based on window button layout
        left_header.set_decoration_layout(
            "close,maximize,minimize:menu" if window_buttons_left else ""
        )

        # Create title box with label and (optionally) app icon
        if not window_buttons_left:
            # App icon on left if window buttons are on right, text truly centered
            center_box = Gtk.CenterBox()
            center_box.set_hexpand(True)
            app_icon = Gtk.Image.new_from_icon_name("big-video-converter")
            app_icon.set_pixel_size(20)
            app_icon.set_halign(Gtk.Align.START)
            app_icon.set_valign(Gtk.Align.START)
            # Do not expand icon
            app_icon.set_hexpand(False)
            center_box.set_start_widget(app_icon)
            title_label = Gtk.Label(label="Big Video Converter")
            title_label.set_halign(Gtk.Align.CENTER)
            title_label.set_valign(Gtk.Align.START)
            title_label.set_hexpand(True)
            center_box.set_center_widget(title_label)
            # No end widget
            left_header.set_title_widget(center_box)
        else:
            title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title_label = Gtk.Label(label="Big Video Converter")
            title_box.append(title_label)
            # Add an expanding box to push controls to the left
            expander = Gtk.Box()
            expander.set_hexpand(True)
            title_box.append(expander)
            left_header.set_title_widget(title_box)
        left_toolbar_view.add_top_bar(left_header)

        # Create scrolled window for content
        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroll.set_min_content_width(300)
        left_scroll.set_max_content_width(400)

        # Create ViewStack for contextual content
        self.left_stack = Adw.ViewStack()

        # Page 1: Conversion Settings
        conversion_settings = self._create_conversion_settings()
        self.left_stack.add_titled(
            conversion_settings, "conversion_settings", _("Conversion")
        )

        # Page 2: Editing Tools (This is now a container to be populated later)
        self.editing_tools_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        self.left_stack.add_titled(
            self.editing_tools_box, "editing_tools", _("Editing")
        )

        left_scroll.set_child(self.left_stack)
        left_toolbar_view.set_content(left_scroll)

        # Set minimum width for left sidebar
        left_toolbar_view.set_size_request(300, -1)

        self.main_paned.set_start_child(left_toolbar_view)

    def _create_conversion_settings(self):
        """Create conversion settings page for left sidebar"""
        from constants import (
            AUDIO_OPTIONS,
            GPU_OPTIONS,
            SUBTITLE_OPTIONS,
            VIDEO_CODEC_OPTIONS,
            VIDEO_QUALITY_OPTIONS,
        )

        # Main settings box
        settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        settings_box.set_spacing(24)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)
        settings_box.set_margin_top(12)
        settings_box.set_margin_bottom(24)

        # Encoding Settings Group
        encoding_group = Adw.PreferencesGroup()

        # GPU selection
        gpu_model = Gtk.StringList()
        for option in GPU_OPTIONS:
            gpu_model.append(option)
        self.gpu_combo = Adw.ComboRow(title=_("GPU"))
        self.gpu_combo.set_subtitle(_("Hardware acceleration"))
        self.gpu_combo.set_model(gpu_model)
        encoding_group.add(self.gpu_combo)
        self.tooltip_helper.add_tooltip(self.gpu_combo, "gpu")

        # GPU Device selection (visible only when multiple GPUs detected)
        self.detected_gpus = self._detect_gpu_devices()
        self.gpu_device_combo = Adw.ComboRow(title=_("GPU Device"))
        self.gpu_device_combo.set_subtitle(_("Select which GPU to use"))
        gpu_device_model = Gtk.StringList()
        gpu_device_model.append(_("Auto"))
        for gpu_info in self.detected_gpus:
            gpu_device_model.append(gpu_info["name"])
        self.gpu_device_combo.set_model(gpu_device_model)
        encoding_group.add(self.gpu_device_combo)
        # Only show when multiple GPUs are available
        self.gpu_device_combo.set_visible(len(self.detected_gpus) > 1)

        # Video Quality
        quality_model = Gtk.StringList()
        for option in VIDEO_QUALITY_OPTIONS:
            quality_model.append(option)
        self.video_quality_combo = Adw.ComboRow(title=_("Video Quality"))
        self.video_quality_combo.set_model(quality_model)
        encoding_group.add(self.video_quality_combo)
        self.tooltip_helper.add_tooltip(self.video_quality_combo, "video_quality")

        # Video Codec
        codec_model = Gtk.StringList()
        for option in VIDEO_CODEC_OPTIONS:
            codec_model.append(option)
        self.video_codec_combo = Adw.ComboRow(title=_("Video Codec"))
        self.video_codec_combo.set_model(codec_model)
        encoding_group.add(self.video_codec_combo)
        self.tooltip_helper.add_tooltip(self.video_codec_combo, "video_codec")

        settings_box.append(encoding_group)

        # Audio & Subtitles Group
        audio_group = Adw.PreferencesGroup()

        # Audio Handling
        audio_model = Gtk.StringList()
        for option in AUDIO_OPTIONS:
            audio_model.append(option)
        self.audio_handling_combo = Adw.ComboRow(title=_("Audio"))
        self.audio_handling_combo.set_model(audio_model)
        audio_group.add(self.audio_handling_combo)
        self.tooltip_helper.add_tooltip(self.audio_handling_combo, "audio_handling")

        # Subtitle Handling
        subtitle_model = Gtk.StringList()
        for option in SUBTITLE_OPTIONS:
            subtitle_model.append(option)
        self.subtitle_combo = Adw.ComboRow(title=_("Subtitles"))
        self.subtitle_combo.set_model(subtitle_model)
        audio_group.add(self.subtitle_combo)
        self.tooltip_helper.add_tooltip(self.subtitle_combo, "subtitles")

        # Audio Noise Reduction
        self.noise_reduction_switch = Adw.SwitchRow(title=_("Noise Reduction"))
        self.noise_reduction_switch.set_subtitle(_("Reduce background noise (GTCRN)"))
        audio_group.add(self.noise_reduction_switch)
        self.tooltip_helper.add_tooltip(self.noise_reduction_switch, "noise_reduction")

        # Noise Reduction Strength (visible only when noise reduction is enabled)
        self.noise_strength_spin = Adw.SpinRow.new_with_range(0.0, 1.0, 0.05)
        self.noise_strength_spin.set_title(_("Strength"))
        self.noise_strength_spin.set_digits(2)
        self.noise_strength_spin.set_value(1.0)
        self.noise_strength_spin.set_visible(False)
        audio_group.add(self.noise_strength_spin)
        self.tooltip_helper.add_tooltip(
            self.noise_strength_spin, "noise_reduction_strength"
        )

        # Noise Gate (visible only when noise reduction is enabled)
        self.gate_expander = Adw.ExpanderRow(title=_("Noise Gate"))
        self.gate_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.gate_switch.connect("state-set", self._on_gate_switch_changed)
        self.gate_expander.add_suffix(self.gate_switch)
        self.gate_expander.set_enable_expansion(False)
        self.gate_expander.set_expanded(False)
        self.gate_expander.set_visible(False)
        self.tooltip_helper.add_tooltip(self.gate_expander, "noise_gate")

        self.gate_threshold_spin = Adw.SpinRow.new_with_range(-60, 0, 1)
        self.gate_threshold_spin.set_title(_("Threshold (dB)"))
        self.gate_threshold_spin.set_value(-30)
        self.gate_threshold_spin.set_sensitive(False)
        self.gate_expander.add_row(self.gate_threshold_spin)
        self.tooltip_helper.add_tooltip(self.gate_threshold_spin, "gate_threshold")

        self.gate_range_spin = Adw.SpinRow.new_with_range(-90, 0, 1)
        self.gate_range_spin.set_title(_("Range (dB)"))
        self.gate_range_spin.set_value(-60)
        self.gate_range_spin.set_sensitive(False)
        self.gate_expander.add_row(self.gate_range_spin)
        self.tooltip_helper.add_tooltip(self.gate_range_spin, "gate_range")

        self.gate_attack_spin = Adw.SpinRow.new_with_range(0.1, 500, 1)
        self.gate_attack_spin.set_title(_("Attack (ms)"))
        self.gate_attack_spin.set_digits(1)
        self.gate_attack_spin.set_value(20.0)
        self.gate_attack_spin.set_sensitive(False)
        self.gate_expander.add_row(self.gate_attack_spin)
        self.tooltip_helper.add_tooltip(self.gate_attack_spin, "gate_attack")

        self.gate_release_spin = Adw.SpinRow.new_with_range(0, 1000, 5)
        self.gate_release_spin.set_title(_("Release (ms)"))
        self.gate_release_spin.set_digits(1)
        self.gate_release_spin.set_value(150.0)
        self.gate_release_spin.set_sensitive(False)
        self.gate_expander.add_row(self.gate_release_spin)
        self.tooltip_helper.add_tooltip(self.gate_release_spin, "gate_release")

        audio_group.add(self.gate_expander)

        settings_box.append(audio_group)

        # Quick Options Group
        options_group = Adw.PreferencesGroup()

        # Copy video without reencoding switch
        self.force_copy_video_check = Adw.SwitchRow(
            title=_("Copy video without reencoding")
        )
        options_group.add(self.force_copy_video_check)
        self.tooltip_helper.add_tooltip(self.force_copy_video_check, "force_copy")

        # Show helpful tooltips switch
        self.show_tooltips_check = Adw.SwitchRow(title=_("Show help on hover"))
        options_group.add(self.show_tooltips_check)
        self.tooltip_helper.add_tooltip(self.show_tooltips_check, "show_tooltips")

        settings_box.append(options_group)

        # Advanced Settings button
        advanced_button = Gtk.Button(label=_("Advanced Settings..."))
        advanced_button.set_margin_top(12)
        advanced_button.connect("clicked", self.on_show_advanced_settings)
        settings_box.append(advanced_button)

        # Initialize settings page to handle connections
        from ui.settings_page import SettingsPage

        self.settings_page = SettingsPage(self)

        # Connect signals and load settings
        self._connect_left_pane_signals()
        self._load_left_pane_settings()

        return settings_box

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

    def _connect_left_pane_signals(self):
        """Connect signals for left pane settings controls"""
        # GPU
        self.gpu_combo.connect(
            "notify::selected", lambda w, p: self._save_gpu_setting(w.get_selected())
        )

        # GPU Device
        self.gpu_device_combo.connect(
            "notify::selected",
            lambda w, p: self.settings_manager.save_setting(
                "gpu-device-index", w.get_selected()
            ),
        )

        # Video Quality
        self.video_quality_combo.connect(
            "notify::selected",
            lambda w, p: self._save_quality_setting(w.get_selected()),
        )

        # Video Codec
        self.video_codec_combo.connect(
            "notify::selected", lambda w, p: self._save_codec_setting(w.get_selected())
        )

        # Copy video without reencoding
        self.force_copy_video_check.connect(
            "notify::active",
            self._on_force_copy_toggled,
        )

        # Audio Handling
        self.audio_handling_combo.connect(
            "notify::selected",
            lambda w, p: self.settings_manager.save_setting(
                "audio-handling", AUDIO_VALUES.get(w.get_selected(), "copy")
            ),
        )

        # Subtitle
        self.subtitle_combo.connect(
            "notify::selected",
            lambda w, p: self.settings_manager.save_setting(
                "subtitle-extract", SUBTITLE_VALUES.get(w.get_selected(), "embedded")
            ),
        )

        # Noise Reduction
        self.noise_reduction_switch.connect(
            "notify::active",
            self._on_noise_reduction_toggled,
        )

        # Noise Reduction Strength
        self.noise_strength_spin.connect(
            "changed",
            lambda w: self.settings_manager.save_setting(
                "noise-reduction-strength", w.get_value()
            ),
        )

        # Noise Gate parameters
        self.gate_threshold_spin.connect(
            "changed",
            lambda w: self.settings_manager.save_setting(
                "noise-gate-threshold", int(w.get_value())
            ),
        )
        self.gate_range_spin.connect(
            "changed",
            lambda w: self.settings_manager.save_setting(
                "noise-gate-range", int(w.get_value())
            ),
        )
        self.gate_attack_spin.connect(
            "changed",
            lambda w: self.settings_manager.save_setting(
                "noise-gate-attack", w.get_value()
            ),
        )
        self.gate_release_spin.connect(
            "changed",
            lambda w: self.settings_manager.save_setting(
                "noise-gate-release", w.get_value()
            ),
        )

        # Show tooltips
        self.show_tooltips_check.connect(
            "notify::active",
            lambda w, p: self._on_tooltips_toggle(w.get_active()),
        )

    def _load_left_pane_settings(self):
        """Load settings for left pane controls"""
        # GPU
        gpu_value = self.settings_manager.load_setting("gpu", "auto")
        gpu_index = self._find_gpu_index(gpu_value)
        self.gpu_combo.set_selected(gpu_index)

        # GPU Device
        gpu_device_index = self.settings_manager.load_setting("gpu-device-index", 0)
        if gpu_device_index <= len(self.detected_gpus):
            self.gpu_device_combo.set_selected(gpu_device_index)

        # Video Quality
        quality_value = self.settings_manager.load_setting("video-quality", "default")
        quality_index = self._find_quality_index(quality_value)
        self.video_quality_combo.set_selected(quality_index)

        # Video Codec
        codec_value = self.settings_manager.load_setting("video-codec", "h264")
        codec_index = self._find_codec_index(codec_value)
        self.video_codec_combo.set_selected(codec_index)

        # Copy video without reencoding
        force_copy = self.settings_manager.load_setting("force-copy-video", False)
        self.force_copy_video_check.set_active(force_copy)

        # Update encoding options state based on force copy setting
        self._update_encoding_options_state(force_copy)

        # Audio Handling - Use reverse lookup
        audio_value = self.settings_manager.load_setting("audio-handling", "copy")
        audio_index = 0  # Default
        for index, internal_value in AUDIO_VALUES.items():
            if internal_value == audio_value:
                audio_index = index
                break
        self.audio_handling_combo.set_selected(audio_index)

        # Subtitle - Use reverse lookup
        subtitle_value = self.settings_manager.load_setting(
            "subtitle-extract", "embedded"
        )
        subtitle_index = 0  # Default
        for index, internal_value in SUBTITLE_VALUES.items():
            if internal_value == subtitle_value:
                subtitle_index = index
                break
        self.subtitle_combo.set_selected(subtitle_index)

        # Noise Reduction
        noise_reduction = self.settings_manager.load_setting("noise-reduction", False)
        self.noise_reduction_switch.set_active(noise_reduction)

        # Noise Reduction Strength
        noise_strength = self.settings_manager.load_setting(
            "noise-reduction-strength", 1.0
        )
        self.noise_strength_spin.set_value(noise_strength)
        self.noise_strength_spin.set_visible(noise_reduction)

        # Noise Gate
        gate_enabled = self.settings_manager.load_setting("noise-gate-enabled", False)
        self.gate_switch.set_active(gate_enabled)
        self.gate_expander.set_visible(noise_reduction)
        self.gate_expander.set_enable_expansion(gate_enabled)

        gate_threshold = self.settings_manager.load_setting("noise-gate-threshold", -30)
        self.gate_threshold_spin.set_value(gate_threshold)
        self.gate_threshold_spin.set_sensitive(gate_enabled)

        gate_range = self.settings_manager.load_setting("noise-gate-range", -60)
        self.gate_range_spin.set_value(gate_range)
        self.gate_range_spin.set_sensitive(gate_enabled)

        gate_attack = self.settings_manager.load_setting("noise-gate-attack", 20.0)
        self.gate_attack_spin.set_value(gate_attack)
        self.gate_attack_spin.set_sensitive(gate_enabled)

        gate_release = self.settings_manager.load_setting("noise-gate-release", 150.0)
        self.gate_release_spin.set_value(gate_release)
        self.gate_release_spin.set_sensitive(gate_enabled)

        # Show tooltips
        show_tooltips = self.settings_manager.load_setting("show-tooltips", True)
        self.show_tooltips_check.set_active(show_tooltips)

    def _on_noise_reduction_toggled(self, switch, param):
        """Handle noise reduction toggle - show/hide strength and gate controls"""
        is_active = switch.get_active()
        self.settings_manager.save_setting("noise-reduction", is_active)

        # Show/hide dependent controls
        self.noise_strength_spin.set_visible(is_active)
        self.gate_expander.set_visible(is_active)

        if not is_active:
            self.gate_switch.set_active(False)

    def _on_gate_switch_changed(self, switch, state):
        """Handle noise gate toggle"""
        self.settings_manager.save_setting("noise-gate-enabled", state)

        self.gate_expander.set_enable_expansion(state)
        self.gate_threshold_spin.set_sensitive(state)
        self.gate_range_spin.set_sensitive(state)
        self.gate_attack_spin.set_sensitive(state)
        self.gate_release_spin.set_sensitive(state)

        if not state:
            self.gate_expander.set_expanded(False)

        return False

    def on_show_advanced_settings(self, button):
        """Show advanced settings in modal dialog"""
        # Create dialog with proper content size and make it resizable
        dialog = Adw.Dialog()
        dialog.set_content_width(700)
        dialog.set_content_height(600)
        dialog.set_can_close(True)

        # Create toolbar view to get the header bar with close button
        toolbar_view = Adw.ToolbarView()

        # Create header bar with title
        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Gtk.Label(label=_("Advanced Settings")))
        toolbar_view.add_top_bar(header_bar)

        # Create a new SettingsPage instance for the dialog
        settings_page = SettingsPage(self)

        # Update controls based on current force copy state
        force_copy_enabled = self.settings_manager.load_setting(
            "force-copy-video", False
        )
        settings_page.update_for_force_copy_state(force_copy_enabled)

        # Create scrolled window to allow content scrolling
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(settings_page.get_page())

        toolbar_view.set_content(scrolled)
        dialog.set_child(toolbar_view)

        # Present dialog
        dialog.present(self.window)

    def _save_gpu_setting(self, index):
        """Save GPU setting as direct value"""
        if index in GPU_VALUES:
            self.settings_manager.save_setting("gpu", GPU_VALUES[index])

    def _save_quality_setting(self, index):
        """Save video quality setting as direct value"""
        if index in VIDEO_QUALITY_VALUES:
            self.settings_manager.save_setting(
                "video-quality", VIDEO_QUALITY_VALUES[index]
            )

    def _save_codec_setting(self, index):
        """Save video codec setting as direct value"""
        if index in VIDEO_CODEC_VALUES:
            self.settings_manager.save_setting("video-codec", VIDEO_CODEC_VALUES[index])

    def _detect_gpu_devices(self):
        """Detect available GPU render devices in the system"""
        gpus = []
        try:
            import glob
            import subprocess

            render_devices = sorted(glob.glob("/dev/dri/renderD*"))
            if len(render_devices) <= 1:
                return gpus

            result = subprocess.run(
                ["lspci", "-nn"], capture_output=True, text=True, timeout=5
            )
            gpu_lines = [
                line
                for line in result.stdout.splitlines()
                if any(kw in line.lower() for kw in ["vga", "3d", "display"])
            ]

            for i, device_path in enumerate(render_devices):
                if i < len(gpu_lines):
                    name = (
                        gpu_lines[i].split(": ", 1)[-1]
                        if ": " in gpu_lines[i]
                        else gpu_lines[i]
                    )
                    # Trim to reasonable length
                    if len(name) > 50:
                        name = name[:47] + "..."
                else:
                    name = os.path.basename(device_path)
                gpus.append({"name": name, "device": device_path})
        except Exception as e:
            print(f"GPU detection error: {e}")
        return gpus

    def _find_gpu_index(self, value):
        """Find index of GPU value"""
        value = value.lower()
        reverse_map = {v: k for k, v in GPU_VALUES.items()}
        return reverse_map.get(value, 0)

    def _find_quality_index(self, value):
        """Find index of quality value"""
        value = value.lower()
        reverse_map = {v: k for k, v in VIDEO_QUALITY_VALUES.items()}
        return reverse_map.get(value, 3)

    def _find_codec_index(self, value):
        """Find index of codec value"""
        value = value.lower()
        reverse_map = {v: k for k, v in VIDEO_CODEC_VALUES.items()}
        return reverse_map.get(value, 0)

    def _on_force_copy_toggled(self, switch, param):
        """Handle force copy toggle - enable/disable encoding options"""
        is_active = switch.get_active()

        # Save the setting
        self.settings_manager.save_setting("force-copy-video", is_active)

        # Update the state of encoding options
        self._update_encoding_options_state(is_active)

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
        if hasattr(self, "show_tooltips_check"):
            self.tooltip_helper.add_tooltip(self.show_tooltips_check, "show_tooltips")

        # Apply tooltips to video edit UI if it exists
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            if hasattr(self.video_edit_page, "ui") and self.video_edit_page.ui:
                self.video_edit_page.ui.apply_tooltips()

    def _on_tooltips_toggle(self, is_active):
        """Handle tooltip toggle change"""
        self.settings_manager.save_setting("show-tooltips", is_active)
        # Re-apply tooltips when re-enabled (they check is_enabled() before showing)
        if hasattr(self, "tooltip_helper") and is_active:
            self._apply_all_tooltips()

    def _update_encoding_options_state(self, force_copy_enabled):
        """Enable/disable encoding options based on force copy state"""
        # When force copy is enabled, disable encoding options (they don't apply)
        # When force copy is disabled, enable encoding options
        enable_encoding_options = not force_copy_enabled

        # Disable/enable encoding settings
        self.gpu_combo.set_sensitive(enable_encoding_options)
        self.video_quality_combo.set_sensitive(enable_encoding_options)
        self.video_codec_combo.set_sensitive(enable_encoding_options)

        # Note: Audio and subtitle options remain enabled even in copy mode
        # as users may want to extract/handle them separately

        # Update video edit page if it exists
        if hasattr(self, "video_edit_page") and self.video_edit_page:
            if hasattr(self.video_edit_page, "ui"):
                self.video_edit_page.ui.update_for_force_copy_state(force_copy_enabled)

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

            # Always initialize/reset per-file metadata with default values when adding to queue
            if hasattr(self, "conversion_page"):
                self.conversion_page.file_metadata[file_path] = {
                    "trim_segments": [],
                    "crop_left": 0,
                    "crop_right": 0,
                    "crop_top": 0,
                    "crop_bottom": 0,
                    "brightness": 0.0,
                    "saturation": 1.0,
                    "hue": 0.0,
                }
                print(f"Initialized clean metadata for: {os.path.basename(file_path)}")

            # Update UI
            if hasattr(self, "conversion_page"):
                GLib.idle_add(self.conversion_page.update_queue_display)
            return True
        return False

    def add_to_conversion_queue(self, file_path):
        """Add a file to the conversion queue without starting conversion"""
        return self.add_file_to_queue(file_path)

    def clear_queue(self):
        """Clear the conversion queue"""
        self.conversion_queue.clear()

        # Clear all file metadata
        if hasattr(self, "conversion_page"):
            self.conversion_page.file_metadata.clear()

        if hasattr(self, "conversion_page"):
            self.conversion_page.update_queue_display()
        print("Conversion queue cleared")

    def remove_from_queue(self, file_path):
        """Remove a specific file from the queue"""
        if file_path in self.conversion_queue:
            self.conversion_queue.remove(file_path)

            # Also clear metadata when removing from queue
            if (
                hasattr(self, "conversion_page")
                and file_path in self.conversion_page.file_metadata
            ):
                del self.conversion_page.file_metadata[file_path]
                print(
                    f"Cleared metadata for removed file: {os.path.basename(file_path)}"
                )

            # If this file is currently loaded in the editor, clear it to force fresh reload
            if (
                hasattr(self, "video_edit_page")
                and self.video_edit_page.current_video_path == file_path
            ):
                self.video_edit_page.current_video_path = None
                print(
                    f"Cleared editor state for removed file: {os.path.basename(file_path)}"
                )

            if hasattr(self, "conversion_page"):
                self.conversion_page.update_queue_display()
            if os.environ.get("BVC_DEBUG"):
                self.logger.debug(f"Removed {os.path.basename(file_path)} from queue")
            return True
        return False

    # Conversion processing
    def start_queue_processing(self):
        """Start processing the conversion queue"""
        if not self.conversion_queue:
            print("Queue is empty, nothing to process")
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        print("Starting queue processing")
        self.is_cancellation_requested = False
        self._was_queue_processing = True
        self.completed_conversions = []
        self.header_bar.set_buttons_sensitive(False)

        # Initialize the progress page with the queue items
        if hasattr(self, "progress_page"):
            self.progress_page.reset()
            self.progress_page.initialize_queue(list(self.conversion_queue))

        self.currently_converting = False
        self.main_stack.set_visible_child_name("progress_view")
        GLib.timeout_add(300, self.process_next_in_queue)

    def convert_current_file(self):
        """Convert the currently opened file in the editor"""
        if not hasattr(self, "video_edit_page") or not self.video_edit_page:
            print("No video edit page available")
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        if self.video_edit_page.is_playing:
            print("Stopping video playback before conversion")
            if self.video_edit_page.mpv_player:
                self.video_edit_page.mpv_player.pause()
            self.video_edit_page.is_playing = False
            if hasattr(self.video_edit_page, "ui") and hasattr(
                self.video_edit_page.ui, "play_pause_button"
            ):
                self.video_edit_page.ui.play_pause_button.set_icon_name(
                    "media-playback-start"
                )
            if self.video_edit_page.position_update_id:
                GLib.source_remove(self.video_edit_page.position_update_id)
                self.video_edit_page.position_update_id = None

        current_file = self.video_edit_page.current_video_path
        if not current_file or not os.path.exists(current_file):
            print("No valid file currently loaded in editor")
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        print(f"Converting current file: {os.path.basename(current_file)}")

        self._original_queue_before_single_conversion = list(self.conversion_queue)
        self._single_file_conversion = True
        self._single_file_to_convert = current_file
        self.conversion_queue = deque([current_file])
        self.start_queue_processing()

    def process_next_in_queue(self):
        """Process the next file in queue if we have capacity"""

        # If cancellation was requested, don't start new conversions
        if self.is_cancellation_requested:
            print("Cancellation requested, stopping queue processing")
            self.header_bar.set_buttons_sensitive(True)
            self._was_queue_processing = False
            return False

        # Determine max concurrent conversions based on settings/hardware
        max_concurrent = 1

        # Check if we can do parallel processing
        # Conditions:
        # 1. GPU setting is "Auto" (index 0)
        # 2. We have multiple detected GPUs
        # 3. Not forcing copy mode (which is fast enough sequentially)
        gpu_setting_index = self.settings_manager.load_setting("gpu", "auto")
        if gpu_setting_index == "auto":
            gpu_setting_index = 0
        elif gpu_setting_index == "nvidia":
            gpu_setting_index = 1
        elif gpu_setting_index == "amd":
            gpu_setting_index = 2
        elif gpu_setting_index == "intel":
            gpu_setting_index = 3
        else:
            # handle other cases or assume sequential
            pass

        # Re-read actual numeric index if needed, but logic above tries to map string back.
        # Actually `load_setting` returns string "auto" usually.
        # Let's check `GPU_VALUES` in `constants.py`?
        # Actually `conversion_page.py` showed `load_setting("gpu", "auto")` returns string.
        # And `load_setting("gpu-device-index", 0)` returns int.

        # Simpler check:
        gpu_mode = self.settings_manager.load_setting("gpu", "auto")
        force_copy = self.settings_manager.get_boolean("force-copy-video", False)

        if (
            gpu_mode == "auto"
            and hasattr(self, "detected_gpus")
            and len(self.detected_gpus) > 1
            and not force_copy
        ):
            max_concurrent = min(
                len(self.detected_gpus), 2
            )  # Limit to 2 for now as per requirement

            # Initialize GPU slots if empty and we haven't started yet
            if not self.gpu_slots and not self.active_conversions:
                self.logger.info(
                    f"Initializing {max_concurrent} GPU slots for parallel processing"
                )
                # Add available GPUs to slots
                for i in range(max_concurrent):
                    # Create slot info with GPU type and device path
                    gpu_info = self.detected_gpus[i]
                    slot = {
                        "id": i,
                        "type": "auto",  # Will be refined by conversion_page logic
                        "device": gpu_info["device"],
                        "name": gpu_info.get("name", "Unknown GPU"),
                    }

                    # Refine type based on name
                    name_lower = slot["name"].lower()
                    if "intel" in name_lower:
                        slot["type"] = "intel"
                    elif "nvidia" in name_lower:
                        slot["type"] = "nvidia"
                    elif "amd" in name_lower:
                        slot["type"] = "amd"

                    self.gpu_slots.append(slot)

        # Check if we reached capacity
        if len(self.active_conversions) >= max_concurrent:
            # We're full, wait for a completion
            return False

        # Check if queue is empty
        if not self.conversion_queue:
            # Only finish if no active conversions remain
            if not self.active_conversions:
                print("Queue processing complete")
                self.header_bar.set_buttons_sensitive(True)
                self.is_cancellation_requested = False
                self.currently_converting = False

                # Show completion summary on progress page
                if hasattr(self, "progress_page"):
                    self.progress_page.show_completion_summary()

                if hasattr(self, "is_minimized") and self.is_minimized:
                    self.send_system_notification(
                        _("Batch Conversion Complete"),
                        _("All queued files have been processed."),
                    )
            return False

        # Get next file
        next_file = self.conversion_queue.popleft()

        # Verify file exists
        if not os.path.exists(next_file):
            print(f"Skipping missing file: {next_file}")
            # Try next one immediately
            GLib.idle_add(self.process_next_in_queue)
            return False

        # Prepare for conversion
        # We don't set current_file_path on conversion_page directly if
        # we want to support parallel, as it might overwrite.
        # But `force_start_conversion` uses it.
        # We should set it right before calling.
        # Prepare for conversion
        # We don't set current_file_path on conversion_page directly wait until called
        self.conversion_page.current_file_path = next_file

        # Allocate a GPU slot if using parallel processing
        gpu_slot = None
        if max_concurrent > 1 and self.gpu_slots:
            gpu_slot = self.gpu_slots.popleft()
            self.logger.info(
                f"Allocated GPU slot for {os.path.basename(next_file)}: {gpu_slot['name']}"
            )

        # Add to active conversions tracking with lock
        conversion_info = {
            "file_path": next_file,
            "gpu_slot": gpu_slot,
            "start_time": GLib.get_real_time(),
        }

        with self.conversions_lock:
            self.active_conversions.append(conversion_info)

        self.currently_converting = True

        # Update UI queue display
        GLib.idle_add(self.conversion_page.update_queue_display)

        # Start conversion with override if slot allocated
        result = self.conversion_page.force_start_conversion(gpu_override=gpu_slot)

        if result is False:
            # Failed to start
            self.logger.error(f"Failed to start conversion for {next_file}")
            with self.conversions_lock:
                if conversion_info in self.active_conversions:
                    self.active_conversions.remove(conversion_info)

            if gpu_slot:
                self.gpu_slots.append(gpu_slot)  # Return slot

            # Try next one
            GLib.idle_add(self.process_next_in_queue)

        # Try to start another if we have capacity (active < max)
        with self.conversions_lock:
            active_count = len(self.active_conversions)

        if active_count < max_concurrent and self.conversion_queue:
            GLib.timeout_add(500, self.process_next_in_queue)

        return False

    def _force_start_conversion(self):
        """Helper to force start conversion with proper error handling"""
        import traceback

        self.currently_converting = True
        result = False
        try:
            print("Forcing conversion to start automatically...")
            if hasattr(self, "conversion_page"):
                result = self.conversion_page.force_start_conversion()
        except Exception as e:
            print(f"Error starting automatic conversion: {e}")
            traceback.print_exc()
            result = False

        if result is False:
            print("Conversion failed to start or was deferred, resetting state.")
            self.currently_converting = False
            GLib.idle_add(lambda: self.conversion_completed(False))

        return False

    def conversion_completed(self, success, skip_tracking=False):
        """Called when a conversion is completed"""
        with self.completion_lock:
            if self._processing_completion:
                self.logger.warning(
                    "WARNING: conversion_completed is already being processed, ignoring duplicate call"
                )
                return
            self._processing_completion = True

        try:
            self.logger.info(f"conversion_completed called with success={success}")

            # Handle cancellation
            if self.is_cancellation_requested:
                self.logger.info("Cancellation requested. Stopping.")
                self.is_cancellation_requested = False
                with self.conversions_lock:
                    self.active_conversions = []
                self.currently_converting = False
                GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
                self.return_to_main_view()
                return

            # Release GPU slot & Tracking
            with self.conversions_lock:
                if self.active_conversions:
                    finished_conversion = self.active_conversions.pop(0)
                    if finished_conversion.get("gpu_slot"):
                        self.logger.info(
                            f"Releasing GPU slot: {finished_conversion['gpu_slot']['name']}"
                        )
                        self.gpu_slots.append(finished_conversion["gpu_slot"])

                if not self.active_conversions:
                    self.currently_converting = False

            # (Legacy tracking code removed)
            # Check single file conversion mode
            if (
                hasattr(self, "_single_file_conversion")
                and self._single_file_conversion
            ):
                self._single_file_conversion = False
                # Restore original queue
                if hasattr(self, "_original_queue_before_single_conversion"):
                    self.conversion_queue = deque(
                        self._original_queue_before_single_conversion
                    )
                    # Update UI to show original queue
                    if hasattr(self, "conversion_page"):
                        GLib.idle_add(self.conversion_page.update_queue_display)

                GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
                return

            # Continue queue processing
            GLib.idle_add(self.process_next_in_queue)

        finally:
            with self.completion_lock:
                self._processing_completion = False

    # UI Navigation
    def show_queue_view(self):
        """Show the file queue view"""
        self.right_stack.set_visible_child_name("queue_view")
        if hasattr(self.header_bar, "set_view"):
            self.header_bar.set_view("queue")
        if hasattr(self, "left_stack"):
            self.left_stack.set_visible_child_name("conversion_settings")
        if hasattr(self.video_edit_page, "cleanup"):
            self.video_edit_page.cleanup()

    def show_editor_for_file(self, file_path):
        """The single, authoritative method to show the editor for a file."""
        if not file_path or not os.path.exists(file_path):
            self.show_error_dialog(_("File not found"))
            return

        print(f"Opening file in editor: {os.path.basename(file_path)}")

        self.right_stack.set_visible_child_name("editor_view")
        if hasattr(self.header_bar, "set_view"):
            self.header_bar.set_view("editor")
        if hasattr(self, "left_stack"):
            self.left_stack.set_visible_child_name("editing_tools")

        def load_video_action():
            if not self.video_edit_page.set_video(file_path):
                self.show_error_dialog(_("Could not load video file"))
                self.show_queue_view()

        GLib.idle_add(load_video_action)

    def show_progress_page(self):
        """Show progress page by switching to progress view"""
        if hasattr(self, "main_stack"):
            self.main_stack.set_visible_child_name("progress_view")
            print("Switched to progress view")

    def return_to_previous_page(self):
        """Return to queue view after conversion completes"""
        if hasattr(self, "main_stack"):
            current_view = self.main_stack.get_visible_child_name()
            if current_view == "progress_view":
                self.main_stack.set_visible_child_name("main_view")
        self.show_queue_view()

    def return_to_main_view(self):
        """Return to main view from progress view"""
        self.main_stack.set_visible_child_name("main_view")
        if hasattr(self, "header_bar"):
            self.header_bar.set_buttons_sensitive(True)

    def on_visible_child_changed(self, stack, param):
        """Update UI when the visible right stack child changes"""
        visible_name = stack.get_visible_child_name()
        if visible_name != "editor_view":
            if hasattr(self.video_edit_page, "cleanup"):
                self.video_edit_page.cleanup()

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

    def on_welcome_action(self, action, param):
        """Show the welcome dialog"""
        self.welcome_dialog = WelcomeDialog(self.window, self.settings_manager)
        self.welcome_dialog.present()

    # Application utilities
    def set_application_icon(self, icon_name=None):
        """Sets the application icon - improved for Wayland compatibility"""
        try:
            if icon_name:
                Gio.Application.set_application_icon(self, icon_name)

            if hasattr(self, "window"):
                self.window.set_icon_name("big-video-converter")

            GLib.set_prgname("big-video-converter")

            print("Application icon set successfully")
        except Exception as e:
            print(f"Error setting application icon: {e}")
            try:
                if hasattr(self, "window"):
                    self.window.set_icon_name("video-x-generic")
                print("Using fallback icon")
            except Exception as e2:
                print(f"Could not set fallback icon: {e2}")

    # Video editing parameters
    def get_trim_times(self):
        """Get the current trim start and end times"""
        start_time = self.settings_manager.load_setting("video-trim-start", 0.0)
        end_time_setting = self.settings_manager.load_setting("video-trim-end", -1.0)
        end_time = None if end_time_setting < 0 else end_time_setting

        print(f"get_trim_times: start={start_time}, end={end_time}")
        return start_time, end_time, self.video_duration

    def reset_trim_settings(self):
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

        print("Trim settings have been reset")

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

    def show_toast(self, message):
        """Shows a toast notification."""
        if hasattr(self, "toast_overlay"):
            toast = Adw.Toast(title=message)
            self.toast_overlay.add_toast(toast)

    def send_system_notification(self, title, body):
        """Send a system notification"""
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.send_notification(None, notification)

    def show_completion_screen(self):
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

    def do_open(self, files, n_files, hint):
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
    def select_files_for_queue(self):
        """Open file chooser to select video files for the queue"""
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Video Files"))
        dialog.set_modal(True)

        if hasattr(self, "last_accessed_directory") and self.last_accessed_directory:
            try:
                initial_folder = Gio.File.new_for_path(self.last_accessed_directory)
                dialog.set_initial_folder(initial_folder)
            except Exception as e:
                print(f"Error setting initial folder: {e}")

        filter = Gtk.FileFilter()
        filter.set_name(_("Video Files"))
        for mime_type in VIDEO_FILE_MIME_TYPES:
            filter.add_mime_type(mime_type)

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter)
        dialog.set_filters(filters)

        dialog.open_multiple(self.window, None, self._on_files_selected)

    def select_folder_for_queue(self):
        """Open folder chooser to select a folder with video files"""
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Folder with Video Files"))
        dialog.set_modal(True)

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
                if folder_path and os.path.isdir(folder_path):
                    files_added = self.process_path_recursively(folder_path)
                    if files_added > 0:
                        self.last_accessed_directory = folder_path
                        self.settings_manager.save_setting(
                            "last-accessed-directory", self.last_accessed_directory
                        )
                        message = _(
                            "{} video files have been added to the queue."
                        ).format(files_added)
                        GLib.idle_add(
                            lambda msg=message: self.show_info_dialog(
                                _("Files Added"), msg
                            )
                        )
                    else:
                        GLib.idle_add(
                            lambda: self.show_info_dialog(
                                _("No Files Found"),
                                _(
                                    "No valid video files were found in the selected folder."
                                ),
                            )
                        )
                else:
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

                if files_added > 0:
                    self.conversion_page.update_queue_display()
                else:
                    GLib.idle_add(
                        lambda: self.show_info_dialog(
                            _("No Files Added"),
                            _("No valid video files were found in your selection."),
                        )
                    )
        except Exception as error:
            if self.logger:
                self.logger.warning(f"Files not selected: {error}")

    def show_network_file_dialog(self):
        """Show dialog to add files from network locations (SFTP, SMB, FTP)"""
        dialog = Adw.Dialog()
        dialog.set_title(_("Add Network File"))
        dialog.set_content_width(480)
        dialog.set_content_height(420)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        # Info label
        info_label = Gtk.Label(
            label=_(
                "Connect to a remote server to browse and add video files.\n"
                "Files are accessed directly from the network without downloading."
            )
        )
        info_label.set_wrap(True)
        info_label.set_xalign(0)
        info_label.add_css_class("dim-label")
        content.append(info_label)

        # Protocol group
        protocol_group = Adw.PreferencesGroup()
        protocol_group.set_title(_("Connection"))

        # Protocol ComboRow
        protocol_row = Adw.ComboRow()
        protocol_row.set_title(_("Protocol"))
        protocol_model = Gtk.StringList()
        for p in ["SFTP (SSH)", "SMB (Windows Share)", "FTP"]:
            protocol_model.append(p)
        protocol_row.set_model(protocol_model)
        protocol_group.add(protocol_row)

        # Server entry
        server_row = Adw.EntryRow()
        server_row.set_title(_("Server"))
        server_row.set_text("")
        protocol_group.add(server_row)

        # Port entry
        port_row = Adw.EntryRow()
        port_row.set_title(_("Port"))
        port_row.set_text("")
        protocol_group.add(port_row)

        # Username entry
        user_row = Adw.EntryRow()
        user_row.set_title(_("Username"))
        user_row.set_text("")
        protocol_group.add(user_row)

        # Remote path entry
        path_row = Adw.EntryRow()
        path_row.set_title(_("Remote Path"))
        path_row.set_text("/")
        protocol_group.add(path_row)

        content.append(protocol_group)

        # Status label
        status_label = Gtk.Label(label="")
        status_label.set_wrap(True)
        status_label.set_xalign(0)
        status_label.set_visible(False)
        content.append(status_label)

        # Connect button
        connect_button = Gtk.Button(label=_("Connect and Browse"))
        connect_button.add_css_class("suggested-action")
        connect_button.add_css_class("pill")
        connect_button.set_halign(Gtk.Align.CENTER)
        connect_button.set_margin_top(8)
        content.append(connect_button)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(content)
        scrolled.set_vexpand(True)
        toolbar_view.set_content(scrolled)
        dialog.set_child(toolbar_view)

        def on_connect_clicked(button):
            server = server_row.get_text().strip()
            if not server:
                status_label.set_text(_("Please enter a server address."))
                status_label.add_css_class("error")
                status_label.set_visible(True)
                return

            # Build URI from fields
            protocol_idx = protocol_row.get_selected()
            schemes = ["sftp", "smb", "ftp"]
            scheme = schemes[protocol_idx]

            user = user_row.get_text().strip()
            port = port_row.get_text().strip()
            remote_path = path_row.get_text().strip() or "/"

            # Build URI
            if user:
                authority = f"{user}@{server}"
            else:
                authority = server
            if port:
                authority += f":{port}"

            # SMB uses smb://server/share format
            if scheme == "smb" and not remote_path.startswith("/"):
                remote_path = "/" + remote_path

            uri = f"{scheme}://{authority}{remote_path}"
            print(f"Mounting network location: {uri}")

            status_label.remove_css_class("error")
            status_label.add_css_class("dim-label")
            status_label.set_text(_("Connecting..."))
            status_label.set_visible(True)
            button.set_sensitive(False)

            # Mount using GIO
            gfile = Gio.File.new_for_uri(uri)
            mount_op = Gtk.MountOperation.new(self.window)

            def on_mount_finished(source, result):
                try:
                    gfile.mount_enclosing_volume_finish(result)
                except GLib.Error as e:
                    # Already mounted is not an error
                    if "already mounted" not in str(e).lower():
                        print(f"Mount error: {e}")
                        GLib.idle_add(
                            lambda: self._handle_mount_error(
                                status_label, button, str(e)
                            )
                        )
                        return

                print(f"Mount successful for {uri}")
                GLib.idle_add(lambda: self._open_network_file_browser(dialog, gfile))

            gfile.mount_enclosing_volume(
                Gio.MountMountFlags.NONE, mount_op, None, on_mount_finished
            )

        connect_button.connect("clicked", on_connect_clicked)
        dialog.present(self.window)

    def _handle_mount_error(self, status_label, button, error_msg):
        """Handle mount error in network dialog"""
        status_label.remove_css_class("dim-label")
        status_label.add_css_class("error")
        status_label.set_text(_("Connection failed: {}").format(error_msg))
        status_label.set_visible(True)
        button.set_sensitive(True)

    def _open_network_file_browser(self, network_dialog, gfile):
        """Open file browser at the mounted network location"""
        network_dialog.close()

        # Resolve GVFS local path
        local_path = gfile.get_path()
        if not local_path:
            # Try to find the GVFS mount point
            try:
                mount = gfile.find_enclosing_mount(None)
                root = mount.get_root()
                local_path = root.get_path()
            except Exception as e:
                self.logger.error(f"Could not resolve GVFS path: {e}")
                self.show_error_dialog(
                    _("Error"),
                    _(
                        "Connected but could not resolve local path. Try browsing via file manager."
                    ),
                )
                return

        print(f"Browsing network files at: {local_path}")

        # Open file dialog at the mounted location
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Network Video Files"))
        dialog.set_modal(True)

        try:
            initial_folder = Gio.File.new_for_path(local_path)
            dialog.set_initial_folder(initial_folder)
        except Exception as e:
            print(f"Error setting initial folder: {e}")

        filter = Gtk.FileFilter()
        filter.set_name(_("Video Files"))
        for mime_type in VIDEO_FILE_MIME_TYPES:
            filter.add_mime_type(mime_type)

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter)
        dialog.set_filters(filters)

        dialog.open_multiple(self.window, None, self._on_files_selected)

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

        def on_dialog_close(widget):
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
