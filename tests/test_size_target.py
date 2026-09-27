"""How a video is made to fit a byte limit, decided before any encode."""

import math
from itertools import pairwise

import pytest

from utils.size_target import (
    INDEX_BYTES_PER_PACKET, PART_RESERVE, Source, SizeTargetError, keyframe_cuts, plan_size,
    target_bytes,
)

HD = Source(duration=600, size=400_000_000, width=1920, height=1080, fps=30)


def test_a_file_that_already_fits_is_copied_or_kept_at_quality():
    small = Source(60, 5_000_000, 1920, 1080, 30)
    assert plan_size(small, 16_000_000, copy_ok=True).kind == "copy"
    assert plan_size(small, 16_000_000, copy_ok=False).kind == "quality"


def test_small_targets_lower_the_bitrate_then_the_resolution():
    # 10 min into 200 MB: ~2.3 Mbit/s, too thin for 1080p H.264, fine at 720p.
    plan = plan_size(HD, 200_000_000, audio_bps=128_000)
    assert plan.kind == "bitrate"
    assert plan.resolution == "1280x720"
    assert plan.video_bitrate == int(200_000_000 * 0.92 * 8 / 600 - 128_000)
    # H.265 needs fewer bits a pixel and keeps 1080p at the same size.
    assert plan_size(HD, 200_000_000, codec="h265", audio_bps=128_000).resolution == ""
    # Half of that goes down to 480p rather than a starved 720p.
    assert plan_size(HD, 100_000_000, audio_bps=128_000).resolution == "854x480"


def test_keep_resolution_never_scales_and_refuses_the_impossible():
    plan = plan_size(HD, 100_000_000, strategy="keep_resolution", audio_bps=128_000)
    assert plan.kind == "bitrate" and plan.resolution == ""
    with pytest.raises(SizeTargetError):
        plan_size(HD, 1_000_000, strategy="keep_resolution")


def test_vertical_video_scales_its_short_side():
    phone = Source(600, 400_000_000, 1080, 1920, 30)
    assert plan_size(phone, 200_000_000, audio_bps=128_000).resolution == "720x1280"


def test_heavy_audio_is_reduced_before_the_picture_starves():
    plan = plan_size(Source(120, 90_000_000, 1920, 1080, 30), 16_000_000, audio_bps=320_000)
    assert plan.kind == "bitrate" and plan.audio_bitrate == 64_000


def test_a_long_video_into_a_chat_limit_is_split_instead_of_ruined():
    movie = Source(7200, 3_000_000_000, 1920, 1080, 24)
    plan = plan_size(movie, 16_000_000)
    assert plan.kind == "split"
    # ~38 s a part at the source's rate: a keyframe every 4 s or less.
    assert 0.5 <= plan.keyframe_interval <= 4


def _packets(seconds, video_bytes_a_second, gop=2.0, fps=25):
    """A plausible stream: a keyframe every ``gop`` s, audio every 1/50 s."""
    packets = []
    for frame in range(int(seconds * fps)):
        time = frame / fps
        key = math.isclose(time % gop, 0, abs_tol=1e-9) or math.isclose(time % gop, gop, abs_tol=1e-9)
        packets.append((time, video_bytes_a_second // fps * (5 if key else 1), key))
    packets += [(i / 50, 400, False) for i in range(int(seconds * 50))]
    return packets


def test_large_targets_split_on_keyframes_without_re_encoding():
    packets = _packets(600, 5_000_000 // 8)
    source = Source(600, sum(p[1] for p in packets), 1920, 1080, 25)
    target = 100_000_000
    plan = plan_size(source, target, copy_ok=True, packets=packets, strategy="split")
    assert plan.kind == "copy_split" and len(plan.cuts) > 1
    keys = {p[0] for p in packets if p[2]}
    for start, end in plan.cuts:
        assert start in keys and (end in keys or end == math.inf)
        part = [p for p in packets if start <= p[0] < end]
        assert sum(p[1] for p in part) + PART_RESERVE + INDEX_BYTES_PER_PACKET * len(part) <= target
    assert plan.cuts[0][0] == 0 and plan.cuts[-1][1] == math.inf
    assert all(a[1] == b[0] for a, b in pairwise(plan.cuts))


def test_a_keyframe_interval_bigger_than_the_target_cannot_be_copied():
    assert keyframe_cuts(_packets(60, 5_000_000, gop=30), 2_000_000) is None
    # Planning then falls back to splitting with a re-encode.
    packets = _packets(60, 5_000_000, gop=30)
    source = Source(60, sum(p[1] for p in packets), 1920, 1080, 25)
    assert plan_size(source, 2_000_000, copy_ok=True, packets=packets, strategy="split").kind == "split"


def test_parts_come_out_even_with_no_stub_at_the_end():
    # 21 keyframe intervals of 1.18 MB into 5 MB: filling parts in turn gives
    # 4,4,4,4,4,1 intervals; the even cuts give 4,3,4,3,4,3.
    packets = _packets(21, 1_000_000, gop=1)
    cuts = keyframe_cuts(packets, 5_000_000)
    sizes = [sum(p[1] for p in packets if start <= p[0] < end) for start, end in cuts]
    assert len(sizes) == 6 and max(sizes) <= 5_000_000
    assert min(sizes) >= 0.7 * max(sizes)


def test_targets():
    assert target_bytes("email") == 23_000_000
    assert target_bytes("fat32") == 4 * 1024**3 - 1
    assert target_bytes("custom", 50) == 50_000_000
    with pytest.raises(SizeTargetError):
        target_bytes("custom", 0)
    with pytest.raises(SizeTargetError):
        target_bytes("nope")


def _context(source, target, strategy="auto", **extra):
    from utils.size_target import prepare_size_job
    context = {"size_target": {"bytes": target, "strategy": strategy},
               "input_file": str(source), "output_ext": ".mkv", "trim_segments": [],
               "output_mode": "join",
               "encode_env": {"video_encoder": "h264", "audio_handling": "copy", "gpu": "auto"}}
    context.update(extra)
    return prepare_size_job(context)


def test_the_job_carries_what_the_script_needs(media):
    size = media['video'].stat().st_size
    copied = _context(media['video'], size * 2)
    assert copied["route"] == "single" and copied["env_vars"]["force_copy_video"] == "1"
    assert copied["env_vars"]["size_limit"] == str(size * 2)
    # Edited video cannot be copied: it is encoded at the chosen quality.
    edited = _context(media['video'], size * 2,
                      encode_env={"video_encoder": "copy", "video_filter": "hflip"})
    assert edited["plan"].kind == "quality" and edited["env_vars"]["video_encoder"] == "h264"
    squeezed = _context(media['video'], size // 2)
    assert squeezed["plan"].kind == "bitrate"
    assert int(squeezed["env_vars"]["video_bitrate"]) == squeezed["plan"].video_bitrate
    split = _context(media['video'], size // 2, "split")
    assert split["route"] == "batch" and split["trim_segments"][0]["start"] == 0.0
    assert split["plan"].kind in ("split", "copy_split")


def test_joined_cuts_split_but_separate_cuts_do_not(media):
    joined = _context(media['video'], 50_000, "split",
                      trim_segments=[{"start": 0, "end": 1}, {"start": 2, "end": 3}])
    assert joined["route"] == "batch" and joined["split_size"] == 50_000
    assert len(joined["trim_segments"]) == 2 and joined["keyframe_interval"] > 0
    with pytest.raises(SizeTargetError):
        _context(media['video'], 50_000, "split", output_mode="split",
                 trim_segments=[{"start": 0, "end": 1}, {"start": 2, "end": 3}])
