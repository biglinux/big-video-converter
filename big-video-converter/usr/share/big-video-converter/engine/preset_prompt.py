"""The prompt that asks a chat assistant to write a preset file."""

from __future__ import annotations

import locale
import os

from engine.presets import KNOWN_ENCODERS

_LANGUAGE_NAMES = {
    "pt": "Portuguese",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ru": "Russian",
    "pl": "Polish",
    "nl": "Dutch",
    "tr": "Turkish",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
    "uk": "Ukrainian",
    "cs": "Czech",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "el": "Greek",
    "he": "Hebrew",
    "hu": "Hungarian",
    "ro": "Romanian",
    "bg": "Bulgarian",
    "et": "Estonian",
    "id": "Indonesian",
    "vi": "Vietnamese",
    "th": "Thai",
    "ca": "Catalan",
}


def system_language() -> str:
    """The language the desktop runs in, e.g. ``pt_BR (Portuguese)``."""
    code = ""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var, "")
        if value and value not in ("C", "POSIX", "C.UTF-8"):
            code = value.split(":")[0]
            break
    if not code:
        try:
            code = locale.getlocale()[0] or ""
        except ValueError:
            code = ""
    code = code.split(".")[0].split("@")[0]
    if not code or code in ("C", "POSIX"):
        return "en_US (English)"
    name = _LANGUAGE_NAMES.get(code.split("_")[0].lower())
    return f"{code} ({name})" if name else code


PROMPT_TEMPLATE = """You are helping a user of Big Video Converter, a Linux GTK front end for FFmpeg.
Write ONE preset file in TOML that fulfils the request at the end of this message.

Rules
- Reply in this language: {language}. Write [preset] name, description and tags in that language too. Keep explanations short; the preset itself is what matters.
- Return the preset as a single ```toml code block. Do not add keys or sections that are not listed here.
- The program already detects the GPU, falls back to CPU decoding or CPU encoding when needed, converts 10-bit sources to 8-bit for 8-bit encoders, tone maps HDR to SDR and handles subtitles. Do NOT try to do those things in the preset.
- Do not add the target filename, "-i", input files or any other positional argument.
- Prefer the structured fields. Use [encoder.<name>] args only for options the structured fields cannot express (for example "-tune film" or "-x264-params keyint=60"). Those args are appended after the program's own options, so FFmpeg's last-one-wins rule applies.

Format (every key is optional except [preset] name; omit what the request does not need)
```toml
format = 1

[preset]
name = "Short name shown in the interface"
description = "One or two sentences: what it is for and what it trades off."
tags = ["youtube", "1080p"]          # short words for search

[video]
codec = "h264"           # copy | h264 | h265 | av1 | vp9 | prores  ("copy" keeps the video stream untouched)
quality = "high"         # default | veryhigh | high | medium | low | verylow | superlow  (CRF/CQ ladder chosen by the program)
speed = "slow"           # default | ultrafast | veryfast | faster | medium | slow | veryslow
resolution = "1920x1080" # WxH; omit to keep the source size
fps = 30                 # omit to keep the source frame rate
pixel_format = "yuv420p" # applied to CPU encoders only; omit unless the target platform demands one
gpu = "auto"             # auto | software | nvidia | amd | intel | vulkan; "software" forces CPU encoding

[audio]
mode = "reencode"        # copy | reencode | none
codec = "aac"            # aac | opus | ac3
bitrate = "192k"
channels = 2
sample_rate = 48000
normalize = false        # loudness normalization to broadcast/streaming levels

[subtitles]
mode = "embedded"        # extract (to .srt files) | embedded (inside the container) | none

[container]
format = "mp4"           # mp4 | mkv | mov | webm

[ffmpeg]
output_options = "-g 60 -bf 2"   # extra FFmpeg output options, only from this list: {allowed_flags}

[encoder.libx264]                # one table per encoder; only the encoder actually used gets its args
args = ["-tune", "film"]

[size]
target = "telegram"      # optional largest file: {size_targets}
```

Encoders the program may pick, depending on the machine: {encoders}.
FFmpeg encoders available on this machine right now: {available}.
FFmpeg version on this machine: {ffmpeg_version}.

Request from the user:
{request}
"""


def build_ai_prompt(
    request: str,
    *,
    available_encoders=(),
    ffmpeg_version: str = "unknown",
    language: str | None = None,
) -> str:
    from engine.ffmpeg_options import ALLOWED_FFMPEG_FLAGS
    from engine.size_target import TARGETS

    request = (
        (request or "").strip()
        or "(the user did not describe the request; ask them what the preset is for)"
    )
    available = (
        ", ".join(sorted(e for e in available_encoders if e in KNOWN_ENCODERS))
        or "unknown"
    )
    return PROMPT_TEMPLATE.format(
        language=language or system_language(),
        allowed_flags=" ".join(sorted(ALLOWED_FFMPEG_FLAGS)),
        encoders=", ".join(KNOWN_ENCODERS),
        size_targets=", ".join(t[0] for t in TARGETS),
        available=available,
        ffmpeg_version=ffmpeg_version,
        request=request,
    )
