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
def get_gpu_options():
    """Return translated GPU options"""
    return [
        _("Auto-detect"),
        _("NVENC (Nvidia)"),
        _("VAAPI (Intel/AMD)"),
        _("QSV (Intel)"),
        _("Vulkan"),
        _("Software (CPU)")
    ]

GPU_OPTIONS = get_gpu_options()  # Default initialization

# Internal values mapping for GPU options
GPU_VALUES = {0: "auto", 1: "nvidia", 2: "amd", 3: "intel", 4: "vulkan", 5: "software"}

# User-friendly quality display names
def get_video_quality_options():
    """Return translated video quality options"""
    return [
        _("Default"),
        _("Very High"),
        _("High"),
        _("Medium"),
        _("Low"),
        _("Very Low"),
        _("Super Low"),
    ]

VIDEO_QUALITY_OPTIONS = get_video_quality_options()  # Default initialization

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
def get_video_codec_options():
    """Return translated video codec options"""
    return [
        _("H.264 (Default)"),
        _("H.265 (HEVC)"),
        _("AV1"),
        _("VP9"),
    ]

VIDEO_CODEC_OPTIONS = get_video_codec_options()  # Default initialization

# Internal codec values for ffmpeg
VIDEO_CODEC_VALUES = {0: "h264", 1: "h265", 2: "av1", 3: "vp9"}

# User-friendly preset names
def get_preset_options():
    """Return translated preset options"""
    return [
        _("Default"),
        _("Ultra Fast"),
        _("Very Fast"),
        _("Faster"),
        _("Medium"),
        _("Slow"),
        _("Very Slow"),
    ]

PRESET_OPTIONS = get_preset_options()  # Default initialization

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
def get_subtitle_options():
    """Return translated subtitle options"""
    return [_("Extract to SRT"), _("Keep Embedded"), _("Remove")]

SUBTITLE_OPTIONS = get_subtitle_options()  # Default initialization

# Internal subtitle values for ffmpeg
SUBTITLE_VALUES = {0: "extract", 1: "embedded", 2: "none"}

# User-friendly audio handling options
def get_audio_options():
    """Return translated audio options"""
    return [_("Copy (No Re-encoding)"), _("Re-encode"), _("Remove Audio")]

AUDIO_OPTIONS = get_audio_options()  # Default initialization

# Internal audio values for ffmpeg
AUDIO_VALUES = {0: "copy", 1: "reencode", 2: "none"}

# Audio codec options for re-encoding (user-friendly names)
def get_audio_codec_options():
    """Return translated audio codec options"""
    return [
        _("AAC (Default)"),
        _("Opus (Best Quality)"),
        _("AC3 (Dolby Digital)"),
    ]

AUDIO_CODEC_OPTIONS = get_audio_codec_options()  # Default initialization

# Internal audio codec values for ffmpeg
AUDIO_CODEC_VALUES = {0: "aac", 1: "opus", 2: "ac3"}

# Video resolution options (user-friendly names)
def get_video_resolution_options():
    """Return translated video resolution options"""
    return [
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

VIDEO_RESOLUTION_OPTIONS = get_video_resolution_options()  # Default initialization

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
            "Automatic (Recommended). If your graphics card is supported, conversion is much faster. "
            "If not, the app will use Software mode automatically. "
            "You can also pick GPU or Software manually."
        ),
        "video_quality": _(
            "Higher quality means a larger file."
        ),
        "video_codec": _(
            "H.264: Most compatible; fast to convert; larger files.\n"
            "H.265: Smaller files; may not play on older devices.\n"
            "VP9: Good for YouTube; can be slow without GPU support.\n"
            "AV1: Smallest files; may be slow and not supported everywhere."
        ),
        "audio_handling": _(
            "Keep original (Recommended): fastest and keeps full quality. "
            "Convert: slightly lower quality, better compatibility, often smaller file."
        ),
        "subtitles": _(
            "If subtitles are embedded, keeping them is usually best. "
            "Some players need an external subtitle file, which can improve compatibility."
        ),
        "force_copy": _(
            "Copy video (very fast, full quality). "
            "Great for quick cuts and trims for social media."
        ),
        # Advanced Settings
        "preset": _(
            "Faster = bigger file. Slower = smaller file. "
            "Choose what matters most: speed or size."
        ),
        "audio_codec": _(
            "Audio format (when converting):\n"
            "• AAC: Most compatible, good quality\n"
            "• Opus: Excellent quality at small sizes\n"
            "• AC3 (Dolby Digital): Good for home theater"
        ),
        "resolution": _(
            "Output size:\n"
            "• Original: Keep the same resolution\n"
            "• Standard: 4K, Full HD, HD (TVs and monitors)\n"
            "• Vertical: For phones and social media\n"
            "• Custom: Set your own dimensions"
        ),
        "output_format": _(
            "Container format:\n"
            "• MP4: Most compatible\n"
            "• MKV: More features"
        ),
        # Video editing
        "brightness": _(
            "Adjust brightness:\n"
            "• Move right: Brighter\n"
            "• Move left: Darker\n"
            "• Default: 0 (no change)"
        ),
        "saturation": _(
            "Adjust color intensity:\n"
            "• Move right: More vivid\n"
            "• Move left: More muted\n"
            "• Default: 1.0 (no change)"
        ),
        "hue": _(
            "Shift overall color tone:\n"
            "• Positive: More red/yellow\n"
            "• Negative: More blue/green\n"
            "• Default: 0 (no change)"
        ),
        "crop": _("Crop the video's edges to keep only the part you need."),
        "segments": _(
            "Choose the parts of the video to keep. Add sections with the + button or place markers on the player area.\nExport the selected parts as one file or as separate clips — great for social media."
        ),
        # Advanced Settings - additional options
        "gpu_partial": _(
            "When using GPU acceleration, this option forces the encoder to be on the GPU and the encode to be on the CPU. It's usually better to leave this unchecked, but it can be useful for compatibility issues."
        ),
        "audio_bitrate": _(
            "Audio quality:\n"
            "• Higher = better quality, larger file\n"
            "• 128k: Good for most content\n"
            "• 192k: High quality\n"
            "• 256k+: Professional use\n"
            "• Default: Auto (encoder decides)"
        ),
        "audio_channels": _(
            "Number of audio channels:\n"
            "• 1: Mono (single speaker)\n"
            "• 2: Stereo (left/right)\n"
            "• 6: 5.1 Surround\n"
            "• 8: 7.1 Surround\n"
            "• Default: Keep original"
        ),
        "additional_options": _(
            "Advanced: Custom FFmpeg options.\n"
            "• For experienced users only\n"
            "• Invalid options may cause errors\n"
            "• Example: -ss 60 -t 30 (skip 60s, take 30s)"
        ),
        "extract_subtitles": _(
            "Skips all remaining processes and just exports the embedded subtitles as an SRT file."
        ),
        "show_tooltips": _(
            "You’re seeing an example of help shown when hovering over an item."
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


def refresh_translations():
    """Refresh all translated constants after gettext is properly initialized"""
    global GPU_OPTIONS, VIDEO_QUALITY_OPTIONS, VIDEO_CODEC_OPTIONS, PRESET_OPTIONS
    global SUBTITLE_OPTIONS, AUDIO_OPTIONS, AUDIO_CODEC_OPTIONS, VIDEO_RESOLUTION_OPTIONS
    
    GPU_OPTIONS = get_gpu_options()
    VIDEO_QUALITY_OPTIONS = get_video_quality_options()
    VIDEO_CODEC_OPTIONS = get_video_codec_options()
    PRESET_OPTIONS = get_preset_options()
    SUBTITLE_OPTIONS = get_subtitle_options()
    AUDIO_OPTIONS = get_audio_options()
    AUDIO_CODEC_OPTIONS = get_audio_codec_options()
    VIDEO_RESOLUTION_OPTIONS = get_video_resolution_options()
