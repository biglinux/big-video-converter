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
