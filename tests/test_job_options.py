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


def test_segments_arg_parses_ordered_ranges():
    from utils.job_options import parse_segments_arg

    assert parse_segments_arg("1-3,3-5") == [{"start": 1.0, "end": 3.0}, {"start": 3.0, "end": 5.0}]
    assert parse_segments_arg("1.5-3, 10-12.25") == [
        {"start": 1.5, "end": 3.0},
        {"start": 10.0, "end": 12.25},
    ]


@pytest.mark.parametrize("text", ["", "5", "3-1", "2-2", "-1-4", "a-b", "1-inf", "nan-3",
                                  "10-12,1-3", "1-5,4-6"])
def test_segments_arg_rejects_malformed_ranges(text):
    from utils.job_options import parse_segments_arg

    with pytest.raises(ValueError):
        parse_segments_arg(text)


def test_a_video_can_have_its_own_size_limit():
    general = {"size-target": "whatsapp", "size-target-mb": 50.0}
    assert freeze(general, {})["settings"]["size-target"] == "whatsapp"
    assert freeze(general, {"size_mode": "none"})["settings"]["size-target"] == ""
    assert freeze(general, {"size_mode": "fat32"})["settings"]["size-target"] == "fat32"
    custom = freeze(general, {"size_mode": "custom", "size_mb": 30})["settings"]
    assert (custom["size-target"], custom["size-target-mb"]) == ("custom", 30.0)


@pytest.mark.parametrize("metadata", [
    {"size_mode": "nope"},
    {"size_mode": "custom", "size_mb": 0},
    {"size_mode": "custom", "size_mb": True},
    {"size_mode": "custom", "size_mb": float("nan")},
])
def test_an_invalid_size_limit_falls_back_to_the_general_one(metadata):
    assert freeze({"size-target": "email"}, metadata)["settings"]["size-target"] == "email"


def test_effects_are_normalized_and_count_as_picture_edits():
    from utils.job_options import has_picture_edits, normalize_metadata

    clean = normalize_metadata({"denoise": "extreme", "sharpen": 3, "stabilize": "yes",
                                "effect_file": "x\0y"})
    assert (clean["denoise"], clean["sharpen"], clean["stabilize"], clean["effect_file"]) == (
        "off", "off", False, "")
    assert not has_picture_edits(clean)
    assert normalize_metadata({"source_hdr": "dolby"})["source_hdr"] == "auto"
    for edit in ({"denoise": "light"}, {"sharpen": "strong"}, {"stabilize": True},
                 {"effect_file": "/looks/warm.cube"}, {"source_hdr": "pq"}):
        assert has_picture_edits(normalize_metadata(edit)), edit
