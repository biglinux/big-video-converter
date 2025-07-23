#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
gi.require_version('Nautilus', '4.0')

from gi.repository import GObject, Nautilus

# --- Internationalization (i18n) Setup ---
APP_NAME = "big-video-converter"

try:
    # Set the default domain for this script. gettext will automatically find
    # the message catalogs in the system's standard locale directories.
    gettext.textdomain(APP_NAME)
except Exception as e:
    print(f"Big Video Converter Extension: Could not set up localization: {e}")

# Define the global translation function.
_ = gettext.gettext


class BigVideoConverterExtension(GObject.GObject, Nautilus.MenuProvider):
    """
    Provides the context menu items for Nautilus to allow video conversion.
    """

    def __init__(self):
        """Initializes the extension."""
        super().__init__()
        self.app_executable = 'big-video-converter-gui'

        # Using a set provides O(1) lookup time, which is more efficient than a list.
        self.supported_mimetypes = {
            'video/mp4', 'video/x-matroska', 'video/webm', 'video/quicktime',
            'video/x-msvideo', 'video/x-ms-wmv', 'video/mpeg', 'video/x-m4v',
            'video/mp2t', 'video/x-flv', 'video/3gpp', 'video/ogg'
        }

    def get_file_items(self, files: list[Nautilus.FileInfo]) -> list[Nautilus.MenuItem]:
        """
        Returns menu items for the selected files.
        The menu is only shown if one or more supported video files are selected.
        """
        video_files = [f for f in files if self._is_video_file(f)]
        if not video_files:
            return []

        num_videos = len(video_files)

        # Define the label based on the number of selected files.
        if num_videos == 1:
            label = _('Convert Video')
            name = 'BigVideoConverter::Convert'
        else:
            label = _('Convert {0} Videos').format(num_videos)
            name = 'BigVideoConverter::ConvertMultiple'

        menu_item = Nautilus.MenuItem(name=name, label=label)
        menu_item.connect('activate', self._launch_application, video_files)
        return [menu_item]

    def _is_video_file(self, file_info: Nautilus.FileInfo) -> bool:
        """
        Checks if a file is a supported video by its mimetype.
        """
        if not file_info or file_info.is_directory():
            return False

        return file_info.get_mime_type() in self.supported_mimetypes

    def _get_file_path(self, file_info: Nautilus.FileInfo) -> str | None:
        """
        Gets the local file path from a Nautilus.FileInfo object by parsing its URI.
        """
        uri = file_info.get_uri()
        if not uri.startswith('file://'):
            return None
        # Decode URL-encoded characters (e.g., %20 -> space) and remove the prefix.
        return unquote(uri[7:])

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
        except Exception as e:
            print(f"Error launching '{self.app_executable}': {e}")
            self._show_error_notification(
                _("Application Launch Error"),
                _("Failed to start Big Video Converter: {0}").format(str(e))
            )

    def _show_error_notification(self, title: str, message: str):
        """
        Displays a desktop error notification using 'notify-send'.
        """
        try:
            subprocess.run([
                'notify-send',
                '--icon=dialog-error',
                f'--app-name={APP_NAME}',
                title,
                message
            ], check=False)
        except FileNotFoundError:
            # Fallback if 'notify-send' is not installed.
            print(f"ERROR: [{title}] {message}")
