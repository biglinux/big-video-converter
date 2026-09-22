"""Pure helpers for per-file queue configuration.

The GTK layer stores JSON-compatible metadata.  Before a job starts, the app
freezes an effective snapshot so later UI changes cannot mutate active work.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

RESOLUTION_MODES = (
    "global", "preset", "original", "3840x2160", "2560x1440",
    "1920x1080", "1280x720", "854x480", "2160x3840",
    "1440x2560", "1080x1920", "720x1280", "480x854", "custom",
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


def default_metadata() -> dict[str, Any]:
    return deepcopy(DEFAULT_METADATA)


def normalize_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    result = default_metadata()
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
        result[key] = number if number % 2 == 0 else number - 1
    return result


def effective_resolution(metadata: dict[str, Any], global_value: str = "") -> str:
    metadata = normalize_metadata(metadata)
    mode = metadata["resolution_mode"]
    if mode in {"global", "preset"}:
        return global_value
    if mode == "original":
        return ""
    if mode == "custom":
        width = metadata.get("custom_width")
        height = metadata.get("custom_height")
        if not width or not height or not 16 <= width <= 16384 or not 16 <= height <= 16384:
            raise ValueError("Custom resolution must be between 16 and 16384")
        return f"{width}x{height}"
    return mode


def summary(metadata: dict[str, Any] | None) -> str:
    metadata = normalize_metadata(metadata)
    parts = []
    preset = metadata.get("preset_snapshot")
    if isinstance(preset, dict) and preset.get("name"):
        parts.append(str(preset["name"]))
    mode = metadata["resolution_mode"]
    if mode == "custom" and metadata.get("custom_width") and metadata.get("custom_height"):
        parts.append(f"{metadata['custom_width']}×{metadata['custom_height']}")
    elif mode == "original":
        parts.append("Original")
    elif mode not in {"global", "preset"}:
        parts.append(mode.replace("x", "×"))
    return " · ".join(parts) or "Global settings"


def freeze(settings: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = normalize_metadata(metadata)
    effective = deepcopy(settings)
    preset = metadata.get("preset_snapshot")
    if isinstance(preset, dict):
        effective.update(deepcopy(preset.get("settings", {})))
    effective["video-resolution"] = effective_resolution(
        metadata, str(effective.get("video-resolution") or "")
    )
    edited = any(
        (
            metadata.get("crop_left"), metadata.get("crop_right"),
            metadata.get("crop_top"), metadata.get("crop_bottom"),
            abs(float(metadata.get("brightness", 0.0))) > 0.0001,
            abs(float(metadata.get("contrast", 0.0))) > 0.0001,
            abs(float(metadata.get("saturation", 1.0)) - 1.0) > 0.0001,
            abs(float(metadata.get("hue", 0.0))) > 0.0001,
            int(metadata.get("rotation", 0)) % 360,
            metadata.get("flip_h"), metadata.get("flip_v"),
            bool(effective.get("video-resolution")),
        )
    )
    if edited:
        effective["force-copy-video"] = False
        if effective.get("video-codec") == "copy":
            effective["video-codec"] = "h264"
    frozen = {"settings": effective, "metadata": deepcopy(metadata)}
    frozen["signature"] = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return frozen


def snapshot_preset(preset) -> dict[str, Any]:
    """Create a validated, self-contained snapshot of a preset object."""

    from pathlib import Path
    from utils.presets import load_preset, preset_environment, preset_settings

    validated = load_preset(preset.path, bundled=bool(preset.bundled))
    source = Path(validated.path).read_text(encoding="utf-8")
    return {
        "id": validated.id,
        "name": validated.display_name,
        "summary": validated.summary,
        "source": source,
        "settings": deepcopy(preset_settings(validated)),
        "environment": deepcopy(preset_environment(validated)),
        "encoders": deepcopy(validated.encoders),
    }
