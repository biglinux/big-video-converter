"""
Constants for the Big Video Converter application.
Global settings, paths, and configuration values.
"""

import os
import gettext

_ = gettext.gettext

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
WINDOW_DEFAULT_WIDTH = 1000
WINDOW_DEFAULT_HEIGHT = 620
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

# Encoding options - User-friendly display names
GPU_OPTIONS = [
    _("Auto-detect"),
    _("NVENC (Nvidia)"),
    _("VAAPI (Intel/AMD)"),
    _("QSV (Intel)"),
    _("Vulkan"),
    _("Software (CPU)")
]

# Internal values mapping for GPU options
GPU_VALUES = {0: "auto", 1: "nvidia", 2: "amd", 3: "intel", 4: "vulkan", 5: "software"}

# User-friendly quality display names
VIDEO_QUALITY_OPTIONS = [
    _("Default"),
    _("Very High"),
    _("High"),
    _("Medium"),
    _("Low"),
    _("Very Low"),
    _("Super Low"),
]

# Internal values for ffmpeg
VIDEO_QUALITY_VALUES = {
    0: "default",
    1: "veryhigh",
    2: "high",
    3: "medium",
    4: "low",
    5: "verylow",
    6: "superlow",
}

# User-friendly codec display names
VIDEO_CODEC_OPTIONS = [
    _("H.264 (Default)"),
    _("H.265 (HEVC)"),
    _("AV1"),
    _("VP9"),
]

# Internal codec values for ffmpeg
VIDEO_CODEC_VALUES = {0: "h264", 1: "h265", 2: "av1", 3: "vp9"}

# User-friendly preset names
PRESET_OPTIONS = [
    _("Default"),
    _("Ultra Fast"),
    _("Very Fast"),
    _("Faster"),
    _("Medium"),
    _("Slow"),
    _("Very Slow"),
]

# Internal preset values for ffmpeg
PRESET_VALUES = {
    0: "default",
    1: "ultrafast",
    2: "veryfast",
    3: "faster",
    4: "medium",
    5: "slow",
    6: "veryslow",
}

# User-friendly subtitle options
SUBTITLE_OPTIONS = [_("Extract to SRT"), _("Keep Embedded"), _("Remove")]

# Internal subtitle values for ffmpeg
SUBTITLE_VALUES = {0: "extract", 1: "embedded", 2: "none"}

# User-friendly audio handling options
AUDIO_OPTIONS = [_("Copy (No Re-encoding)"), _("Re-encode"), _("Remove Audio")]

# Internal audio values for ffmpeg
AUDIO_VALUES = {0: "copy", 1: "reencode", 2: "none"}

# Audio codec options for re-encoding (user-friendly names)
AUDIO_CODEC_OPTIONS = [
    _("AAC (Default)"),
    _("Opus (Best Quality)"),
    _("AC3 (Dolby Digital)"),
]

# Internal audio codec values for ffmpeg
AUDIO_CODEC_VALUES = {0: "aac", 1: "opus", 2: "ac3"}

# Video resolution options (user-friendly names)
VIDEO_RESOLUTION_OPTIONS = [
    _("Default (Original)"),
    _("4K UHD (3840×2160)"),
    _("2K QHD (2560×1440)"),
    _("Full HD (1920×1080)"),
    _("HD (1280×720)"),
    _("SD (854×480)"),
    _("4K UHD Vertical (2160×3840)"),
    _("2K QHD Vertical (1440×2560)"),
    _("Full HD Vertical (1080×1920)"),
    _("HD Vertical (720×1280)"),
    _("SD Vertical (480×854)"),
    _("Custom"),
]

# Internal resolution values for ffmpeg
VIDEO_RESOLUTION_VALUES = {
    0: "",  # Default (original)
    1: "3840x2160",  # 4K UHD
    2: "2560x1440",  # 2K QHD
    3: "1920x1080",  # Full HD
    4: "1280x720",  # HD
    5: "854x480",  # SD
    6: "2160x3840",  # 4K UHD Vertical
    7: "1440x2560",  # 2K QHD Vertical
    8: "1080x1920",  # Full HD Vertical
    9: "720x1280",  # HD Vertical
    10: "480x854",  # SD Vertical
    11: "custom",  # Custom
}

# Beginner-friendly tooltips for all UI elements
def get_tooltips():
    """Return translated tooltips dictionary"""
    return {
        # Sidebar options
        "gpu": _(
            "Choose which hardware accelerates video processing:\n• Auto-detect: Automatically uses the best available\n• NVENC: For Nvidia graphics cards (fastest)\n• VAAPI: For Intel/AMD graphics\n• QSV: For Intel processors\n• Vulkan: Cross-platform GPU acceleration\n• Software: Uses CPU only (slowest but compatible)"
        ),
        "video_quality": _(
            "Controls how compressed the video will be:\n• Very High/High: Best quality, larger file size\n• Medium: Good balance between quality and size\n• Low/Very Low: Smaller files, lower quality\nHigher quality = slower processing + bigger files"
        ),
        "video_codec": _(
            "The format used to compress video:\n• H.264: Most compatible, works everywhere\n• H.265 (HEVC): Better compression, newer devices\n• AV1: Best compression, requires modern hardware\n• VP9: Good for web, used by YouTube"
        ),
        "audio_handling": _(
            "What to do with the audio:\n• Copy: Keep audio unchanged (fast)\n• Re-encode: Convert audio to new format\n• Remove: Delete all audio tracks"
        ),
        "subtitles": _(
            "How to handle subtitle tracks:\n• Extract to SRT: Save as separate subtitle file\n• Keep Embedded: Leave inside video file\n• Remove: Delete all subtitles"
        ),
        "force_copy": _(
            "Exclusive option to cut videos into segments or change the container format to MP4 or MKV, with the advantage of no quality loss and very fast processing."
        ),
        # Advanced Settings
        "preset": _(
            "Encoding speed vs file size:\n• Ultra Fast: Quick but larger files\n• Medium: Balanced (recommended)\n• Very Slow: Best compression, takes longer"
        ),
        "audio_codec": _(
            "Audio compression format (when re-encoding):\n• AAC: Most compatible, good quality\n• Opus: Best quality at low bitrates\n• AC3 (Dolby Digital): For home theater systems"
        ),
        "resolution": _(
            "Output video size:\n• Original: Keep the same size\n• Standard formats: 4K, Full HD, HD for TVs/monitors\n• Vertical formats: For smartphones and social media\n• Custom: Specify your own dimensions"
        ),
        "output_format": _(
            "Video container format:\n• MP4: Most compatible\n• MKV: Supports more features"
        ),
        # Video editing
        "brightness": _(
            "Adjust how light or dark the video appears:\n• Move right: Brighter\n• Move left: Darker\n• Default: 0 (no change)"
        ),
        "saturation": _(
            "Control color intensity:\n• Move right: More vivid colors\n• Move left: More gray/washed out\n• Default: 1.0 (no change)"
        ),
        "hue": _(
            "Shift colors:\n• Positive values: Shift toward red/yellow\n• Negative values: Shift toward blue/green\n• Default: 0 (no change)"
        ),
        "crop": _(
            "Remove unwanted areas from video edges:\n• Drag borders to remove parts\n• Useful for removing black bars or unwanted content\n• Does not resize, only cuts"
        ),
        "segments": _(
            "Select the parts you want to keep in the video. You can add sections by clicking the + button or, more easily, by using the marker button directly on the video area.\n You can export the selected segments as a single file or as separate files, which is very convenient, for example, when creating clips for social media."
        ),
        # Advanced Settings - additional options
        "gpu_partial": _(
            "Decodes video using CPU\n• Encodes video using GPU\n• Can be faster on some systems\n• Try if full GPU encoding has issues"
        ),
        "audio_bitrate": _(
            "Audio quality:\n• Higher = better quality, larger file\n• 128k: Good for most content\n• 192k: High quality\n• 256k+: Audiophile quality\n• Default: Let encoder decide"
        ),
        "audio_channels": _(
            "Number of audio channels:\n• 1: Mono (single speaker)\n• 2: Stereo (left/right)\n• 6: 5.1 Surround sound\n• 8: 7.1 Surround sound\n• Default: Keep original"
        ),
        "additional_options": _(
            "Advanced FFmpeg options:\n• Add custom FFmpeg parameters\n• Example: -ss 60 -t 30 (skip 60s, take 30s)\n• For advanced users only\n• Wrong options can cause errors"
        ),
        "extract_subtitles": _(
            "Skips video processing\n• Only extracts subtitle tracks to SRT files\n• Fast operation\n• Useful when you only need subtitles"
        ),
        "show_tooltips": _(
            "• Display explanations when hovering over options\n• This message you are reading is an example of a tooltip"
        ),
        # Header bar buttons
        "back_button": _("Return to file queue"),
        "add_files_button": _("Add video files to the queue for conversion"),
        "clear_queue_button": _("Remove all files from the queue"),
        "convert_all_button": _("Start converting all files in the queue"),
        "convert_current_button": _("Convert this file with current editing settings"),
        "menu_button": _("Open application menu"),
        # File list
        "file_list_item": _("Right-click for more options"),
        "file_list_play_button": _("Preview this video file"),
        "file_list_edit_button": _("Open video editor for this file"),
        "file_list_info_button": _("Show detailed file information"),
        "file_list_remove_button": _("Remove this file from the queue"),
    }
