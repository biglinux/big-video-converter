"""Files context-menu entry that opens the selected videos in Big Video Converter."""

import gettext
import logging
from pathlib import Path
from urllib.parse import unquote

import gi

# Nautilus 49 and later ship only the 4.1 typelib, and nautilus-python has
# loaded it before this file runs: asking for 4.0 raises and the menu entry
# never appears. Older releases ship 4.0 only.
try:
    gi.require_version("Nautilus", "4.1")
except ValueError:
    gi.require_version("Nautilus", "4.0")

from gi.repository import Gio, GLib, GObject, Nautilus

APP_NAME = "big-video-converter"

gettext.bindtextdomain(APP_NAME, "/usr/share/locale")


def _(message):
    """Domain-specific translation to avoid conflict with Nautilus' own textdomain."""
    return gettext.dgettext(APP_NAME, message)


class BigVideoConverterExtension(GObject.GObject, Nautilus.MenuProvider):
    def __init__(self):
        super().__init__()
        self.app_executable = "big-video-converter-gui"

        # Both spellings of the Matroska type: shared-mime-info 2.4 renamed
        # "video/x-matroska" to "video/matroska", and Nautilus reports the
        # name the installed database uses.
        self.supported_mimetypes = {
            "video/mp4",
            "video/matroska",
            "video/x-matroska",
            "video/webm",
            "video/quicktime",
            "video/x-msvideo",
            "video/x-ms-wmv",
            "video/mpeg",
            "video/x-m4v",
            "video/mp2t",
            "video/x-flv",
            "video/3gpp",
            "video/ogg",
            "video/x-ogm+ogg",
            "video/vnd.avi",
            "video/avi",
        }

        # Fallback for mimetype names that keep changing between
        # shared-mime-info releases. Nautilus cannot import the application's
        # modules: this must equal constants.VIDEO_FILE_EXTENSIONS (a test
        # compares them).
        self.supported_extensions = {
            ".mp4",
            ".mkv",
            ".webm",
            ".mov",
            ".avi",
            ".wmv",
            ".mpeg",
            ".mpg",
            ".m4v",
            ".ts",
            ".m2ts",
            ".mts",
            ".flv",
            ".3gp",
            ".ogv",
        }

    def get_file_items(self, *args):
        """One entry when the selection holds supported videos.

        Older Nautilus versions also pass the window; the files come last.
        """
        files = args[-1]
        video_files = [f for f in files if self._is_video_file(f)]
        if not video_files:
            return []

        num_videos = len(video_files)

        if num_videos == 1:
            label = _("Convert Video")
            name = "BigVideoConverter::Convert"
        else:
            label = gettext.dngettext(
                APP_NAME, "Convert {0} Video", "Convert {0} Videos", num_videos
            ).format(num_videos)
            name = "BigVideoConverter::ConvertMultiple"

        menu_item = Nautilus.MenuItem(name=name, label=label)
        menu_item.connect("activate", self._launch_application, video_files)
        return [menu_item]

    def _is_video_file(self, file_info: Nautilus.FileInfo) -> bool:
        """By mimetype, else by extension for a mimetype we do not know."""
        if not file_info or file_info.is_directory():
            return False

        mime_type = file_info.get_mime_type() or ""
        if mime_type in self.supported_mimetypes:
            return True

        uri = file_info.get_uri() or ""
        return Path(unquote(uri)).suffix.lower() in self.supported_extensions

    def _launch_application(
        self, menu_item: Nautilus.MenuItem, files: list[Nautilus.FileInfo]
    ):
        # GIO keeps non-UTF-8 names intact and maps a network location to its
        # gvfs FUSE path when there is one. The application checks that each
        # path exists; a stat here on a slow mount would freeze Files.
        file_paths = [
            path for f in files if (path := f.get_location().get_path()) is not None
        ]

        if not file_paths:
            self._show_error_notification(
                _("No valid local files selected"),
                _("Could not get the path for the selected video files."),
            )
            return

        # GIO reaps the child when it exits, so Files keeps no zombie.
        try:
            Gio.Subprocess.new(
                [self.app_executable, *file_paths],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error as e:
            self._show_error_notification(
                _("Application Launch Error"),
                _("Failed to start Big Video Converter: {0}").format(e.message),
            )

    def _show_error_notification(self, title: str, message: str):
        try:
            Gio.Subprocess.new(
                [
                    "notify-send",
                    "--icon=dialog-error",
                    f"--app-name={APP_NAME}",
                    title,
                    message,
                ],
                Gio.SubprocessFlags.NONE,
            )
        except GLib.Error:
            # Without notify-send the message only reaches Nautilus' log.
            logging.getLogger(__name__).error("%s: %s", title, message)
