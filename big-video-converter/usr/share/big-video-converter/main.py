"""
Main entry point for Big Video Converter application.
"""

import logging
import os
import sys
import threading
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

import gettext

import big_gtk_kit
from audio_settings import AudioSettingsMixin
from constants import APP_ID
from engine.dependency_checker import DependencyChecker
from engine.job_options import SOURCE_HDR_MODES, parse_segments_arg
from engine.settings_store import SettingsStore
from file_intake import FileIntakeMixin
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from queue_runner import QueueRunnerMixin
from sidebar_builder import SidebarBuilderMixin
from ui.conversion_page import ConversionPage
from ui.dependency_dialog import InstallDependencyDialog
from ui.header_bar import HeaderBar
from ui.help_tooltips import HelpTooltips
from ui.progress_page import ProgressPage
from ui.style import install as install_style
from ui.video_edit_page import VideoEditPage
from ui.welcome_dialog import WelcomeDialog
from video_profiles import VideoProfilesMixin

_ = gettext.gettext
ngettext = gettext.ngettext

MIN_WIDTH, MIN_HEIGHT = 640, 480


class VideoConverterApp(
    AudioSettingsMixin,
    VideoProfilesMixin,
    QueueRunnerMixin,
    FileIntakeMixin,
    SidebarBuilderMixin,
    Adw.Application,
):
    def _window_buttons_on_left(self):
        """Detect if window buttons (close/min/max) are on the left side."""
        try:
            # GNOME desktop schemas are optional outside a GNOME session.
            # Constructing GSettings for a missing schema aborts in C; it is
            # not a Python exception, so check before calling the constructor.
            source = Gio.SettingsSchemaSource.get_default()
            schema = (
                source.lookup("org.gnome.desktop.wm.preferences", True)
                if source is not None
                else None
            )
            if (
                schema is None
                or not schema.has_key("button-layout")
                or schema.get_path() is None
            ):
                return False
            settings = Gio.Settings.new_full(schema, None, None)
            layout = settings.get_string("button-layout")
            if layout and ":" in layout:
                left, right = layout.split(":", 1)
                if "close" in left:
                    return True
                if "close" in right:
                    return False
            elif layout:
                # If no colon, treat as right side (default GNOME)
                if "close" in layout:
                    return False
        except GLib.Error as error:
            # Called before self.logger exists, and in tests without an instance.
            logging.getLogger(__name__).debug(
                "Could not read the button layout: %s", error
            )
        # Default: right side
        return False

    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN
            | Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )

        # Players hand a file over with the cuts the user marked there.
        self.add_main_option(
            "segments",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            "Cuts to keep, in seconds: START-END[,START-END...]",
            "RANGES",
        )
        # And how they read its colors, when the file does not say.
        self.add_main_option(
            "source-hdr",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            "Colors of a file that does not describe them: pq, hlg or sdr",
            "MODE",
        )

        GLib.set_prgname("big-video-converter")
        self.connect("startup", lambda _app: big_gtk_kit.install())
        self.connect("activate", self.on_activate)

        self.settings_store = SettingsStore()
        self.dependency_checker = DependencyChecker()
        self.last_accessed_directory = self.settings_store.load_setting(
            "last-accessed-directory", os.path.expanduser("~")
        )

        self.help_tooltips = HelpTooltips(self.settings_store)

        self.logger = logging.getLogger(__name__)

        # Indices: 0=MP4, 1=MKV, 2=MOV, 3=WebM
        current_format = self.settings_store.load_setting("output-format-index", 0)
        if current_format > 3:
            self.settings_store.save_setting("output-format-index", 0)  # Reset to MP4

        self.conversions_running = 0
        self.conversion_queue = deque()
        # Track active conversions for parallel processing
        self.active_conversions = []  # List of dictionaries with conversion info
        self.gpu_slots = deque()  # Queue of available GPU slots for parallel processing
        self.is_cancellation_requested = False
        # From queue start until it settles, between its jobs too.
        self.currently_converting = False
        # Output paths reserved by jobs that have not finished yet, so two
        # parallel jobs never pick the same free name.
        self.claimed_outputs = set()

        # Track completed conversions for completion screen
        self.completed_conversions = []

        self.conversions_lock = threading.Lock()

        self._setup_actions()

    def _setup_actions(self):
        actions = {
            "about": self.on_about_action,
            "welcome": self.on_welcome_action,
            "quit": lambda a, p: self._on_window_close_request(self.window),
            "add_files": lambda a, p: self.select_files_for_queue(),
            "add_folder": lambda a, p: self.select_folder_for_queue(),
            "add_network_file": lambda a, p: self.show_network_file_dialog(),
            "start_conversion": lambda a, p: self.start_queue_processing(),
            "clear_queue": lambda a, p: self.clear_queue(),
            "restore_settings": lambda a, p: self._on_restore_settings(),
            "toggle_sidebar": lambda a, p: self.split_view.set_show_sidebar(
                not self.split_view.get_show_sidebar()
            ),
        }

        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        # Stateful toggle for tooltips (used in hamburger menu)
        show_tooltips = self.settings_store.load_setting("show-tooltips", True)
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
        self.set_accels_for_action("app.toggle_sidebar", ["F9"])
        # The queue always shows its settings; only the editor hides them.
        self.lookup_action("toggle_sidebar").set_enabled(False)

    def _setup_icon_theme(self):
        """Put the bundled icons first in the search path, once per process."""
        icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
        icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        paths = icon_theme.get_search_path()
        if os.path.isdir(icons_dir) and icons_dir not in paths:
            icon_theme.set_search_path([icons_dir, *paths])

    def on_activate(self, app) -> None:
        self._setup_icon_theme()

        is_first_activation = not hasattr(self, "window") or self.window is None

        if is_first_activation:
            self._create_window()

        # Present the window early so dialogs can be transient for it
        self.window.present()

        if not self.dependency_checker.are_dependencies_available():
            self._show_dependency_install_dialog()
            # Don't proceed with normal activation until ffmpeg is handled
            return

        if is_first_activation and WelcomeDialog.should_show_welcome(
            self.settings_store
        ):
            GLib.idle_add(self._show_welcome_dialog_startup)

    def _show_welcome_dialog_startup(self):
        """Show welcome dialog on startup (called via idle_add)"""
        self.welcome_dialog = WelcomeDialog(self.window, self.settings_store)
        self.welcome_dialog.present()
        return False  # Remove idle callback

    def _create_window(self):
        """Create the main application window and UI components"""
        install_style()
        self.window = Adw.ApplicationWindow(application=self)
        self.window.add_css_class("big-video-converter")

        # Small enough for 1024x600 panels and 1366x768 at 150 % (911x512
        # logical) with room for a desktop panel. Below 1000sp the sidebar
        # overlays the content instead of sitting beside it.
        self.window.set_size_request(MIN_WIDTH, MIN_HEIGHT)

        # Restore window size from settings
        width = self.settings_store.load_setting("window-width", 1200)
        height = self.settings_store.load_setting("window-height", 720)

        width = max(width, MIN_WIDTH)
        height = max(height, MIN_HEIGHT)
        self.window.set_default_size(width, height)

        # Restore maximized state
        is_maximized = self.settings_store.load_setting("window-maximized", False)
        if is_maximized:
            self.window.maximize()

        self.window.set_title("Big Video Converter")

        self.window.connect("close-request", self._on_window_close_request)

        self.window.set_icon_name("big-video-converter")

        self._setup_drag_and_drop()

        self.toast_overlay = Adw.ToastOverlay()

        self.main_stack = Adw.ViewStack()

        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_vexpand(True)
        self.split_view.set_min_sidebar_width(300)
        self.split_view.set_max_sidebar_width(640)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1000sp"))
        compact.add_setter(self.split_view, "collapsed", True)
        # Collapsed, the overlay takes the sidebar's natural width, which can
        # be the whole window; leave the queue visible beside it.
        compact.add_setter(self.split_view, "max-sidebar-width", 360)
        self.window.add_breakpoint(compact)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(
            """
        .sidebar {
            background-color: @sidebar_bg_color;
        }
        .warning-banner {
            background-color: alpha(@warning_color, 0.25);
            color: @warning_color;
        }
        .chip {
            background-color: alpha(@accent_bg_color, 0.12);
            border-radius: 999px;
            padding: 1px 8px;
        }
        """
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self._create_left_pane()
        self._create_right_pane()

        sidebar_position = self.settings_store.load_setting("sidebar-position", 430)
        self.split_view.set_sidebar_width_fraction(
            min(0.5, max(0.2, sidebar_position / width))
        )

        self.main_stack.add_titled(self.split_view, "main_view", _("Main"))

        self.toast_overlay.set_child(self.main_stack)

        # Subtitle-only alert banner
        self.subtitle_banner = Adw.Banner()
        self.subtitle_banner.set_title(
            _("Only subtitles will be extracted; videos will not be converted")
        )
        self.subtitle_banner.add_css_class("warning-banner")
        self.subtitle_banner.set_revealed(
            self.settings_store.get_boolean("only-extract-subtitles", False)
        )

        # Wrap toast overlay + banner in a vertical box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(self.subtitle_banner)
        content_box.append(self.toast_overlay)
        self.toast_overlay.set_vexpand(True)
        self.window.set_content(content_box)

        self._create_pages()

    def _on_window_close_request(self, window):
        if not self.active_conversions and not self.conversions_running:
            self.quit()
            return True
        if getattr(self, "_close_dialog", None) is not None:
            return True
        dialog = Adw.AlertDialog(
            heading=_("Stop converting and close?"),
            body=_(
                "Conversions are still running. Closing stops unfinished work and keeps its originals."
            ),
        )
        self._close_dialog = dialog
        dialog.add_response("continue", _("Keep converting"))
        dialog.add_response("close", _("Stop and close"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("continue")
        dialog.set_close_response("continue")

        def respond(_dialog, response):
            self._close_dialog = None
            if response == "close":
                self.quit()

        dialog.connect("response", respond)
        dialog.present(window)
        return True

    def quit(self):
        """Keep the main loop alive until every conversion has acknowledged cancellation."""
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        self.settings_store.flush()
        if getattr(self, "conversion_page", None) is not None:
            self.conversion_page.thumbnail_manager.shutdown()
        self.is_cancellation_requested = True
        self.conversion_queue.clear()
        if getattr(self, "progress_page", None) is not None:
            self.progress_page._do_cancel_all()
        if getattr(self, "conversion_page", None) is not None:
            self.conversion_page.close_decision_dialogs()
        if getattr(self, "window", None) is not None and self.window.get_realized():
            self._save_window_state()
        if self._finish_quit():
            GLib.timeout_add(100, self._finish_quit)

    def _finish_quit(self):
        if self.active_conversions or self.conversions_running:
            return GLib.SOURCE_CONTINUE
        page = getattr(self, "conversion_page", None)
        if page is not None and not page.thumbnail_manager.shutdown_complete:
            return GLib.SOURCE_CONTINUE
        if getattr(self, "video_edit_page", None) is not None:
            self.video_edit_page.cleanup()
        super().quit()
        return GLib.SOURCE_REMOVE

    def _save_window_state(self):
        """Save current window size, position and maximized state"""
        is_maximized = self.window.is_maximized()
        self.settings_store.save_setting("window-maximized", is_maximized)

        # Only save size if not maximized
        if not is_maximized:
            width = self.window.get_width()
            height = self.window.get_height()
            self.settings_store.save_setting("window-width", width)
            self.settings_store.save_setting("window-height", height)

        if self.split_view.get_show_sidebar():
            self.settings_store.save_setting(
                "sidebar-position", self.split_view.get_sidebar().get_width()
            )

    def _create_right_pane(self):
        """Create right pane with stack for queue and editor views using ToolbarView"""
        self.right_toolbar_view = Adw.ToolbarView()

        # Detect window button layout
        window_buttons_left = self._window_buttons_on_left()

        self.header_bar = HeaderBar(self, window_buttons_left)
        self.right_toolbar_view.add_top_bar(self.header_bar)

        self.right_stack = Adw.ViewStack()
        self.right_stack.set_hhomogeneous(False)
        # The queue needs less height than the editor; do not make it pay.
        self.right_stack.set_vhomogeneous(False)
        self.right_stack.set_vexpand(True)
        self.right_stack.set_hexpand(True)

        self.right_stack.connect(
            "notify::visible-child-name", self.on_visible_child_changed
        )

        # The pages will be added here in _create_pages()
        # Queue view and editor view

        self.right_toolbar_view.set_content(self.right_stack)

        self.right_toolbar_view.set_size_request(620, -1)

        content = Gtk.Overlay(child=self.right_toolbar_view)
        content.add_overlay(self._create_sidebar_resize_handle())
        self.split_view.set_content(content)

    def _create_pages(self):
        self.conversion_page = ConversionPage(self)

        self.video_edit_page = VideoEditPage(self, self.conversion_page)
        self.progress_page = ProgressPage(self)

        # The editing tools need the editor page, created above.
        self.video_edit_page.ui.populate_sidebar(self.editing_tools_box)
        # The left pane loaded its settings before the editor existed.
        self.video_edit_page.ui.update_for_force_copy_state(
            self.settings_store.get_boolean("force-copy-video", False)
        )

        self.right_stack.add_titled(
            self.conversion_page.get_page(), "queue_view", _("Queue")
        )

        # Add editor view to right stack. In a small window the editor's
        # controls wrap below the video; they scroll instead of being clipped.
        editor_scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            child=self.video_edit_page.get_page(),
        )
        self.right_stack.add_titled(editor_scroller, "editor_view", _("Editor"))

        self.main_stack.add_titled(
            self.progress_page.get_page(), "progress_view", _("Progress")
        )

    def _on_tooltip_action_activated(self, action, _param):
        current = action.get_state().get_boolean()
        new_state = not current
        action.set_state(GLib.Variant.new_boolean(new_state))
        self.settings_store.save_setting("show-tooltips", new_state)
        self.help_tooltips.refresh()

    # UI Navigation
    def show_queue_view(self) -> None:
        self.right_stack.set_visible_child_name("queue_view")
        self.header_bar.set_view("queue")
        self.left_stack.set_visible_child_name("conversion_settings")
        self.sidebar_title.set_title("Big Video Converter")
        self.sidebar_title.set_subtitle("")
        # Collapsed, the settings would cover the queue; the header's
        # toggle brings them over it.
        self.split_view.set_show_sidebar(not self.split_view.get_collapsed())
        self.video_edit_page.cleanup()
        self.conversion_page.update_queue_display()

    def show_editor_for_file(self, file_path: str) -> None:
        """The single, authoritative method to show the editor for a file."""
        if not file_path or not os.path.exists(file_path):
            self.show_error_dialog(_("File not found"))
            return

        self.logger.debug("Opening file in editor: %s", os.path.basename(file_path))

        self.right_stack.set_visible_child_name("editor_view")
        self.header_bar.set_view("editor")
        self.left_stack.set_visible_child_name("editing_tools")
        self.sidebar_title.set_title(_("Edit video"))
        self.sidebar_title.set_subtitle(_("Changes apply only to the selected video"))

        def load_video_action() -> None:
            if not self.video_edit_page.set_video(file_path):
                self.show_error_dialog(_("Could not load video file"))
                self.show_queue_view()

        GLib.idle_add(load_video_action)

    def show_progress_page(self) -> None:
        self.main_stack.set_visible_child_name("progress_view")

    def return_to_main_view(self) -> None:
        self.main_stack.set_visible_child_name("main_view")
        self.show_queue_view()
        self.header_bar.set_buttons_sensitive(True)

    def on_visible_child_changed(self, stack, param) -> None:
        """Leaving the editor releases its player."""
        if stack.get_visible_child_name() != "editor_view":
            self.video_edit_page.cleanup()

    # Menu actions
    def on_about_action(self, action, param) -> None:
        from constants import APP_DEVELOPERS, APP_NAME, APP_VERSION

        about = Adw.AboutDialog(
            developer_name="BigLinux",
            issue_url="https://github.com/biglinux/big-video-converter/issues",
            application_name=APP_NAME,
            application_icon="big-video-converter",
            version=APP_VERSION,
            developers=APP_DEVELOPERS,
            license_type=Gtk.License.MIT_X11,
            website="https://www.biglinux.com.br",
        )
        about.present(self.window)

    def on_welcome_action(self, action, param) -> None:
        self.welcome_dialog = WelcomeDialog(self.window, self.settings_store)
        self.welcome_dialog.present()

    def _on_restore_settings(self) -> None:
        """Delegate to settings_page reset (reuses existing implementation)."""
        self.settings_page._on_reset_button_clicked(None)

    # Dialog helpers
    def show_error_dialog(self, message: str, detail: str | None = None) -> None:
        """Shows an error dialog.

        The second argument is optional so callers can pass either
        (message) or (title, detail).
        """
        dialog = Gtk.AlertDialog()
        if detail is None:
            dialog.set_message(_("Error"))
            dialog.set_detail(message)
        else:
            dialog.set_message(message)
            dialog.set_detail(detail)
        dialog.show(self.window)

    def show_info_dialog(self, title: str, message: str) -> None:
        dialog = Gtk.AlertDialog()
        dialog.set_message(title)
        dialog.set_detail(message)
        dialog.show(self.window)

    def send_system_notification(self, title: str, body) -> None:
        """Tell someone who is looking elsewhere; an active window shows it already."""
        if self.window.is_active():
            return
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.send_notification(None, notification)

    def do_open(self, files: str, n_files: str, hint) -> None:
        """Handle files opened via file association or from another instance"""
        if not hasattr(self, "window") or self.window is None:
            self.activate()

        files_added = 0
        refused_busy = False
        # smb:// or sftp:// without a FUSE mount has no path FFmpeg can read.
        remote = [file.get_uri() for file in files if not file.get_path()]
        for file in files:
            file_path = file.get_path()
            if (
                file_path
                and os.path.exists(file_path)
                and self.is_valid_video_file(file_path)
            ):
                if self.add_file_to_queue(file_path):
                    files_added += 1
                elif self.currently_converting:
                    refused_busy = True
        if refused_busy:
            self.show_info_dialog(
                _("Conversion in progress"),
                _("New videos can be added when the current conversions finish."),
            )
        if remote:
            self.show_error_dialog(
                ngettext(
                    "This file cannot be opened",
                    "These files cannot be opened",
                    len(remote),
                ),
                ngettext(
                    "It is not on a local or mounted drive. Open its network share "
                    "in the file manager, or use “Add Network File”, then add it "
                    "again:\n{files}",
                    "They are not on a local or mounted drive. Open their network "
                    "share in the file manager, or use “Add Network File”, then add "
                    "them again:\n{files}",
                    len(remote),
                ).format(files="\n".join(remote)),
            )

        if files_added > 0:
            GLib.idle_add(self.show_queue_view)
            # Request window focus when files are added externally
            if hasattr(self, "window") and self.window:
                self.window.present()

    def do_command_line(self, command_line):
        args = command_line.get_arguments()
        options = command_line.get_options_dict().end().unpack()
        segments = None
        source_hdr = options.get("source-hdr")
        try:
            if "segments" in options:
                segments = parse_segments_arg(options["segments"])
            if source_hdr is not None and source_hdr not in SOURCE_HDR_MODES:
                raise ValueError(
                    f"source-hdr {source_hdr!r} is not one of {', '.join(SOURCE_HDR_MODES)}"
                )
        except ValueError as exc:
            # Refuse rather than queue the whole file as if no cuts were asked
            # for. Players start us detached, so stderr alone reaches nobody.
            command_line.printerr(f"big-video-converter: {exc}\n")
            self.activate()
            self.show_error_dialog(
                _("The settings sent by the player could not be used"), str(exc)
            )
            return 2

        # A second instance forwards its arguments: resolve them against its
        # working directory, not ours, and keep URIs for do_open to report.
        files = [command_line.create_file_for_arg(arg) for arg in args[1:]]
        files = [f for f in files if not f.get_path() or os.path.isfile(f.get_path())]
        if files:
            self.do_open(files, len(files), "")
            for path in filter(None, (f.get_path() for f in files)):
                metadata = self.conversion_page.file_metadata.get(path)
                if metadata is None:
                    continue
                if segments is not None:
                    metadata["trim_segments"] = [dict(s) for s in segments]
                if source_hdr is not None:
                    metadata["source_hdr"] = source_hdr

        self.activate()
        return 0

    def _show_dependency_install_dialog(self):
        """Shows the dialog to install required dependencies (FFmpeg and MPV)."""
        # Opening files and a second instance activate again; one prompt only.
        if getattr(self, "_dependency_prompted", False):
            return
        self._dependency_prompted = True
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
            # The preview modules load when the application starts, so a
            # successful installation still needs a fresh start; the dialog's
            # own text asks for it. A cancelled or failed one leaves nothing
            # usable either.
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
