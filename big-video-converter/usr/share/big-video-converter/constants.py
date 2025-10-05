"""
Constants for the Big Video Converter application.
Global settings, paths, and configuration values.
"""

import os

# Application metadata
APP_ID = "br.com.biglinux.converter"
APP_NAME = "Big Video Converter"
APP_VERSION = "3.0.0"

APP_DEVELOPERS = ["Tales A. Mendonça", "Bruno Gonçalves Araujo"]
APP_WEBSITES = ["communitybig.org", "biglinux.com.br"]

# Paths to executables
# Detect if running from AppImage or system install
if 'APPIMAGE' in os.environ or 'APPDIR' in os.environ:
    # Running from AppImage
    # constants.py is in: usr/share/big-video-converter/constants.py
    # Script is in: usr/bin/big-video-converter
    # Need to go up to AppImage root and then to usr/bin
    script_dir = os.path.dirname(os.path.abspath(__file__))  # usr/share/big-video-converter
    usr_dir = os.path.dirname(os.path.dirname(script_dir))   # usr
    appimage_root = os.path.dirname(usr_dir)                  # AppImage root
    CONVERT_SCRIPT_PATH = os.path.join(appimage_root, 'usr', 'bin', 'big-video-converter')
elif os.path.exists('/usr/bin/big-video-converter'):
    # System install
    CONVERT_SCRIPT_PATH = '/usr/bin/big-video-converter'
else:
    # Development/local
    CONVERT_SCRIPT_PATH = './big-video-converter'

# UI constants
WINDOW_DEFAULT_WIDTH = 900
WINDOW_DEFAULT_HEIGHT = 620
CONTENT_MAX_WIDTH = 800
CONTENT_TIGHTENING_THRESHOLD = 600

# File dialog filters
VIDEO_FILE_MIME_TYPES = [
    "video/mp4",
    "video/x-matroska",
    "video/x-msvideo",
    "video/quicktime",
    "video/webm",
    "video/x-flv",
    "video/mpeg",
    "video/3gpp",
    "video/x-ms-wmv",
    "video/ogg",
    "video/mp2t",
]

# Encoding options
GPU_OPTIONS = [
    "Auto-detect",
    "NVENC (Nvidia)",
    "VAAPI (Intel/AMD)",
    "QSV (Intel)",
    "Vulkan",
    "Software (CPU)"
]
VIDEO_QUALITY_OPTIONS = [
    "Default",
    "veryhigh",
    "high",
    "medium",
    "low",
    "verylow",
    "superlow",
]
VIDEO_CODEC_OPTIONS = [
    "Default (h264)",
    "h265 (HEVC)",
    "av1 (AV1)",
    "vp9 (VP9)",
]
PRESET_OPTIONS = [
    "Default",
    "ultrafast",
    "veryfast",
    "faster",
    "medium",
    "slow",
    "veryslow",
]
SUBTITLE_OPTIONS = [
    "extract (SRT)",
    "embedded",
    "none"
]
AUDIO_OPTIONS = [
    "copy",
    "reencode", 
    "none"
]
