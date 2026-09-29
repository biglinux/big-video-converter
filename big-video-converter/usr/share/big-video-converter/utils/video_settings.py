"""FFmpeg video filters built from one file's crop, colour and transform edits."""

import logging
import math
import os
import subprocess
from functools import lru_cache

from utils.ffmpeg_path import get_ffmpeg_executable

logger = logging.getLogger(__name__)

# Video Adjustment Default Values
VIDEO_ADJUSTMENT_DEFAULTS = {
    "brightness": 0.0,  # Preview player: -1.0 to 1.0, FFmpeg: -1.0 to 1.0 (direct map)
    "contrast": 0.0,  # Preview: -1.0 to 1.0; FFmpeg eq neutral is 1.0
    "saturation": 1.0,  # Preview player: 0.0 to 2.0, FFmpeg: 0.0 to 16.0 (needs conversion)
    "hue": 0.0,  # Preview player: -1.0 to 1.0, FFmpeg: -3.14 to 3.14 radians (needs conversion)
    "crop_left": 0,
    "crop_right": 0,
    "crop_top": 0,
    "crop_bottom": 0,
}

# Settings key mapping
SETTING_KEYS = {
    "brightness": "preview-brightness",
    "contrast": "preview-contrast",
    "saturation": "preview-saturation",
    "hue": "preview-hue",
    "crop_left": "preview-crop-left",
    "crop_right": "preview-crop-right",
    "crop_top": "preview-crop-top",
    "crop_bottom": "preview-crop-bottom",
}

# Effect levels, chosen by eye and measured on 1080p (GTX 1660 Ti, Ryzen
# 5600H): each runs at 90 frames per second or more. hqdn3d removes grain and
# low-light noise (and 5-10% of the file on a clean film); CAS is AMD's
# contrast adaptive sharpening, which sharpens without the halos of unsharp.
DENOISE_FILTERS = {
    "light": "hqdn3d=2:1.5:3:2.25",
    "medium": "hqdn3d=4:3:6:4.5",
    "strong": "hqdn3d=8:6:12:9",
}
SHARPEN_FILTERS = {
    "light": "cas=strength=0.3:planes=1",
    "medium": "cas=strength=0.6:planes=1",
    "strong": "cas=strength=0.9:planes=1",
}
LUT_SUFFIXES = (".cube", ".3dl")
SHADER_SUFFIXES = (".hook", ".glsl")


@lru_cache(maxsize=1)
def available_filters() -> frozenset[str]:
    """Filter names this FFmpeg build has: hqdn3d needs a GPL build, the
    stabilizer libvidstab, shaders libplacebo."""
    try:
        result = subprocess.run(
            [get_ffmpeg_executable(), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("Failed to query ffmpeg filters: %s", error)
        return frozenset()
    # " TS  hqdn3d  V->V  Apply a High Quality 3D Denoiser."
    return frozenset(parts[1] for parts in map(str.split, result.stdout.splitlines())
                     if len(parts) >= 3 and "->" in parts[2])


def filter_path(path: str) -> str:
    """A path escaped for an option value inside a filter graph.

    FFmpeg parses it twice, as the option value and then as part of the graph;
    escaped for both, a folder named "a,b" or "x:y" stays one path instead of
    ending the option or starting another filter (the script's filter_path).
    """
    for special in ("\\", "'", ":"):
        path = path.replace(special, "\\" + special)
    for special in ("\\", "'", "[", "]", ",", ";"):
        path = path.replace(special, "\\" + special)
    return path


def effect_file_filter(path: str) -> str:
    """lut3d for a colour LUT, libplacebo for an mpv shader, "" otherwise."""
    suffix = os.path.splitext(path)[1].lower()
    if suffix in LUT_SUFFIXES:
        return f"lut3d=file={filter_path(path)}"
    if suffix in SHADER_SUFFIXES:
        return f"libplacebo=custom_shader_path={filter_path(path)}"
    return ""


def effects_graph(denoise: str, sharpen: str, effect_file: str) -> str:
    """The effects that run before the colour adjustments, as one graph.

    The order is the preview's: mpv runs its filter graph first and applies
    brightness, contrast, saturation and hue afterwards, when it converts the
    picture for display. A shader runs after those, so it is not part of this.
    """
    parts = [DENOISE_FILTERS.get(denoise), SHARPEN_FILTERS.get(sharpen)]
    if os.path.splitext(effect_file)[1].lower() in LUT_SUFFIXES:
        parts.append(effect_file_filter(effect_file))
    return ",".join(part for part in parts if part)


# Threshold for determining if a value needs to be included
FLOAT_THRESHOLD = 0.01


class CropError(ValueError):
    """The file's crop cannot be applied: its frame size is unknown or the
    margins leave no picture. Converting without it would silently produce
    an uncropped video."""


class SettingsOverride:
    """Read-only settings view with per-file overrides.

    A conversion must not write its per-file crop/colour values into the global
    settings: with parallel conversions the second file overwrites the first
    one's values before its FFmpeg command is built, so both end up with the
    wrong filters. Wrapping the manager keeps the per-file values local to the
    conversion that owns them.

    Only reads are overridden; anything else is delegated to the real manager.
    """

    def __init__(self, settings, overrides: dict):
        self._settings = settings
        self._overrides = overrides or {}

    def get_value(self, key: str, default=None):
        if key in self._overrides:
            return self._overrides[key]
        return self._settings.get_value(key, default)

    def load_setting(self, key: str, default=None):
        return self.get_value(key, default)

    def get_string(self, key: str, default=None):
        value = self.get_value(key, default)
        return value if value is None else str(value)

    def get_boolean(self, key: str, default=None):
        if key in self._overrides:
            return bool(self._overrides[key])
        return self._settings.get_boolean(key, default)

    def __getattr__(self, name):
        # Delegate everything else (set_*, save_setting, ...) to the manager.
        return getattr(self._settings, name)


def get_adjustment_value(settings, name: str):
    """Get adjustment value from settings"""
    return settings.get_value(SETTING_KEYS[name], VIDEO_ADJUSTMENT_DEFAULTS[name])


#
# Value Conversion Functions (Preview Player to FFmpeg)
#
def preview_saturation_to_ffmpeg(saturation):
    """
    Preview player saturation: 0.0 to 2.0 (1.0 is neutral)
    FFmpeg eq saturation: 0.0 to 16.0 (1.0 is neutral)

    Mapping strategy:
    - Below neutral: Preview [0.0, 1.0] → FFmpeg [0.0, 1.0] (direct map)
    - Above neutral: Preview [1.0, 2.0] → FFmpeg [1.0, 3.0] (scaled map)
    """
    if saturation >= 1.0:
        # Map Preview's [1.0, 2.0] to FFmpeg's [1.0, 3.0]
        return 1.0 + (saturation - 1.0) * 1.5
    else:
        # Direct 1:1 map for values below neutral
        return saturation


def preview_hue_to_ffmpeg(hue):
    """
    Preview player hue: -1.0 to 1.0 (0.0 is neutral)
    FFmpeg hue filter: -3.14 to 3.14 radians / -π to π (0.0 is neutral)

    The preview player supports hue in the range [-1.0, 1.0].
    We need to convert this normalized range to FFmpeg's radian range.

    Mapping: Preview [-1.0, 1.0] → FFmpeg [-π, π] radians
    Formula: ffmpeg_hue = preview_hue * π

    This ensures:
    - Preview hue = -1.0 → FFmpeg hue = -π (-180°)
    - Preview hue =  0.0 → FFmpeg hue =  0  (0°)
    - Preview hue = +1.0 → FFmpeg hue = +π (+180°)
    """
    return hue * math.pi


#
# FFmpeg Filter Generation
#
def get_ffmpeg_filter_string(settings, video_width: int | None = None, video_height: int | None = None):
    """The comma-separated FFmpeg filter chain, or "" when nothing changes.

    Raises CropError when crop margins are set but cannot be applied.
    """
    filters = []

    # Pixel-format negotiation belongs to the encoder backend. It must never
    # suppress the user's crop, colour, rotation or flip operations for HEVC.

    # 1. Add crop filter
    crop_left = get_adjustment_value(settings, "crop_left")
    crop_right = get_adjustment_value(settings, "crop_right")
    crop_top = get_adjustment_value(settings, "crop_top")
    crop_bottom = get_adjustment_value(settings, "crop_bottom")

    logger.debug(
        f"Video filters: crop_left={crop_left}, crop_right={crop_right}, crop_top={crop_top}, crop_bottom={crop_bottom}"
    )
    logger.debug(
        f"Video filters: video_width={video_width}, video_height={video_height}"
    )

    if crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0:
        if not video_width or not video_height:
            raise CropError("the video frame size is unknown")
        crop_width = video_width - crop_left - crop_right
        crop_height = video_height - crop_top - crop_bottom
        if crop_width <= 0 or crop_height <= 0 or min(crop_left, crop_right, crop_top, crop_bottom) < 0:
            raise CropError(
                f"crop margins {crop_left}/{crop_right}/{crop_top}/{crop_bottom} "
                f"leave no picture of a {video_width}x{video_height} frame")
        filters.append(f"crop={crop_width}:{crop_height}:{crop_left}:{crop_top}")

    # 2. Add eq filter with calibrated values (brightness, saturation)
    eq_parts = []

    # Preview and FFmpeg eq brightness share the -1.0..1.0 range, 0.0 neutral.
    brightness = get_adjustment_value(settings, "brightness")
    if abs(brightness) > FLOAT_THRESHOLD:
        eq_parts.append(f"brightness={brightness:.3f}")

    contrast = get_adjustment_value(settings, "contrast")
    if abs(contrast) > FLOAT_THRESHOLD:
        eq_parts.append(f"contrast={max(0.0, 1.0 + contrast):.3f}")

    saturation = get_adjustment_value(settings, "saturation")
    if abs(saturation - 1.0) > FLOAT_THRESHOLD:
        ffmpeg_saturation = preview_saturation_to_ffmpeg(saturation)
        eq_parts.append(f"saturation={ffmpeg_saturation:.3f}")

    # Effects before the colour adjustments, as the preview shows them.
    effect_file = settings.get_value("preview-effect-file", "") or ""
    effects = effects_graph(settings.get_value("preview-denoise", "off"),
                            settings.get_value("preview-sharpen", "off"), effect_file)
    if effects:
        filters.append(effects)

    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    # 3. Add hue filter separately (FFmpeg requires separate hue filter, not in eq)
    hue = get_adjustment_value(settings, "hue")
    if abs(hue) > FLOAT_THRESHOLD:
        ffmpeg_hue = preview_hue_to_ffmpeg(hue) * 180 / math.pi
        filters.append(f"hue=h={ffmpeg_hue:.3f}")

    # 4. Rotation (transpose for 90/270, hflip+vflip for 180)
    rotation = int(settings.load_setting("preview-rotation", 0))
    rotation = rotation % 360
    if rotation == 90:
        filters.append("transpose=1")
    elif rotation == 180:
        filters.append("hflip")
        filters.append("vflip")
    elif rotation == 270:
        filters.append("transpose=2")

    # 5. Flip (applied after rotation to match mpv behavior)
    flip_h = settings.get_boolean("preview-flip-h", False)
    flip_v = settings.get_boolean("preview-flip-v", False)
    if flip_h:
        filters.append("hflip")
    if flip_v:
        filters.append("vflip")

    # mpv runs a shader on the displayed picture, after the colour changes.
    if os.path.splitext(effect_file)[1].lower() in SHADER_SUFFIXES:
        filters.append(effect_file_filter(effect_file))

    return ",".join(filters)
