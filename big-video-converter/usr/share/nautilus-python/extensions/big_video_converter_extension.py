"""
Big Video Converter - Nautilus Extension
Adds a context menu option to convert video files using the
Big Video Converter application.
"""

import gettext
import subprocess
from pathlib import Path
from urllib.parse import unquote

# Import 'gi' and explicitly require GTK and Nautilus versions.
# This is mandatory in modern PyGObject to prevent warnings and ensure API compatibility.
import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gio, GLib, GObject, Nautilus

# --- Internationalization (i18n) Setup ---
APP_NAME = "big-video-converter"

gettext.bindtextdomain(APP_NAME, "/usr/share/locale")


def _(message):
    """Domain-specific translation to avoid conflict with Nautilus' own textdomain."""
    return gettext.dgettext(APP_NAME, message)


class BigVideoConverterExtension(GObject.GObject, Nautilus.MenuProvider):
    """
    Provides the context menu items for Nautilus to allow video conversion.
    """

    def __init__(self):
        """Initializes the extension."""
        super().__init__()
        self.app_executable = 'big-video-converter-gui'

        # Using a set provides O(1) lookup time, which is more efficient than a list.
        # Both spellings of the Matroska type are listed: shared-mime-info 2.4
        # renamed it from "video/x-matroska" to "video/matroska", and Nautilus
        # reports the canonical name of the installed database — matching only
        # the old one made the menu disappear for every .mkv file.
        self.supported_mimetypes = {
            'video/mp4', 'video/matroska', 'video/x-matroska', 'video/webm',
            'video/quicktime', 'video/x-msvideo', 'video/x-ms-wmv', 'video/mpeg',
            'video/x-m4v', 'video/mp2t', 'video/x-flv', 'video/3gpp', 'video/ogg',
            'video/x-ogm+ogg', 'video/vnd.avi', 'video/avi',
        }

        # Fallback for mimetype names that keep changing between
        # shared-mime-info releases. Nautilus cannot import the application's
        # modules: this must equal constants.VIDEO_FILE_EXTENSIONS (a test
        # compares them).
        self.supported_extensions = {
            '.mp4', '.mkv', '.webm', '.mov', '.avi', '.wmv', '.mpeg', '.mpg',
            '.m4v', '.ts', '.m2ts', '.mts', '.flv', '.3gp', '.ogv',
        }

    def get_file_items(self, *args):
        """
        Returns menu items for the selected files.
        The menu is only shown if one or more supported video files are selected.
        
        Note: Using *args for compatibility across Nautilus versions.
        The last argument is always the list of selected files.
        """
        files = args[-1]
        video_files = [f for f in files if self._is_video_file(f)]
        if not video_files:
            return []

        num_videos = len(video_files)

        # Define the label based on the number of selected files.
        if num_videos == 1:
            label = _('Convert Video')
            name = 'BigVideoConverter::Convert'
        else:
            label = gettext.dngettext(
                APP_NAME, 'Convert {0} Video', 'Convert {0} Videos', num_videos
            ).format(num_videos)
            name = 'BigVideoConverter::ConvertMultiple'

        menu_item = Nautilus.MenuItem(name=name, label=label)
        menu_item.connect('activate', self._launch_application, video_files)
        return [menu_item]

    def _is_video_file(self, file_info: Nautilus.FileInfo) -> bool:
        """
        Checks if a file is a supported video by its mimetype, falling back to
        the file extension when the mimetype is not one we know.
        """
        if not file_info or file_info.is_directory():
            return False

        mime_type = file_info.get_mime_type() or ''
        if mime_type in self.supported_mimetypes:
            return True

        uri = file_info.get_uri() or ''
        return Path(unquote(uri)).suffix.lower() in self.supported_extensions

    def _get_file_path(self, file_info: Nautilus.FileInfo) -> str | None:
        """
        The local path of the file, or None when it has none. GIO keeps
        non-UTF-8 names intact and maps network locations to their gvfs FUSE
        path when that is available.
        """
        return file_info.get_location().get_path()

    def _launch_application(self, menu_item: Nautilus.MenuItem, files: list[Nautilus.FileInfo]):
        """
        Launches the Big Video Converter application with the selected files.
        """
        file_paths = []
        for f in files:
            path = self._get_file_path(f)
            if path and Path(path).exists():
                file_paths.append(path)

        if not file_paths:
            self._show_error_notification(
                _("No valid local files selected"),
                _("Could not get the path for the selected video files.")
            )
            return

        try:
            cmd = [self.app_executable] + file_paths
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except OSError as e:
            print(f"Error launching '{self.app_executable}': {e}")
            self._show_error_notification(
                _("Application Launch Error"),
                _("Failed to start Big Video Converter: {0}").format(str(e))
            )

    def _show_error_notification(self, title: str, message: str):
        """
        Displays a desktop error notification using 'notify-send'.
        """
        # Nautilus must not wait for the notification daemon; GIO reaps the
        # child from the main loop.
        try:
            Gio.Subprocess.new([
                'notify-send',
                '--icon=dialog-error',
                f'--app-name={APP_NAME}',
                title,
                message
            ], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            # Fallback if 'notify-send' is not installed.
            print(f"ERROR: [{title}] {message}")
