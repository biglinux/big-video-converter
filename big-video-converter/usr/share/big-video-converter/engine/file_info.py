import gettext
import json
import logging
import math
import os
import re
import subprocess
import threading

from engine.ffmpeg_path import get_ffmpeg_executable, get_ffprobe_executable

logger = logging.getLogger(__name__)

_ = gettext.gettext


# Probe answers the queue needs before launching a job, keyed by the file's
# identity (path, size, mtime) so an edited file is never answered from stale
# data. The queue warms these entries on a worker thread; the launch, which
# has to run on the GTK main loop, then finds them ready instead of blocking
# the interface on ffprobe for up to ten seconds per call.
_PROBE_CACHE: dict = {}
_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_CACHE_LIMIT = 64


def _file_identity(file_path: str):
    try:
        st = os.stat(file_path)
    except OSError:
        return None
    return (os.path.abspath(file_path), st.st_size, st.st_mtime_ns)


class _ProbeFailed(Exception):
    """Raised by a cached probe that could not answer: the caller gets the
    probe's fallback `answer`, and the next call probes again."""

    def __init__(self, answer):
        super().__init__(answer)
        self.answer = answer


def _cached_probe(func):
    """Memoize a probe by file identity; failures are not cached."""

    def wrapper(file_path: str, *args, **kwargs):
        identity = _file_identity(file_path)
        key = (func.__name__, identity)
        if identity is not None:
            with _PROBE_CACHE_LOCK:
                if key in _PROBE_CACHE:
                    return _PROBE_CACHE[key]
        try:
            value = func(file_path, *args, **kwargs)
        except _ProbeFailed as failure:
            return failure.answer
        if identity is not None:
            with _PROBE_CACHE_LOCK:
                if len(_PROBE_CACHE) >= _PROBE_CACHE_LIMIT:
                    _PROBE_CACHE.pop(next(iter(_PROBE_CACHE)))
                _PROBE_CACHE[key] = value
        return value

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    wrapper.__wrapped__ = func
    return wrapper


def launch_probes(file_path: str) -> dict:
    """Every answer a launch needs, probed on a worker thread for the main loop."""
    return {
        "audio": has_audio_streams(file_path),
        "size": get_video_dimensions(file_path),
        "mp4": check_mp4_compatibility(file_path),
    }


def display_size(stream: dict):
    """(width, height) of a probed video stream as FFmpeg's filters see it.

    FFmpeg rotates phone videos by their display matrix before any filter,
    so a crop computed from the stored size fails on a portrait recording.
    """
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    rotation = next(
        (
            side.get("rotation")
            for side in stream.get("side_data_list", [])
            if "rotation" in side
        ),
        0,
    )
    return (height, width) if round(float(rotation) / 90) % 2 else (width, height)


@_cached_probe
def get_video_dimensions(file_path: str):
    """Displayed (width, height) of the first video stream, or (None, None)."""
    command = [
        get_ffprobe_executable(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height:stream_side_data=rotation",
        "-of",
        "json",
        file_path,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
    except (subprocess.SubprocessError, OSError) as error:
        logger.error("Error getting video dimensions: %s", error)
        raise _ProbeFailed((None, None)) from error
    if result.returncode != 0:
        logger.error("ffprobe error: %s", result.stderr)
        raise _ProbeFailed((None, None))
    try:
        streams = json.loads(result.stdout).get("streams") or []
        if streams:
            return display_size(streams[0])
    except (ValueError, TypeError) as error:
        logger.error("Unreadable ffprobe output: %s", error)
        raise _ProbeFailed((None, None)) from error
    logger.debug("No video streams found in file")
    return None, None


@_cached_probe
def get_queue_summary(file_path: str):
    """Duration, displayed size and codec of the first video stream.

    One FFprobe call for the queue row; callers run it off the GTK thread.
    Values the file does not report are None.
    """
    command = [
        get_ffprobe_executable(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        (
            "format=duration:stream=codec_name,width,height,duration"
            ":stream_side_data=rotation"
        ),
        "-of",
        "json",
        file_path,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
        data = json.loads(result.stdout or "{}")
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        raise _ProbeFailed(None) from error
    if result.returncode != 0 or not isinstance(data, dict):
        raise _ProbeFailed(None)
    streams = data.get("streams") or [{}]
    stream = streams[0] if isinstance(streams[0], dict) else {}
    width, height = display_size(stream) if stream.get("width") else (None, None)
    duration = None
    for value in (stream.get("duration"), (data.get("format") or {}).get("duration")):
        try:
            duration = float(value)
            break
        except (TypeError, ValueError):
            continue
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "codec": stream.get("codec_name"),
    }


_FFMPEG_STREAM_RE = re.compile(
    r"^\s*Stream #0:(?P<index>\d+)"
    r"(?:\[[^\]]*\])?"
    r"(?:\((?P<language>[A-Za-z]{2,3})\))?"
    r":\s*(?P<kind>Video|Audio|Subtitle|Data|Attachment):\s*(?P<rest>.*)$"
)
_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def _probe_with_ffmpeg(file_path: str):
    """Build an ffprobe-like info dict by parsing `ffmpeg -i` output.

    Needed because ffprobe aborts without printing anything when it cannot open
    a decoder for any stream of the file (dvd_subtitle tracks trigger this on
    FFmpeg 9), even though the file itself is perfectly usable. The result only
    carries the fields the UI actually reads.
    """
    try:
        result = subprocess.run(
            [
                get_ffmpeg_executable(),
                "-hide_banner",
                "-i",
                file_path,
                "-t",
                "0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            # Tags are written in whatever encoding the muxer used.
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("ffmpeg fallback probe failed: %s", e)
        return None

    output = result.stderr or ""
    # Everything before the "Output #" section describes the input.
    input_block = output.split("\nOutput #", 1)[0]

    streams = []
    for line in input_block.splitlines():
        match = _FFMPEG_STREAM_RE.match(line)
        if not match:
            continue

        kind = match.group("kind").lower()
        rest = match.group("rest")
        fields = [f.strip() for f in rest.split(",")]

        stream = {
            "index": int(match.group("index")),
            "codec_type": "subtitle" if kind == "subtitle" else kind,
            "codec_name": fields[0].split(" ")[0] if fields else "",
        }
        if match.group("language"):
            stream["tags"] = {"language": match.group("language")}

        if kind == "video":
            for field in fields[1:]:
                size = re.match(r"^(\d{2,5})x(\d{2,5})$", field.split(" ")[0])
                if size:
                    stream["width"] = int(size.group(1))
                    stream["height"] = int(size.group(2))
                fps = re.match(r"^([\d.]+) fps$", field)
                if fps:
                    stream["r_frame_rate"] = f"{int(float(fps.group(1)) * 1000)}/1000"
            if len(fields) > 1:
                stream["pix_fmt"] = fields[1].split("(")[0].strip()
        elif kind == "audio":
            for field in fields[1:]:
                rate = re.match(r"^(\d+) Hz$", field)
                if rate:
                    stream["sample_rate"] = rate.group(1)
                layout = field.split("(")[0].strip()
                channels = {
                    "mono": 1,
                    "stereo": 2,
                    "2.1": 3,
                    "3.0": 3,
                    "quad": 4,
                    "4.0": 4,
                    "3.1": 4,
                    "5.0": 5,
                    "5.1": 6,
                    "6.0": 6,
                    "6.1": 7,
                    "7.0": 7,
                    "7.1": 8,
                }.get(layout)
                if not channels:
                    # "3 channels": ffmpeg's form for a layout without a name.
                    generic = re.match(r"^(\d+) channels?$", layout)
                    if generic:
                        channels = int(generic.group(1))
                if channels:
                    stream["channels"] = channels

        streams.append(stream)

    if not any(s["codec_type"] == "video" for s in streams):
        return None

    info = {"streams": streams, "format": {}}

    duration_match = _FFMPEG_DURATION_RE.search(input_block)
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        info["format"]["duration"] = str(
            int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        )

    container = re.search(
        r"^Input #0,\s*([^,]+(?:,[^,]+)*),\s*from", input_block, re.MULTILINE
    )
    if container:
        info["format"]["format_name"] = container.group(1).strip()
        info["format"]["format_long_name"] = container.group(1).strip()

    try:
        info["format"]["size"] = str(os.path.getsize(file_path))
    except OSError:
        pass

    logger.debug(
        "ffmpeg fallback probe found %s streams in %s",
        len(streams),
        os.path.basename(file_path),
    )
    return info


def get_video_file_info(file_path: str):
    """The file's format, streams and chapters as ffprobe reports them, or None."""
    try:
        if not os.path.exists(file_path):
            return None

        command = [
            get_ffprobe_executable(),
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            file_path,
        ]

        # Don't use check=True: ffprobe exits non-zero when it fails to open a
        # decoder for any stream (e.g. dvd_subtitle tracks on FFmpeg 9) even
        # though it already printed usable JSON for the rest of the file.
        # ffprobe copies tag bytes into its JSON as they are, Latin-1 too.
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )

        # ffprobe may also stop halfway through, leaving truncated JSON behind.
        try:
            info = json.loads(result.stdout) if result.stdout.strip() else None
        except json.JSONDecodeError:
            info = None

        if not info or not info.get("streams"):
            # ffprobe gave up on this file: read the metadata from ffmpeg.
            logger.debug(
                "ffprobe returned no usable data for %s; falling back to ffmpeg",
                os.path.basename(file_path),
            )
            info = _probe_with_ffmpeg(file_path)
            if not info:
                return None

        # Calculate bitrate if not provided by ffprobe
        if (
            "format" in info
            and (
                "bit_rate" not in info["format"] or info["format"]["bit_rate"] == "N/A"
            )
            and "duration" in info["format"]
            and "size" in info["format"]
        ):
            duration = float(info["format"]["duration"])
            size = float(info["format"]["size"])
            if duration > 0:
                bitrate = (size * 8) / duration
                info["format"]["bit_rate"] = str(int(bitrate))

        return normalize_probe(info)

    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Error getting file info: %s", e)
        return None


_INTEGER_FIELDS = ("size", "bit_rate", "sample_rate", "channels", "width", "height")


def _number(value, kind):
    try:
        number = kind(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def normalize_probe(info: dict) -> dict:
    """ffprobe's answer with every field the information dialog reads usable.

    Runs on the worker: "N/A", NaN or a missing block would otherwise raise in
    the dialog's main-loop callback and leave it empty. Unusable fields are left out.
    """

    def clean(block):
        result = {}
        for key, value in block.items():
            if key in _INTEGER_FIELDS:
                value = _number(value, lambda v: int(float(v)))
            elif key == "duration":
                value = _number(value, float)
            elif key == "r_frame_rate":
                num, _sep, den = str(value).partition("/")
                if not (num.isdigit() and den.isdigit() and int(den)):
                    value = None
            elif key == "tags":
                value = (
                    {str(k): str(v) for k, v in value.items()}
                    if isinstance(value, dict)
                    else None
                )
            if value is not None:
                result[key] = value
        return result

    format_block = info.get("format")
    streams = info.get("streams")
    return {
        **info,
        "format": clean(format_block) if isinstance(format_block, dict) else {},
        "streams": [clean(stream) for stream in streams if isinstance(stream, dict)]
        if isinstance(streams, list)
        else [],
    }


@_cached_probe
def has_audio_streams(file_path: str):
    """Whether the file has an audio stream; True when ffprobe cannot tell."""
    try:
        if not os.path.exists(file_path):
            return False

        command = [
            get_ffprobe_executable(),
            "-v",
            "quiet",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            file_path,
        ]

        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )

        if result.stdout.strip():
            return True

        if result.returncode != 0:
            # ffprobe could not analyze the file at all (it bails out when a
            # decoder for an unrelated stream cannot be opened). An empty
            # answer here means "unknown", not "no audio"; assume there is
            # audio so we never silently drop the audio tracks.
            logger.warning(
                "ffprobe failed while checking audio streams; assuming the file has audio"
            )
            return True

        return False

    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Error checking audio streams: %s", e)
        # On error, assume there might be audio to avoid accidentally removing it
        raise _ProbeFailed(True) from e


@_cached_probe
def check_mp4_compatibility(file_path: str):
    """(compatible, streams): whether a stream copy into MP4 keeps every stream.

    Each dict in ``streams`` describes one offending stream for the UI:
    ``codec_type``, ``codec_name``, ``index``, ``language`` and ``title``.
    Translation is left to the caller.
    """
    try:
        if not os.path.exists(file_path):
            return False, []

        info = get_video_file_info(file_path)
        if not info or "streams" not in info:
            # Can't determine, assume compatible
            raise _ProbeFailed((True, []))

        # MP4 compatible codecs
        mp4_video_codecs = ["h264", "hevc", "mpeg4", "h263", "mjpeg", "vp9", "av1"]
        mp4_audio_codecs = [
            "aac",
            "mp3",
            "ac3",
            "eac3",
            "opus",
            "vorbis",
            "flac",
            "alac",
        ]

        incompatible = []
        # Per-type track numbers, so the UI can say "audio track 2"
        type_counters = {"video": 0, "audio": 0}

        for stream in info["streams"]:
            codec_type = stream.get("codec_type")
            if codec_type not in type_counters:
                continue

            type_counters[codec_type] += 1
            codec_name = stream.get("codec_name", "").lower()
            if not codec_name:
                continue

            allowed = mp4_video_codecs if codec_type == "video" else mp4_audio_codecs
            if codec_name in allowed:
                continue

            tags = stream.get("tags") or {}
            incompatible.append(
                {
                    "codec_type": codec_type,
                    "codec_name": codec_name,
                    "index": type_counters[codec_type],
                    "language": tags.get("language", ""),
                    "title": tags.get("title", ""),
                }
            )

        return len(incompatible) == 0, incompatible

    except OSError as e:
        logger.error("Error checking MP4 compatibility: %s", e)
        # On error, assume compatible to avoid blocking conversion
        raise _ProbeFailed((True, [])) from e
