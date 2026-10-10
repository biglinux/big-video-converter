"""Deterministic crop-ratio geometry."""

from __future__ import annotations

ASPECT_RATIOS = {
    "free": None,
    "original": None,
    "16:9": 16 / 9,
    "9:16": 9 / 16,
    "1:1": 1.0,
    "4:5": 4 / 5,
    "3:2": 3 / 2,
    "2.39:1": 2.39,
}


def _even(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value - 1


def centered_crop(width: int, height: int, aspect: str, rotation: int = 0):
    width, height = int(width), int(height)
    if width < 2 or height < 2:
        raise ValueError("Video dimensions are unavailable")
    if aspect in {"", "free", "original"}:
        return 0, 0, 0, 0
    ratio = ASPECT_RATIOS.get(aspect)
    if ratio is None:
        raise ValueError(f"Unsupported crop ratio: {aspect}")
    if int(rotation) % 180:
        ratio = 1.0 / ratio
    if width / height > ratio:
        crop_height = _even(height)
        crop_width = _even(round(crop_height * ratio))
    else:
        crop_width = _even(width)
        crop_height = _even(round(crop_width / ratio))
    crop_width = min(crop_width, _even(width))
    crop_height = min(crop_height, _even(height))
    horizontal = max(0, width - crop_width)
    vertical = max(0, height - crop_height)
    left = horizontal // 2
    right = horizontal - left
    top = vertical // 2
    bottom = vertical - top
    return left, right, top, bottom
