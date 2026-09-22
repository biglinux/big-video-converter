from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "big-video-converter/usr/share/big-video-converter"
sys.path.insert(0, os.fspath(SHARE))

from utils.crop_geometry import ASPECT_RATIOS, centered_crop


def output_size(width, height, margins):
    left, right, top, bottom = margins
    return width - left - right, height - top - bottom


def test_square_crop_is_centered_and_even():
    margins = centered_crop(1920, 1080, "1:1")
    width, height = output_size(1920, 1080, margins)
    assert width == height
    assert width % 2 == 0 and height % 2 == 0
    assert abs(margins[0] - margins[1]) <= 1


def test_vertical_ratio_is_interpreted_in_rotated_output_orientation():
    margins = centered_crop(1920, 1080, "9:16", rotation=90)
    width, height = output_size(1920, 1080, margins)
    assert abs(width / height - 16 / 9) < 0.01


def test_original_and_free_keep_the_whole_frame():
    assert centered_crop(1920, 1080, "original") == (0, 0, 0, 0)
    assert centered_crop(1920, 1080, "free") == (0, 0, 0, 0)


def test_all_social_ratios_fit_source_bounds():
    for ratio in ("16:9", "9:16", "1:1", "4:5", "3:2", "2.39:1"):
        margins = centered_crop(3840, 2160, ratio)
        width, height = output_size(3840, 2160, margins)
        assert 2 <= width <= 3840
        assert 2 <= height <= 2160
        assert width % 2 == 0 and height % 2 == 0


def test_ratio_order_matches_editor_choices():
    assert tuple(ASPECT_RATIOS) == (
        "free", "original", "16:9", "9:16", "1:1", "4:5", "3:2", "2.39:1"
    )
