#!/usr/bin/env python3
"""
Big Video Converter - Nautilus Extension
Global context menu extension for video file conversion.
"""

import os
import subprocess
import locale
from gi.repository import Nautilus, GObject
from urllib.parse import unquote
from pathlib import Path

# Standard gettext setup that xgettext can recognize
import gettext

# Configure translation domain
DOMAIN = "big-video-converter"
LOCALE_DIR = "/usr/share/locale"

try:
    # Set up the translation - compatible with xgettext extraction
    locale.setlocale(locale.LC_ALL, '')
    gettext.bindtextdomain(DOMAIN, LOCALE_DIR)
    gettext.textdomain(DOMAIN)
    _ = gettext.gettext
except Exception as e:
    print(f"Warning: Could not set up translations: {e}")
    # Fallback: no translation
    _ = lambda x: x

class BigVideoConverterExtension(GObject.GObject, Nautilus.MenuProvider):
    """Nautilus extension for Big Video Converter integration"""
    
    def __init__(self):
        super().__init__()
        
        # Supported video MIME types from the .desktop file
        self.supported_mimetypes = {
            'video/mp4',
            'video/x-matroska', 
            'video/webm',
            'video/quicktime',
            'video/x-msvideo',
            'video/x-ms-wmv', 
            'video/mpeg',
            'video/x-m4v',
            'video/mp2t',
            'video/x-flv',
            'video/3gpp',
            'video/ogg'
        }
        
        # Supported file extensions as fallback
        self.supported_extensions = {
            '.mp4', '.mkv', '.webm', '.mov', '.avi', '.wmv', 
            '.mpeg', '.mpg', '.m4v', '.ts', '.flv', '.3gp', 
            '.ogv', '.m2ts', '.mts'
        }
        
        # Application executable
        self.app_executable = 'big-video-converter-gui'
    
    def get_file_items(self, files):
        """
        Return menu items for selected files.
        Only shows items for supported video files.
        """
        if not files:
            return []
        
        # Check if all selected files are supported video files
        video_files = []
        for file_info in files:
            if self.is_video_file(file_info):
                video_files.append(file_info)
        
        # Only show menu if we have video files
        if not video_files:
            return []
        
        menu_items = []
        
        # Main convert action
        if len(video_files) == 1:
            # Single file - show "Convert Video"
            convert_item = Nautilus.MenuItem(
                name='BigVideoConverter::Convert',
                label=_('Convert Video'),
                tip=_('Convert {0} using Big Video Converter').format(os.path.basename(video_files[0].get_name())),
                icon='big-video-converter'
            )
            convert_item.connect('activate', self.convert_video, video_files)
            menu_items.append(convert_item)
            
        else:
            # Multiple files - show "Convert Videos"
            convert_item = Nautilus.MenuItem(
                name='BigVideoConverter::ConvertMultiple',
                label=_('Convert {0} Videos').format(len(video_files)),
                tip=_('Convert {0} video files using Big Video Converter').format(len(video_files)),
                icon='big-video-converter'
            )
            convert_item.connect('activate', self.convert_video, video_files)
            menu_items.append(convert_item)
        
        return menu_items
    
    def get_background_items(self, current_folder):
        """No background menu items"""
        return []
    
    def is_video_file(self, file_info):
        """
        Check if the file is a supported video file.
        Uses both MIME type and file extension for detection.
        """
        if not file_info:
            return False
        
        # Check MIME type first
        mime_type = file_info.get_mime_type()
        if mime_type and mime_type in self.supported_mimetypes:
            return True
        
        # Fallback to file extension
        file_name = file_info.get_name()
        if file_name:
            file_ext = Path(file_name).suffix.lower()
            if file_ext in self.supported_extensions:
                return True
        
        return False
    
    def get_file_path(self, file_info):
        """Get the local file path from Nautilus file info"""
        try:
            file_uri = file_info.get_uri()
            if file_uri.startswith('file://'):
                # Remove file:// prefix and decode URL encoding
                file_path = unquote(file_uri[7:])
                return file_path
            return None
        except Exception as e:
            print(f"Error getting file path: {e}")
            return None
    
    def convert_video(self, menu_item, files):
        """Launch Big Video Converter for conversion"""
        self.launch_application(files)
    
    def launch_application(self, files):
        """
        Launch the Big Video Converter application with the selected files.
        
        Args:
            files: List of Nautilus file info objects
        """
        if not files:
            return
        
        # Check if application is installed
        if not self.is_application_available():
            self.show_error_notification(
                _("Big Video Converter not found"),
                _("Please install the {0} application").format(self.app_executable)
            )
            return
        
        # Get file paths
        file_paths = []
        for file_info in files:
            file_path = self.get_file_path(file_info)
            if file_path and os.path.exists(file_path):
                file_paths.append(file_path)
        
        if not file_paths:
            self.show_error_notification(
                _("No valid files"),
                _("Could not access the selected video files")
            )
            return
        
        try:
            # Build command
            cmd = [self.app_executable] + file_paths
            
            # Launch the application in background
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            
            print(f"Launched {self.app_executable} with {len(file_paths)} file(s)")
            
        except Exception as e:
            print(f"Error launching application: {e}")
            self.show_error_notification(
                _("Launch Error"), 
                _("Failed to launch Big Video Converter: {0}").format(str(e))
            )
    
    def is_application_available(self):
        """Check if the Big Video Converter application is available"""
        try:
            # Check if executable exists in PATH
            result = subprocess.run(
                ['which', self.app_executable],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def show_error_notification(self, title, message):
        """Show error notification using notify-send if available"""
        try:
            subprocess.run([
                'notify-send',
                '--icon=dialog-error',
                '--app-name=Big Video Converter',
                title,
                message
            ], check=False)
        except Exception:
            # If notify-send is not available, just print to console
            print(_("Error: {0} - {1}").format(title, message))


# Extension registration - required for Nautilus to load the extension
def get_extension_types():
    """Return the extension types provided by this module"""
    return [BigVideoConverterExtension]