from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "big-video-converter/usr/share/big-video-converter"
sys.path.insert(0, os.fspath(SHARE))

from utils.job_options import (
    default_metadata,
    effective_resolution,
    freeze,
    normalize_metadata,
    summary,
)


def test_custom_resolution_is_normalized_to_even_dimensions():
    metadata = normalize_metadata(
        {"resolution_mode": "custom", "custom_width": 721, "custom_height": 1281}
    )
    assert effective_resolution(metadata) == "720x1280"


def test_file_resolution_overrides_general_resolution():
    metadata = default_metadata()
    metadata["resolution_mode"] = "1280x720"
    snapshot = freeze(
        {
            "video-resolution": "3840x2160",
            "force-copy-video": True,
            "video-codec": "copy",
        },
        metadata,
    )
    assert snapshot["settings"]["video-resolution"] == "1280x720"
    assert snapshot["settings"]["force-copy-video"] is False
    assert snapshot["settings"]["video-codec"] == "h264"


def test_original_resolution_explicitly_disables_scaling():
    metadata = default_metadata()
    metadata["resolution_mode"] = "original"
    assert effective_resolution(metadata, "1920x1080") == ""


def test_preset_then_file_override_precedence():
    metadata = default_metadata()
    metadata["preset_snapshot"] = {
        "name": "YouTube",
        "settings": {"video-resolution": "1920x1080", "video-codec": "h264"},
    }
    metadata["resolution_mode"] = "720x1280"
    snapshot = freeze(
        {"video-resolution": "3840x2160", "video-codec": "h265"}, metadata
    )
    assert snapshot["settings"]["video-codec"] == "h264"
    assert snapshot["settings"]["video-resolution"] == "720x1280"


def test_snapshot_does_not_share_nested_editor_state():
    metadata = default_metadata()
    metadata["trim_segments"].append({"start": 1.0, "end": 2.0})
    snapshot = freeze({}, metadata)
    metadata["trim_segments"][0]["end"] = 9.0
    assert snapshot["metadata"]["trim_segments"][0]["end"] == 2.0


def test_summary_exposes_profile_and_effective_size():
    metadata = default_metadata()
    metadata.update(
        preset_snapshot={"name": "WhatsApp"},
        resolution_mode="720x1280",
    )
    assert summary(metadata) == "WhatsApp · 720×1280"
