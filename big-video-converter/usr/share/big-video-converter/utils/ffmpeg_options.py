"""Validation of user-supplied extra FFmpeg options.

The "Additional options" text is exported as the ``options`` environment
variable and expanded by ``eval`` inside the conversion script, so it has to be
checked before it ever reaches the shell. Two layers:

1. every flag must be on ALLOWED_FFMPEG_FLAGS (stream specifiers such as
   ``:v``, ``:a:1`` are accepted on the flags that take them);
2. the accepted tokens are re-quoted with shlex.quote, so shell metacharacters
   in a value cannot start a new command even if one slips through.
"""

import gettext
import logging
import re
import shlex

logger = logging.getLogger(__name__)

_ = gettext.gettext

# Flags a user may reasonably want to add by hand. Keep the base name here;
# stream specifiers (-c:v, -b:a:1, ...) are stripped before the lookup.
ALLOWED_FFMPEG_FLAGS = {
    # Time / trimming
    "-ss", "-sseof", "-t", "-to", "-itsoffset", "-copyts", "-start_at_zero",
    "-avoid_negative_ts", "-shortest",
    # Codecs and encoder tuning
    "-c", "-codec", "-vcodec", "-acodec", "-scodec",
    "-preset", "-tune", "-profile", "-level", "-crf", "-qp", "-cq",
    "-b", "-minrate", "-maxrate", "-bufsize", "-g", "-bf", "-refs",
    "-rc", "-rc_mode", "-global_quality", "-x264-params", "-x265-params",
    "-svtav1-params", "-cpu-used", "-row-mt", "-aq-mode", "-look_ahead_depth",
    # Video / audio format
    "-pix_fmt", "-r", "-fps_mode", "-vsync", "-aspect", "-s",
    "-ar", "-ac", "-sample_fmt", "-channel_layout",
    # Filters
    "-vf", "-af", "-filter", "-filter:v", "-filter:a", "-filter_complex", "-lavfi",
    # Stream selection / metadata
    "-map", "-map_metadata", "-map_chapters", "-metadata", "-disposition",
    "-vn", "-an", "-sn", "-dn", "-ignore_unknown", "-max_muxing_queue_size",
    # Container
    "-f", "-movflags", "-fflags", "-flags", "-strict", "-brand", "-tag",
    # Threads / misc
    "-threads", "-filter_threads", "-thread_queue_size", "-loglevel",
    "-stats", "-nostats", "-hide_banner", "-probesize", "-analyzeduration",
    "-err_detect", "-hwaccel", "-hwaccel_output_format", "-init_hw_device",
    "-filter_hw_device", "-frames", "-vframes", "-aframes",
}

# Characters that must never reach the shell inside a value.
_FORBIDDEN = re.compile(r"[;&|`$><\n\r\\]|\$\(|\)|\(")

# Trailing stream specifier: -c:v, -b:a:1, -disposition:s:0 ...
_SPECIFIER = re.compile(r"^(-[A-Za-z0-9_\-]+?)(:[A-Za-z0-9_:.]+)?$")


def _base_flag(token: str) -> str:
    """Strip a trailing stream specifier from a flag."""
    match = _SPECIFIER.match(token)
    if not match:
        return token
    return match.group(1)


def validate_additional_options(text: str):
    """Check user-supplied FFmpeg options.

    Returns (ok, value): on success value is the sanitized, shell-quoted
    option string ready to be exported; on failure it is an error message
    suitable for showing to the user.
    """
    if not text or not text.strip():
        return True, ""

    try:
        tokens = shlex.split(text)
    except ValueError as e:
        return False, _("Additional options could not be parsed: {0}").format(e)

    for token in tokens:
        if _FORBIDDEN.search(token):
            return False, _(
                "Additional options contain characters that are not allowed: {0}"
            ).format(token)

        if token.startswith("-") and len(token) > 1 and not _is_negative_number(token):
            if _base_flag(token) not in ALLOWED_FFMPEG_FLAGS:
                return False, _(
                    "The FFmpeg option “{0}” is not allowed. Remove it from the "
                    "additional options to continue."
                ).format(token)

    sanitized = " ".join(shlex.quote(token) for token in tokens)
    logger.debug(f"Additional options accepted: {sanitized}")
    return True, sanitized


def _is_negative_number(token: str) -> bool:
    """True for values like -1 or -0.5, which are values and not flags."""
    try:
        float(token)
    except ValueError:
        return False
    return True
