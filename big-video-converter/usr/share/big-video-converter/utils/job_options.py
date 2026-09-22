"""Pure helpers for per-file queue configuration.

The GTK layer stores JSON-compatible metadata.  Before a job starts, the app
freezes an effective snapshot so later UI changes cannot mutate active work.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

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
    "output_mode": "join",
    "preset_id": None,
    "preset_snapshot": None,
    "resolution_mode": "global",
    "custom_width": None,
    "custom_height": None,
}


def normalize_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(DEFAULT_METADATA)
    if isinstance(value, dict):
        result.update(deepcopy(value))
    if result.get("resolution_mode") not in RESOLUTION_MODES:
        result["resolution_mode"] = "global"
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


def freeze(settings: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = normalize_metadata(metadata)
    effective = deepcopy(settings)
    preset = metadata.get("preset_snapshot")
    if isinstance(preset, dict):
        effective.update(deepcopy(preset.get("settings", {})))
    effective["video-resolution"] = effective_resolution(
        metadata, str(settings.get("video-resolution") or "")
    )
    edited = any(
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
            metadata["resolution_mode"] != "global"
            and bool(effective.get("video-resolution")),
        )
    )
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
