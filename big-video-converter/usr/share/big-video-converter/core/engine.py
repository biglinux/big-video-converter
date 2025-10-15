"""
Conversion Engine - Core business logic for video conversion.
Handles command preparation, environment setup, and security validation.
"""

import os
import shlex
import tempfile
from utils.video_settings import (
    gstreamer_brightness_to_ffmpeg,
    gstreamer_saturation_to_ffmpeg,
    gstreamer_hue_to_ffmpeg,
)


# Whitelist of safe FFmpeg flags (Phase 2: Security)
ALLOWED_FFMPEG_FLAGS = {
    "-ss",
    "-t",
    "-to",  # Timing
    "-vf",
    "-af",  # Filters
    "-c:v",
    "-c:a",  # Codecs
    "-b:v",
    "-b:a",  # Bitrates
    "-preset",  # Encoding preset
    "-pix_fmt",  # Pixel format
    "-r",  # Frame rate
    "-s",  # Size/resolution
    "-aspect",  # Aspect ratio
    "-map",  # Stream mapping
    "-metadata",  # Metadata
    "-threads",  # Thread count
    "-crf",  # Constant Rate Factor
    "-qp",  # Quantization parameter
}

# Shell metacharacters that should not appear in flag values
DANGEROUS_CHARS = {";", "|", "&", "$", "(", ")", "`", "<", ">", "\n", "\r"}


def format_time_ffmpeg(seconds):
    """Format time in seconds to HH:MM:SS.mmm format for FFmpeg."""
    if seconds is None or seconds < 0:
        return None
    hours = int(seconds) // 3600
    minutes = (int(seconds) % 3600) // 60
    seconds_remainder = int(seconds) % 60
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds_remainder:02d}.{milliseconds:03d}"