"""Parse additional FFmpeg options as data, never as a shell program.

Every option has a known arity. Positional arguments (additional inputs or
outputs) are rejected. The CLI and GUI share this contract. The returned quoted
text is a transport format for shlex, not a command to execute in a shell.
"""

import gettext
import re
import shlex
import sys

_ = gettext.gettext

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


_NO_VALUE_FLAGS = {
    "-copyts", "-start_at_zero", "-shortest", "-vn", "-an", "-sn", "-dn",
    "-ignore_unknown", "-stats", "-nostats", "-hide_banner",
}
# Keep the existing restrictions on arbitrary filter/option expressions. These
# are an application policy, not the defence against shell interpretation.
_FORBIDDEN = re.compile(r"[;&|`$><\n\r\\()]|\x00")
_SPECIFIER = re.compile(r"^(-[A-Za-z0-9_\-]+)(:[A-Za-z0-9_:.]+)?$")
_NEGATIVE_NUMBER = re.compile(r"^-\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def _base_flag(token: str) -> str:
    match = _SPECIFIER.fullmatch(token)
    return match.group(1) if match else token


def parse_additional_options(text: str) -> list[str]:
    """Return argv tokens, rejecting incomplete options and extra filenames."""
    if not isinstance(text, str) or len(text) > 65536:
        raise ValueError(_("Additional options could not be parsed: {0}").format("invalid text"))
    tokens = shlex.split(text)
    i = 0
    while i < len(tokens):
        flag = tokens[i]
        base = _base_flag(flag)
        if base not in ALLOWED_FFMPEG_FLAGS:
            raise ValueError(_(
                "The FFmpeg option “{0}” is not allowed. Remove it from the "
                "additional options to continue."
            ).format(flag))
        if _FORBIDDEN.search(flag):
            raise ValueError(_("Additional options contain characters that are not allowed: {0}").format(flag))
        i += 1
        if base in _NO_VALUE_FLAGS:
            continue
        if i == len(tokens):
            raise ValueError(_("Additional options could not be parsed: {0}").format(flag))
        value = tokens[i]
        if not value or _FORBIDDEN.search(value):
            raise ValueError(_("Additional options contain characters that are not allowed: {0}").format(value))
        if value.startswith("-") and not _is_negative_number(value):
            negative_map = base == "-map" and re.fullmatch(r"-\d+(?::[A-Za-z0-9_:]+)?\??", value)
            negative_disposition = base == "-disposition" and re.fullmatch(r"-[A-Za-z_]+", value)
            if not negative_map and not negative_disposition:
                raise ValueError(_("Additional options could not be parsed: {0}").format(flag))
        i += 1
    return tokens


def validate_additional_options(text: str):
    """Return (accepted, normalized option text or localized error)."""
    try:
        return True, shlex.join(parse_additional_options(text or ""))
    except ValueError as error:
        return False, str(error)


def _is_negative_number(token: str) -> bool:
    return bool(_NEGATIVE_NUMBER.fullmatch(token))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--null":
        sys.exit("Usage: ffmpeg_options.py --null OPTIONS")
    try:
        argv = parse_additional_options(sys.argv[2])
    except ValueError as error:
        sys.exit(str(error))
    for token in argv:
        sys.stdout.buffer.write(token.encode("utf-8") + b"\0")
