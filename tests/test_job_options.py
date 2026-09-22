"""Per-video choices must resolve independently of the general recipe."""

import pytest
from utils.job_options import freeze


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("global", "1920x1080"),
        ("preset", "1280x720"),
        ("original", ""),
        ("720x1280", "720x1280"),
        ("custom", "640x360"),
    ],
)
def test_resolution_modes_have_distinct_precedence(mode, expected):
    general = {"video-resolution": "1920x1080", "video-codec": "h265"}
    metadata = {
        "resolution_mode": mode,
        "custom_width": 641,
        "custom_height": 361,
        "preset_snapshot": {
            "settings": {"video-resolution": "1280x720", "video-codec": "h264"}
        },
    }
    resolved = freeze(general, metadata)["settings"]
    assert resolved["video-resolution"] == expected
    assert resolved["video-codec"] == "h264"
    assert general["video-codec"] == "h265"


def test_copy_ignores_stale_general_size_but_individual_resize_reencodes():
    general = {"force-copy-video": True, "video-resolution": "1920x1080"}
    assert freeze(general, {})["settings"]["force-copy-video"] is True
    assert (
        freeze(general, {"resolution_mode": "1280x720"})["settings"]["force-copy-video"]
        is False
    )


@pytest.mark.parametrize("width", [0, 15, 16385, "invalid"])
def test_invalid_custom_dimensions_cannot_start_a_job(width):
    with pytest.raises(ValueError):
        freeze(
            {},
            {"resolution_mode": "custom", "custom_width": width, "custom_height": 720},
        )
