"""Pure helpers for per-file queue configuration.

The GTK layer stores JSON-compatible metadata.  Before a job starts, the app
freezes an effective snapshot so later UI changes cannot mutate active work.
"""

from __future__ import annotations

import gettext
import math
from copy import deepcopy
from typing import Any

_ = gettext.gettext

RESOLUTION_MODES = (
    "global",
    "preset",
    "original",
    "3840x2160",
    "2560x1440",
    "1920x1080",
    "1280x720",
    "854x480",
    "2160x3840",
    "1440x2560",
    "1080x1920",
    "720x1280",
    "480x854",
    "custom",
)

DEFAULT_METADATA = {
    "trim_segments": [],
    "crop_left": 0,
    "crop_right": 0,
    "crop_top": 0,
    "crop_bottom": 0,
    "brightness": 0.0,
    "contrast": 0.0,
    "saturation": 1.0,
    "hue": 0.0,
    "rotation": 0,
    "flip_h": False,
    "flip_v": False,
    # Effects: noise reduction and sharpening levels (utils/video_settings),
    # two-pass stabilization, and a colour LUT (.cube/.3dl) or mpv shader
    # (.hook/.glsl) the user chose.
    "denoise": "off",
    "sharpen": "off",
    "stabilize": False,
    "effect_file": "",
    # How to read the source's colours when its file does not say (see the
    # script's source_hdr): "auto" trusts the file.
    "source_hdr": "auto",
    "output_mode": "join",
    "preset_id": None,
    "preset_snapshot": None,
    "resolution_mode": "global",
    "custom_width": None,
    "custom_height": None,
    # "global" follows the sidebar; "none", a target id or "custom" (size_mb).
    "size_mode": "global",
    "size_mb": None,
}

EFFECT_LEVELS = ("off", "light", "medium", "strong")
SOURCE_HDR_MODES = ("auto", "pq", "hlg", "sdr")

SIZE_MODES = ("global", "none", "custom", "whatsapp", "discord", "email", "100mb",
              "telegram", "fat32")


def normalize_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(DEFAULT_METADATA)
    if isinstance(value, dict):
        result.update(deepcopy(value))
    if result.get("resolution_mode") not in RESOLUTION_MODES:
        result["resolution_mode"] = "global"
    if result.get("size_mode") not in SIZE_MODES:
        result["size_mode"] = "global"
    for key in ("denoise", "sharpen"):
        if result.get(key) not in EFFECT_LEVELS:
            result[key] = "off"
    result["stabilize"] = result.get("stabilize") is True
    if result.get("source_hdr") not in SOURCE_HDR_MODES:
        result["source_hdr"] = "auto"
    effect_file = result.get("effect_file")
    if not isinstance(effect_file, str) or len(effect_file) > 4096 or "\0" in effect_file:
        result["effect_file"] = ""
    size_mb = result.get("size_mb")
    if (isinstance(size_mb, bool) or not isinstance(size_mb, (int, float))
            or not math.isfinite(size_mb) or not 1 <= size_mb <= 1_000_000):
        result["size_mb"] = None
        if result["size_mode"] == "custom":
            result["size_mode"] = "global"
    for key in ("custom_width", "custom_height"):
        raw = result.get(key)
        if raw in (None, "") or isinstance(raw, bool):
            result[key] = None
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            result[key] = None
            continue
        if not 16 <= number <= 16384:
            result[key] = None
            continue
        result[key] = number if number % 2 == 0 else number - 1
    return result


MAX_SEGMENTS = 10_000


def parse_segments_arg(text: str) -> list[dict[str, float]]:
    """Parse ``START-END[,START-END...]`` (seconds) given on the command line.

    Used by players that hand a file over with its cuts already marked.
    Raises ``ValueError`` on anything that is not a list of finite, ordered,
    non-negative ranges, so a bad hand-off never silently converts the whole
    file.
    """
    segments = []
    for part in text.split(","):
        start_text, sep, end_text = part.strip().partition("-")
        if not sep:
            raise ValueError(f"segment {part.strip()!r} is not START-END")
        start, end = float(start_text), float(end_text)
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise ValueError(f"segment {part.strip()!r} needs 0 <= start < end")
        if segments and start < segments[-1]["end"]:
            raise ValueError(_("The segment {0} starts before the previous one ends; "
                               "list the segments in order without overlaps.").format(part.strip()))
        segments.append({"start": start, "end": end})
    if len(segments) > MAX_SEGMENTS:
        raise ValueError(f"more than {MAX_SEGMENTS} segments")
    return segments


def effective_resolution(metadata: dict[str, Any], global_value: str = "") -> str:
    metadata = normalize_metadata(metadata)
    mode = metadata["resolution_mode"]
    if mode == "global":
        return global_value
    if mode == "preset":
        preset = metadata.get("preset_snapshot") or {}
        return str(preset.get("settings", {}).get("video-resolution", global_value))
    if mode == "original":
        return ""
    if mode == "custom":
        width = metadata.get("custom_width")
        height = metadata.get("custom_height")
        if (
            not width
            or not height
            or not 16 <= width <= 16384
            or not 16 <= height <= 16384
        ):
            raise ValueError("Custom resolution must be between 16 and 16384")
        return f"{width}x{height}"
    return mode


def has_picture_edits(metadata: dict[str, Any]) -> bool:
    """Cropped, adjusted or turned: a video that can no longer be copied."""
    return any(
        (
            metadata.get("crop_left"),
            metadata.get("crop_right"),
            metadata.get("crop_top"),
            metadata.get("crop_bottom"),
            abs(float(metadata.get("brightness", 0.0))) > 0.0001,
            abs(float(metadata.get("contrast", 0.0))) > 0.0001,
            abs(float(metadata.get("saturation", 1.0)) - 1.0) > 0.0001,
            abs(float(metadata.get("hue", 0.0))) > 0.0001,
            int(metadata.get("rotation", 0)) % 360,
            metadata.get("flip_h"),
            metadata.get("flip_v"),
            metadata.get("denoise", "off") != "off",
            metadata.get("sharpen", "off") != "off",
            metadata.get("stabilize"),
            metadata.get("effect_file"),
            metadata.get("source_hdr", "auto") != "auto",
        )
    )


def freeze(settings: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = normalize_metadata(metadata)
    effective = deepcopy(settings)
    preset = metadata.get("preset_snapshot")
    if isinstance(preset, dict):
        effective.update(deepcopy(preset.get("settings", {})))
    effective["video-resolution"] = effective_resolution(
        metadata, str(settings.get("video-resolution") or "")
    )
    if metadata["size_mode"] != "global":
        effective["size-target"] = "" if metadata["size_mode"] == "none" else metadata["size_mode"]
        if metadata["size_mode"] == "custom":
            effective["size-target-mb"] = float(metadata["size_mb"])
    edited = has_picture_edits(metadata) or (
        metadata["resolution_mode"] != "global" and bool(effective.get("video-resolution")))
    if edited:
        effective["force-copy-video"] = False
        if effective.get("video-codec") == "copy":
            effective["video-codec"] = "h264"
    return {"settings": effective, "metadata": metadata}


def snapshot_preset(preset) -> dict[str, Any]:
    """Create a validated, self-contained snapshot of a preset object."""

    from utils.presets import (
        _MAX_TEXT,
        parse_preset_text,
        preset_settings,
        validate_preset,
    )

    with open(preset.path, encoding="utf-8") as handle:
        source = handle.read(_MAX_TEXT + 1)
    validated = validate_preset(
        parse_preset_text(source),
        preset_id=preset.id,
        path=preset.path,
        bundled=bool(preset.bundled),
    )
    return {
        "id": validated.id,
        "name": validated.display_name,
        "source": source,
        "settings": deepcopy(preset_settings(validated)),
    }
