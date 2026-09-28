"""Parse additional FFmpeg options as data, never as a shell program.

Every option has a known arity. Positional arguments (additional inputs or
outputs) are rejected. The CLI and GUI share this contract. The returned quoted
text is a transport format for shlex, not a command to execute in a shell.
Any filter FFmpeg has is accepted; file_access_settings() lists the settings
that reach outside the conversion, so imported text can be confirmed first.
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
    # Encoder private options a preset may pin (libx264/x265, NVENC, QSV,
    # VAAPI, SVT-AV1, libvpx, ProRes). None of them names a file or a device.
    "-coder", "-qmin", "-qmax", "-keyint_min", "-sc_threshold", "-b_strategy",
    "-trellis", "-subq", "-me_range", "-aq-strength", "-psy-rd", "-partitions",
    "-weightp", "-mbtree", "-qcomp", "-deblock", "-x264opts", "-slices",
    "-rc-lookahead", "-temporal-aq", "-spatial-aq", "-b_ref_mode", "-multipass",
    "-forced-idr", "-zerolatency", "-a53cc", "-intra-refresh", "-nal-hrd",
    "-extbrc", "-mbbrc", "-adaptive_i", "-adaptive_b", "-low_delay_brc",
    "-async_depth", "-compression_level", "-quality", "-deadline",
    "-tile-columns", "-tile-rows", "-lag-in-frames", "-auto-alt-ref",
    "-tune-content", "-vendor", "-bits_per_mb",
    "-color_primaries", "-color_trc", "-colorspace", "-color_range",
    "-video_track_timescale", "-frag_duration", "-min_frag_duration",
    # Video / audio format
    "-pix_fmt", "-r", "-fps_mode", "-aspect", "-s",
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
    "-err_detect", "-hwaccel", "-hwaccel_output_format", "-filter_hw_device",
    "-frames", "-vframes", "-aframes",
}


_NO_VALUE_FLAGS = {
    "-copyts", "-start_at_zero", "-shortest", "-vn", "-an", "-sn", "-dn",
    "-ignore_unknown", "-stats", "-nostats", "-hide_banner",
}
# Keep the existing restrictions on arbitrary filter/option expressions. These
# are an application policy, not the defence against shell interpretation.
_FORBIDDEN = re.compile(r"[;&|`$><\n\r\\()]|\x00")
_FILTERGRAPH_FLAGS = {"-vf", "-af", "-filter", "-filter_complex", "-lavfi"}
# Settings that may read or write files, load code or reach the network. They
# are legitimate in the user's own options, so this only drives a confirmation
# when someone else's text (an imported preset or profile) carries them.
# Being generous costs a question, never a conversion.
_FILE_ACCESS_FILTERS = {
    "movie", "amovie", "subtitles", "ass", "lut1d", "lut3d", "ladspa", "lv2",
    "frei0r", "frei0r_src", "vidstabdetect", "vidstabtransform", "libvmaf",
    "vmafmotion", "sofalizer", "lensfun", "sendcmd", "asendcmd", "zmq", "azmq",
    "removelogo", "find_rect", "cover_rect", "arnndn", "sr", "derain",
    "dnn_processing", "dnn_detect", "dnn_classify", "ocr", "asr", "flite",
    "whisper", "signature", "program_opencl", "openclsrc",
}
# Filters whose file option a positional (unnamed) value can land on.
_POSITIONAL_FILE_FILTERS = {
    "drawtext", "curves", "deshake", "psnr", "ssim", "xpsnr", "metadata",
    "ametadata", "firequalizer", "libplacebo",
}
_FILE_KEYS = {
    "result", "input", "sofa", "model", "plot", "object", "cover", "shader",
    "destination", "fontsdir",
}
_FILTER_NAME = re.compile(r"([A-Za-z0-9_]+)(?:@[A-Za-z0-9_]+)?")
_LABELS = re.compile(r"^(?:\s*\[[^\]]*\])*\s*|(?:\s*\[[^\]]*\])*\s*$")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S")
# libx264/libx265 read these from, or write them to, a named file (or, for
# "pass", a default log in the working directory). x264 and x265 treat "_" as
# "-" and accept a "no-" prefix, so keys are compared after that folding.
# SVT-AV1's "film-grain" is a strength, not the x265 grain file.
_ENCODER_PARAM_FLAGS = {"-x264-params", "-x264opts", "-x265-params", "-svtav1-params"}
_FILE_ENCODER_PARAMS = {
    "stats", "pass", "qpfile", "cqmfile", "dump-yuv", "opencl-clbin", "recon",
    "csv", "csv-log-level", "zonefile", "analysis-load", "analysis-save",
    "analysis-reuse-file", "lambda-file", "scaling-list", "dhdr10-info",
    "film-grain", "aom-film-grain", "dolby-vision-rpu", "nalu-file",
    "fovea-gaze-file",
}
_FILE_SVTAV1_PARAMS = {"stats", "pass", "fgs-table"}
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


def file_access_settings(text: str) -> list[str]:
    """The settings in valid option text that may reach files or the network.

    Each filter is returned whole, each encoder parameter as key=value.
    FFmpeg splits a graph at commas and an argument list at colons outside
    single quotes, then drops the quotes; backslashes and semicolons never get
    this far (_FORBIDDEN).
    """
    tokens = parse_additional_options(text)
    found = []
    i = 0
    while i < len(tokens):
        base = _base_flag(tokens[i])
        if base in _NO_VALUE_FLAGS:
            i += 1
            continue
        value = tokens[i + 1]
        i += 2
        if base in _FILTERGRAPH_FLAGS:
            found += [f for f in _split(value, ",") if _filter_reaches_out(_LABELS.sub("", f))]
        elif base in _ENCODER_PARAM_FLAGS:
            listed = _FILE_SVTAV1_PARAMS if base == "-svtav1-params" else _FILE_ENCODER_PARAMS
            for item in value.replace("'", "").replace('"', "").split(":"):
                key, _sep, option = item.partition("=")
                key = re.sub(r"^no-?", "", key.strip().lower().replace("_", "-"))
                if key in listed or _names_file(key) or _reaches_out(option):
                    found.append(item)
    return list(dict.fromkeys(f.strip() for f in found))


def _filter_reaches_out(text: str) -> bool:
    name, args = (_split(text, "=", 1) + [""])[:2]
    match = _FILTER_NAME.fullmatch(_unquote(name))
    if not match or match[1] in _FILE_ACCESS_FILTERS:
        return True
    for item in _split(args, ":") if args else ():
        parts = _split(item, "=", 1)
        if len(parts) == 1:
            if match[1] in _POSITIONAL_FILE_FILTERS or _reaches_out(item):
                return True
        elif _names_file(_unquote(parts[0])) or _reaches_out(parts[1]):
            return True
    return False


def _split(text: str, separator: str, limit: int = -1) -> list[str]:
    """Split at separators outside single quotes, keeping the quotes."""
    parts, quoted, start = [], False, 0
    for index, char in enumerate(text):
        if char == "'":
            quoted = not quoted
        elif char == separator and not quoted and limit != len(parts):
            parts.append(text[start:index])
            start = index + 1
    return parts + [text[start:]]


def _unquote(text: str) -> str:
    return text.replace("'", "").strip()


def _names_file(key: str) -> bool:
    """qpfile, stats_file, filename, db_path, result... but not profile."""
    key = key.lower()
    return (key.startswith("/") or key in _FILE_KEYS
            or bool(re.search(r"file|path", key.replace("profile", ""))))


def _reaches_out(value: str) -> bool:
    """An absolute, home, parent or URL-like location such as file: or http:."""
    value = _unquote(value)
    return value.startswith(("/", "~")) or ".." in value or bool(_URL.match(value))


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
