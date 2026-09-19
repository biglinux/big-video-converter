"""User presets: TOML recipes that ride on top of the tested conversion engine.

A preset never replaces what the bash script already decides well (which
FFmpeg binary, which GPU, when to decode on the CPU, when to tone map or drop
to 8 bits, how to fall back). It only says what the user wants at the end:
codec, quality, container, audio, a few output options and, for power users,
extra arguments for specific encoders. Those extra arguments are appended
after the script's own, so FFmpeg's last-one-wins rule lets a preset override
a single knob without rebuilding the whole command.

The same module serves the GUI (listing, applying, importing, the AI prompt)
and the script (``--env`` and ``--encoder-args`` sub-commands, NUL-separated,
never a shell program).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import gettext
import locale
import os
import re
import shlex
import sys
import tempfile
import tomllib

_ = gettext.gettext

FORMAT_VERSION = 1
USER_DIR_NAME = "presets"

VIDEO_CODECS = ("copy", "h264", "h265", "av1", "vp9", "prores")
VIDEO_QUALITIES = ("default", "veryhigh", "high", "medium", "low", "verylow", "superlow")
VIDEO_SPEEDS = ("default", "ultrafast", "veryfast", "faster", "medium", "slow", "veryslow")
GPU_MODES = ("auto", "software", "nvidia", "amd", "intel", "vulkan")
AUDIO_MODES = ("copy", "reencode", "none")
AUDIO_CODECS = ("aac", "opus", "ac3")
SUBTITLE_MODES = ("extract", "embedded", "none")
CONTAINERS = ("mp4", "mkv", "mov", "webm")
CONTAINER_INDEX = {"mp4": 0, "mkv": 1, "mov": 2, "webm": 3}

# Encoders the script can select. A preset may carry arguments for any of
# them; the ones for the encoder actually chosen at run time are applied.
KNOWN_ENCODERS = (
    "libx264", "libx265", "libsvtav1", "libvpx-vp9", "prores_ks",
    "h264_nvenc", "hevc_nvenc", "av1_nvenc",
    "h264_vaapi", "hevc_vaapi", "av1_vaapi", "vp9_vaapi",
    "h264_qsv", "hevc_qsv", "av1_qsv", "vp9_qsv",
    "h264_vulkan", "hevc_vulkan", "av1_vulkan",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESOLUTION_RE = re.compile(r"^([1-9]\d{1,4})x([1-9]\d{1,4})$")
_BITRATE_RE = re.compile(r"^[1-9][0-9]*[kKmM]?$")
_PIX_FMT_RE = re.compile(r"^[a-z0-9_]{3,32}$")
_ENCODER_RE = re.compile(r"^[a-z0-9_-]{2,32}$")
_FENCE_RE = re.compile(r"```(?:toml)?\s*\n(.*?)```", re.S)
_MAX_TEXT = 65536


class PresetError(ValueError):
    """A preset file that cannot be trusted; the message is for the user."""


@dataclass
class Preset:
    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    author: str = ""
    video: dict = field(default_factory=dict)
    audio: dict = field(default_factory=dict)
    subtitles: dict = field(default_factory=dict)
    container: dict = field(default_factory=dict)
    ffmpeg: dict = field(default_factory=dict)
    encoders: dict[str, list[str]] = field(default_factory=dict)
    path: str = ""
    bundled: bool = False

    # Bundled presets are written in English; their texts are listed in
    # presets/bundled_strings.py so the ordinary gettext catalogue carries
    # them into every language. A user's own preset is shown as written.
    @property
    def display_name(self) -> str:
        return _(self.name) if self.bundled and self.name else self.name

    @property
    def display_description(self) -> str:
        return _(self.description) if self.bundled and self.description else self.description

    @property
    def display_tags(self) -> list[str]:
        return [_(tag) for tag in self.tags] if self.bundled else list(self.tags)

    @property
    def summary(self) -> str:
        """One line for the sidebar: codec, quality, container."""
        parts = []
        codec = self.video.get("codec")
        if codec == "copy":
            parts.append(_("Copy video"))
        elif codec:
            parts.append({"h264": "H.264", "h265": "H.265", "av1": "AV1", "vp9": "VP9",
                          "prores": "ProRes"}.get(codec, codec))
        if self.video.get("resolution"):
            parts.append(self.video["resolution"])
        if self.container.get("format"):
            parts.append(self.container["format"].upper())
        return " · ".join(parts)


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------

def bundled_dir() -> str:
    """Presets shipped with the application, next to this package."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets")


def user_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "big-video-converter", USER_DIR_NAME)


# --------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------

def parse_preset_text(text: str) -> dict:
    """TOML text, possibly wrapped in a Markdown code fence by a chat bot."""
    if not isinstance(text, str) or len(text) > _MAX_TEXT:
        raise PresetError(_("The preset text is empty or too large."))
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise PresetError(_("The preset is not valid TOML: {0}").format(error)) from error
    if not isinstance(data, dict):
        raise PresetError(_("The preset must be a TOML table."))
    return data


def _table(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise PresetError(_("[{0}] must be a table.").format(key))
    return value


def _choice(table: dict, section: str, key: str, allowed: tuple, required: bool = False):
    if key not in table:
        if required:
            raise PresetError(_("[{0}] {1} is required.").format(section, key))
        return None
    value = table[key]
    if not isinstance(value, str) or value not in allowed:
        raise PresetError(_("[{0}] {1} must be one of: {2}").format(section, key, ", ".join(allowed)))
    return value


def _text(table: dict, section: str, key: str, limit: int = 4096, default: str = "") -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise PresetError(_("[{0}] {1} must be a short text.").format(section, key))
    return value


def _integer(table: dict, section: str, key: str, low: int, high: int):
    if key not in table:
        return None
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PresetError(_("[{0}] {1} must be a whole number between {2} and {3}.").format(section, key, low, high))
    return value


def _boolean(table: dict, section: str, key: str):
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, bool):
        raise PresetError(_("[{0}] {1} must be true or false.").format(section, key))
    return value


def _options_text(table: dict, section: str, key: str) -> str:
    """Free FFmpeg options go through the same grammar as the GUI field."""
    from utils.ffmpeg_options import parse_additional_options

    text = _text(table, section, key)
    if not text:
        return ""
    try:
        return shlex.join(parse_additional_options(text))
    except ValueError as error:
        raise PresetError(_("[{0}] {1}: {2}").format(section, key, error)) from error


def _encoder_args(name: str, table) -> list[str]:
    from utils.ffmpeg_options import parse_additional_options

    if not isinstance(table, dict):
        raise PresetError(_("[encoder.{0}] must be a table with an args list.").format(name))
    args = table.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) and a for a in args):
        raise PresetError(_("[encoder.{0}] args must be a list of strings.").format(name))
    if len(args) > 200:
        raise PresetError(_("[encoder.{0}] has too many arguments.").format(name))
    try:
        return parse_additional_options(shlex.join(args))
    except ValueError as error:
        raise PresetError(_("[encoder.{0}]: {1}").format(name, error)) from error


def validate_preset(data: dict, *, preset_id: str = "", path: str = "", bundled: bool = False) -> Preset:
    """Turn a parsed TOML table into a Preset or raise PresetError."""
    if not isinstance(data, dict):
        raise PresetError(_("The preset must be a TOML table."))
    version = data.get("format", FORMAT_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version != FORMAT_VERSION:
        raise PresetError(_("Unsupported preset format {0}; this version reads format {1}.").format(version, FORMAT_VERSION))
    unknown = set(data) - {"format", "preset", "video", "audio", "subtitles", "container", "ffmpeg", "encoder"}
    if unknown:
        raise PresetError(_("Unknown section(s): {0}").format(", ".join(sorted(unknown))))

    meta = _table(data, "preset")
    name = _text(meta, "preset", "name", 120).strip()
    if not name:
        raise PresetError(_("[preset] name is required."))
    description = _text(meta, "preset", "description", 2000).strip()
    author = _text(meta, "preset", "author", 200).strip()
    tags = meta.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and 0 < len(t) <= 40 for t in tags) or len(tags) > 20:
        raise PresetError(_("[preset] tags must be a list of short words."))

    video_in = _table(data, "video")
    video: dict = {}
    codec = _choice(video_in, "video", "codec", VIDEO_CODECS)
    if codec:
        video["codec"] = codec
    for key, allowed in (("quality", VIDEO_QUALITIES), ("speed", VIDEO_SPEEDS), ("gpu", GPU_MODES)):
        value = _choice(video_in, "video", key, allowed)
        if value:
            video[key] = value
    if "resolution" in video_in:
        resolution = _text(video_in, "video", "resolution", 16)
        match = _RESOLUTION_RE.match(resolution)
        if not match or int(match.group(1)) > 32768 or int(match.group(2)) > 32768:
            raise PresetError(_("[video] resolution must look like 1920x1080."))
        video["resolution"] = resolution
    if "fps" in video_in:
        fps = video_in["fps"]
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not 1 <= fps <= 300:
            raise PresetError(_("[video] fps must be a number between 1 and 300."))
        video["fps"] = fps
    if "pixel_format" in video_in:
        pix = _text(video_in, "video", "pixel_format", 32)
        if not _PIX_FMT_RE.match(pix):
            raise PresetError(_("[video] pixel_format must be an FFmpeg pixel format name such as yuv420p."))
        video["pixel_format"] = pix
    unknown = set(video_in) - {"codec", "quality", "speed", "gpu", "resolution", "fps", "pixel_format"}
    if unknown:
        raise PresetError(_("[video] has unknown key(s): {0}").format(", ".join(sorted(unknown))))

    audio_in = _table(data, "audio")
    audio: dict = {}
    for key, allowed in (("mode", AUDIO_MODES), ("codec", AUDIO_CODECS)):
        value = _choice(audio_in, "audio", key, allowed)
        if value:
            audio[key] = value
    if "bitrate" in audio_in:
        bitrate = _text(audio_in, "audio", "bitrate", 8)
        if not _BITRATE_RE.match(bitrate):
            raise PresetError(_("[audio] bitrate must look like 192k."))
        audio["bitrate"] = bitrate
    channels = _integer(audio_in, "audio", "channels", 1, 64)
    if channels is not None:
        audio["channels"] = channels
    sample_rate = _integer(audio_in, "audio", "sample_rate", 8000, 192000)
    if sample_rate is not None:
        audio["sample_rate"] = sample_rate
    normalize = _boolean(audio_in, "audio", "normalize")
    if normalize is not None:
        audio["normalize"] = normalize
    unknown = set(audio_in) - {"mode", "codec", "bitrate", "channels", "sample_rate", "normalize"}
    if unknown:
        raise PresetError(_("[audio] has unknown key(s): {0}").format(", ".join(sorted(unknown))))

    subtitles_in = _table(data, "subtitles")
    subtitles: dict = {}
    mode = _choice(subtitles_in, "subtitles", "mode", SUBTITLE_MODES)
    if mode:
        subtitles["mode"] = mode
    unknown = set(subtitles_in) - {"mode"}
    if unknown:
        raise PresetError(_("[subtitles] has unknown key(s): {0}").format(", ".join(sorted(unknown))))

    container_in = _table(data, "container")
    container: dict = {}
    fmt = _choice(container_in, "container", "format", CONTAINERS)
    if fmt:
        container["format"] = fmt
    unknown = set(container_in) - {"format"}
    if unknown:
        raise PresetError(_("[container] has unknown key(s): {0}").format(", ".join(sorted(unknown))))

    ffmpeg_in = _table(data, "ffmpeg")
    ffmpeg: dict = {}
    for key in ("input_options", "output_options"):
        value = _options_text(ffmpeg_in, "ffmpeg", key)
        if value:
            ffmpeg[key] = value
    unknown = set(ffmpeg_in) - {"input_options", "output_options"}
    if unknown:
        raise PresetError(_("[ffmpeg] has unknown key(s): {0}").format(", ".join(sorted(unknown))))

    encoders_in = _table(data, "encoder")
    encoders: dict[str, list[str]] = {}
    for enc_name, table in encoders_in.items():
        if not isinstance(enc_name, str) or not _ENCODER_RE.match(enc_name):
            raise PresetError(_("[encoder.{0}] is not an encoder name.").format(enc_name))
        args = _encoder_args(enc_name, table)
        if args:
            encoders[enc_name] = args

    if codec == "copy" and encoders:
        raise PresetError(_("A preset that copies the video cannot carry encoder arguments."))

    if not preset_id:
        preset_id = slugify(name)
    if not _ID_RE.match(preset_id):
        raise PresetError(_("Invalid preset identifier: {0}").format(preset_id))
    return Preset(id=preset_id, name=name, description=description, tags=tags, author=author,
                  video=video, audio=audio, subtitles=subtitles, container=container,
                  ffmpeg=ffmpeg, encoders=encoders, path=path, bundled=bundled)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    return slug or "preset"


def load_preset(path: str, *, bundled: bool = False) -> Preset:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_TEXT + 1)
    except OSError as error:
        raise PresetError(_("Could not read the preset: {0}").format(error)) from error
    if len(raw) > _MAX_TEXT:
        raise PresetError(_("The preset text is empty or too large."))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PresetError(_("The preset is not UTF-8 text.")) from error
    stem = os.path.splitext(os.path.basename(path))[0]
    return validate_preset(parse_preset_text(text), preset_id=stem, path=os.path.abspath(path), bundled=bundled)


def list_presets(*, include_bundled: bool = True) -> list[Preset]:
    """Bundled and user presets; a user file with the same id hides a bundled one.

    Unreadable files are skipped rather than hiding every other preset; the
    dialog reports them through :func:`broken_presets`.
    """
    found: dict[str, Preset] = {}
    sources = [(bundled_dir(), True)] if include_bundled else []
    sources.append((user_dir(), False))
    for directory, bundled in sources:
        for path in _toml_files(directory):
            try:
                preset = load_preset(path, bundled=bundled)
            except PresetError:
                continue
            found[preset.id] = preset
    return sorted(found.values(), key=lambda p: (p.bundled, p.name.lower()))


def broken_presets() -> list[tuple[str, str]]:
    """(path, reason) for user preset files that do not validate."""
    problems = []
    for path in _toml_files(user_dir()):
        try:
            load_preset(path)
        except PresetError as error:
            problems.append((path, str(error)))
    return problems


def find_preset(preset_id: str) -> Preset | None:
    if not preset_id:
        return None
    for preset in list_presets():
        if preset.id == preset_id:
            return preset
    return None


def _toml_files(directory: str) -> list[str]:
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [os.path.join(directory, n) for n in names if n.endswith(".toml") and not n.startswith(".")]


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------

def save_user_preset(text: str, *, preset_id: str = "", replace: bool = False) -> Preset:
    """Validate TOML text and store it in the user directory, atomically.

    The text is stored as written (comments included). A new preset never
    silently overwrites an existing one unless ``replace`` is set.
    """
    data = parse_preset_text(text)
    preset = validate_preset(data, preset_id=preset_id)
    directory = user_dir()
    os.makedirs(directory, exist_ok=True)
    target_id = preset.id
    if not replace:
        existing = {p.id for p in list_presets(include_bundled=False)}
        counter = 2
        while target_id in existing:
            target_id = f"{preset.id}-{counter}"
            counter += 1
    path = os.path.join(directory, target_id + ".toml")
    _atomic_write(path, text if not _FENCE_RE.search(text) else _FENCE_RE.search(text).group(1))
    return load_preset(path)


def delete_user_preset(preset: Preset) -> None:
    if preset.bundled or not preset.path.startswith(os.path.abspath(user_dir()) + os.sep):
        raise PresetError(_("Bundled presets cannot be deleted; duplicate one to edit it."))
    os.remove(preset.path)


def duplicate_preset(preset: Preset) -> Preset:
    """A user copy of any preset, ready to be edited by hand."""
    with open(preset.path, "r", encoding="utf-8") as handle:
        text = handle.read()
    copy_name = _("{0} (copy)").format(preset.display_name)
    text, count = re.subn(r'^(\s*name\s*=\s*)"[^"\n]*"', lambda m: m.group(1) + _toml_string(copy_name), text,
                          count=1, flags=re.M)
    if count == 0:
        text = text.replace("[preset]", "[preset]\nname = " + _toml_string(copy_name), 1)
    return save_user_preset(text, preset_id=preset.id + "-copy")


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _atomic_write(path: str, text: str) -> None:
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".preset-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Applying a preset
# --------------------------------------------------------------------------

def preset_settings(preset: Preset) -> dict:
    """Settings-manager keys a preset fixes; the GUI applies these to widgets."""
    out: dict = {}
    codec = preset.video.get("codec")
    if codec:
        out["video-codec"] = codec
        out["force-copy-video"] = codec == "copy"
    if "quality" in preset.video:
        out["video-quality"] = preset.video["quality"]
    if "speed" in preset.video:
        out["preset"] = preset.video["speed"]
    if "gpu" in preset.video:
        out["gpu"] = preset.video["gpu"]
    out["video-resolution"] = preset.video.get("resolution", "") if codec != "copy" else ""
    if "mode" in preset.audio:
        out["audio-handling"] = preset.audio["mode"]
    if "codec" in preset.audio:
        out["audio-codec"] = preset.audio["codec"]
    if "bitrate" in preset.audio:
        out["audio-bitrate"] = preset.audio["bitrate"]
    if "channels" in preset.audio:
        out["audio-channels"] = str(preset.audio["channels"])
    if "normalize" in preset.audio:
        out["normalize-enabled"] = preset.audio["normalize"]
    if "mode" in preset.subtitles:
        out["subtitle-extract"] = preset.subtitles["mode"]
    if "format" in preset.container:
        out["output-format-index"] = CONTAINER_INDEX[preset.container["format"]]
    return out


def preset_options(preset: Preset) -> str:
    """Output options the structured fields imply, plus the free ones."""
    tokens: list[str] = []
    if "fps" in preset.video:
        fps = preset.video["fps"]
        tokens += ["-r", str(int(fps)) if float(fps).is_integer() else repr(float(fps))]
    if "sample_rate" in preset.audio:
        tokens += ["-ar", str(preset.audio["sample_rate"])]
    text = shlex.join(tokens)
    for key in ("input_options", "output_options"):
        if preset.ffmpeg.get(key):
            text = (text + " " + preset.ffmpeg[key]).strip()
    return text


def preset_environment(preset: Preset) -> dict[str, str]:
    """Variables the bash script understands, for use without the GUI."""
    env: dict[str, str] = {}
    codec = preset.video.get("codec")
    if codec == "copy":
        env["force_copy_video"] = "1"
    elif codec:
        env["video_encoder"] = codec
    if "quality" in preset.video:
        env["video_quality"] = preset.video["quality"]
    if "speed" in preset.video:
        env["preset"] = preset.video["speed"]
    if "gpu" in preset.video:
        env["gpu"] = preset.video["gpu"]
    if preset.video.get("resolution") and codec != "copy":
        env["video_resolution"] = preset.video["resolution"]
    if "mode" in preset.audio:
        env["audio_handling"] = preset.audio["mode"]
    if "codec" in preset.audio:
        env["audio_codec"] = preset.audio["codec"]
    if "bitrate" in preset.audio:
        env["audio_bitrate"] = preset.audio["bitrate"]
    if "channels" in preset.audio:
        env["audio_channels"] = str(preset.audio["channels"])
    if preset.audio.get("normalize"):
        env["normalize_enabled"] = "1"
    if "mode" in preset.subtitles:
        env["subtitle_extract"] = preset.subtitles["mode"]
    if "format" in preset.container:
        env["output_format"] = preset.container["format"]
    options = preset_options(preset)
    if options:
        env["preset_options"] = options
    return env


SOFTWARE_ENCODERS = ("libx264", "libx265", "libsvtav1", "libvpx-vp9", "prores_ks")


def encoder_args(preset: Preset, encoder: str) -> list[str]:
    """Arguments for one encoder; the pixel format only for software ones.

    A global -pix_fmt would ask a VAAPI or QSV encoder for a CPU pixel format
    and fail the GPU stages, sending every preset with a pixel format to
    software encoding. Hardware encoders keep the script's own surface format.
    """
    args = list(preset.encoders.get(encoder, []))
    if "pixel_format" in preset.video and encoder in SOFTWARE_ENCODERS:
        args = ["-pix_fmt", preset.video["pixel_format"]] + args
    return args


# --------------------------------------------------------------------------
# The prompt for a chat assistant
# --------------------------------------------------------------------------

_LANGUAGE_NAMES = {
    "pt": "Portuguese", "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "ru": "Russian", "pl": "Polish", "nl": "Dutch", "tr": "Turkish",
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "ar": "Arabic", "hi": "Hindi",
    "uk": "Ukrainian", "cs": "Czech", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "el": "Greek", "he": "Hebrew", "hu": "Hungarian", "ro": "Romanian", "bg": "Bulgarian",
    "et": "Estonian", "id": "Indonesian", "vi": "Vietnamese", "th": "Thai", "ca": "Catalan",
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
normalize = false        # loudness normalisation to broadcast/streaming levels

[subtitles]
mode = "embedded"        # extract (to .srt files) | embedded (inside the container) | none

[container]
format = "mp4"           # mp4 | mkv | mov | webm

[ffmpeg]
output_options = "-g 60 -bf 2"   # extra FFmpeg output options, only from this list: {allowed_flags}

[encoder.libx264]                # one table per encoder; only the encoder actually used gets its args
args = ["-tune", "film"]
```

Encoders the program may pick, depending on the machine: {encoders}.
FFmpeg encoders available on this machine right now: {available}.
FFmpeg version on this machine: {ffmpeg_version}.

Request from the user:
{request}
"""


def build_ai_prompt(request: str, *, available_encoders=(), ffmpeg_version: str = "unknown",
                    language: str | None = None) -> str:
    from utils.ffmpeg_options import ALLOWED_FFMPEG_FLAGS

    request = (request or "").strip() or "(the user did not describe the request; ask them what the preset is for)"
    available = ", ".join(sorted(e for e in available_encoders if e in KNOWN_ENCODERS)) or "unknown"
    return PROMPT_TEMPLATE.format(
        language=language or system_language(),
        allowed_flags=" ".join(sorted(ALLOWED_FFMPEG_FLAGS)),
        encoders=", ".join(KNOWN_ENCODERS),
        available=available,
        ffmpeg_version=ffmpeg_version,
        request=request,
    )


# --------------------------------------------------------------------------
# Command line used by the bash script
# --------------------------------------------------------------------------

def bundled_strings_source() -> str:
    """Python source listing every translatable text of the bundled presets.

    Written to presets/bundled_strings.py, never imported: xgettext reads it
    together with the rest of the application, so the same catalogue that
    translates the interface translates the preset names and descriptions.
    A test keeps the file in step with the TOML files.
    """
    texts: list[str] = []
    for path in _toml_files(bundled_dir()):
        preset = load_preset(path, bundled=True)
        for text in [preset.name, preset.description, *preset.tags]:
            if text and text not in texts:
                texts.append(text)
    lines = [
        '"""Translatable texts of the bundled presets. GENERATED, do not edit.',
        "",
        "Regenerate with:  python3 utils/presets.py --strings > presets/bundled_strings.py",
        '"""',
        "import gettext",
        "",
        "_ = gettext.gettext",
        "",
        "BUNDLED_PRESET_TEXTS = [",
    ]
    lines += [f"    _({text!r})," for text in texts]
    lines += ["]", ""]
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    usage = "Usage: presets.py --env FILE | --encoder-args FILE ENCODER | --validate FILE | --strings"
    if len(argv) == 2 and argv[1] == "--strings":
        sys.stdout.write(bundled_strings_source())
        return 0
    if len(argv) < 3:
        sys.exit(usage)
    mode, path = argv[1], argv[2]
    try:
        preset = load_preset(path)
    except PresetError as error:
        sys.stderr.write(f"ERROR: Invalid preset {path}: {error}\n")
        return 2
    out = sys.stdout.buffer
    if mode == "--env" and len(argv) == 3:
        for key, value in preset_environment(preset).items():
            out.write(f"{key}={value}".encode("utf-8") + b"\0")
    elif mode == "--encoder-args" and len(argv) == 4:
        for token in encoder_args(preset, argv[3]):
            out.write(token.encode("utf-8") + b"\0")
    elif mode == "--validate" and len(argv) == 3:
        sys.stdout.write(f"OK: {preset.name} ({preset.id})\n")
    else:
        sys.exit(usage)
    return 0


if __name__ == "__main__":
    _parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    sys.exit(_main(sys.argv))
