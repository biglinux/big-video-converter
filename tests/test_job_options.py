"""Per-video choices must resolve independently of the general recipe."""

import pytest
from engine.job_options import freeze


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


def test_only_individual_resize_disables_copy():
    general = {"force-copy-video": True, "video-resolution": "1920x1080"}
    assert freeze(general, {})["settings"]["force-copy-video"] is True
    assert (
        freeze(general, {"resolution_mode": "1280x720"})["settings"]["force-copy-video"]
        is False
    )


@pytest.mark.parametrize("width", [0, 15, 16385, "invalid"])
def test_invalid_custom_dimensions_are_rejected(width):
    with pytest.raises(ValueError):
        freeze(
            {},
            {"resolution_mode": "custom", "custom_width": width, "custom_height": 720},
        )


def test_segments_arg_parses_ordered_ranges():
    from engine.job_options import parse_segments_arg

    assert parse_segments_arg("1-3,3-5") == [
        {"start": 1.0, "end": 3.0},
        {"start": 3.0, "end": 5.0},
    ]
    assert parse_segments_arg("1.5-3, 10-12.25") == [
        {"start": 1.5, "end": 3.0},
        {"start": 10.0, "end": 12.25},
    ]


@pytest.mark.parametrize(
    "text",
    ["", "5", "3-1", "2-2", "-1-4", "a-b", "1-inf", "nan-3", "10-12,1-3", "1-5,4-6"],
)
def test_segments_arg_rejects_malformed_ranges(text):
    from engine.job_options import parse_segments_arg

    with pytest.raises(ValueError):
        parse_segments_arg(text)


def test_video_has_its_own_size_limit():
    general = {"size-target": "whatsapp", "size-target-mb": 50.0}
    assert freeze(general, {})["settings"]["size-target"] == "whatsapp"
    assert freeze(general, {"size_mode": "none"})["settings"]["size-target"] == ""
    assert freeze(general, {"size_mode": "fat32"})["settings"]["size-target"] == "fat32"
    custom = freeze(general, {"size_mode": "custom", "size_mb": 30})["settings"]
    assert (custom["size-target"], custom["size-target-mb"]) == ("custom", 30.0)


@pytest.mark.parametrize(
    "metadata",
    [
        {"size_mode": "nope"},
        {"size_mode": "custom", "size_mb": 0},
        {"size_mode": "custom", "size_mb": True},
        {"size_mode": "custom", "size_mb": float("nan")},
    ],
)
def test_invalid_size_limit_uses_general_one(metadata):
    assert (
        freeze({"size-target": "email"}, metadata)["settings"]["size-target"] == "email"
    )


def test_effects_normalize_and_count_as_edits():
    from engine.job_options import has_picture_edits, normalize_metadata

    clean = normalize_metadata(
        {"denoise": "extreme", "sharpen": 3, "stabilize": "yes", "effect_file": "x\0y"}
    )
    assert (
        clean["denoise"],
        clean["sharpen"],
        clean["stabilize"],
        clean["effect_file"],
    ) == ("off", "off", False, "")
    assert not has_picture_edits(clean)
    assert normalize_metadata({"source_hdr": "dolby"})["source_hdr"] == "auto"
    for edit in (
        {"denoise": "light"},
        {"sharpen": "strong"},
        {"stabilize": True},
        {"effect_file": "/looks/warm.cube"},
        {"source_hdr": "pq"},
    ):
        assert has_picture_edits(normalize_metadata(edit)), edit


def test_join_merges_overlapping_unordered_cuts():
    from engine.job_options import joined_cuts

    cuts = [
        {"start": 5, "end": 8},
        {"start": 0, "end": 2},
        {"start": 1, "end": 3},
        {"start": 7, "end": 9},
        {"start": 3, "end": 4},
    ]
    assert joined_cuts(cuts) == [{"start": 0, "end": 4}, {"start": 5, "end": 9}]
