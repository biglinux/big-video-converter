"""Fit a video under a byte limit: copy, split, or encode at a bitrate.

The rules below come from measurements on real footage (a 1080p music video):

* Encoders asked for an average bitrate miss it: two-pass x264/x265 by up to
  4 %, NVENC and Vulkan by up to 22 %, and AAC "96k" writes about 10 % more.
  A bitrate job therefore aims 8 % under the limit and is re-encoded with a
  corrected bitrate only when the result is still over it.
* Parts are cut by a stream copy on keyframes, from the source or from one
  encode made with a short GOP, so every byte of a part is known before it is
  written; only the container index (~16 bytes a packet) is added. The cuts
  are balanced: the parts come out about the same size, never a stub at the
  end. (FFmpeg's -fs was measured and dropped: it overshoots by a varying
  60-340 KB and leaves the flushed audio of the next part in each one.)
* Below ~0.06 bits per pixel H.264 looks worse than the same bitrate at a
  lower resolution (0.035 for H.265/VP9, 0.025 for AV1).
"""

from __future__ import annotations

import gettext
import json
import math
import subprocess
from dataclasses import dataclass

from utils.ffmpeg_path import get_ffprobe_executable
from utils.media_validation import probe_media

_ = gettext.gettext

# (id, label, bytes, size as shown). Decimal megabytes: the safe side of
# every service limit.
TARGETS = (
    ("whatsapp", _("WhatsApp video"), 16_000_000, "16 MB"),
    ("discord", _("Discord"), 20_000_000, "20 MB"),
    # Gmail counts 25 MB before encoding; 2 MB are left for the message.
    ("email", _("E-mail"), 23_000_000, "23 MB"),
    ("100mb", _("Upload sites"), 100_000_000, "100 MB"),
    ("telegram", _("Telegram or WhatsApp document"), 2_000_000_000, "2 GB"),
    # The largest file FAT32 can hold.
    ("fat32", _("FAT32 drive or Telegram Premium"), 4 * 1024**3 - 1, "4 GB"),
)
STRATEGIES = ("auto", "keep_resolution", "split")

BITRATE_MARGIN = 0.92
AAC_OVERSHOOT = 1.10
MIN_BPP = {"h264": 0.06, "h265": 0.035, "vp9": 0.035, "av1": 0.025}
SHORT_SIDES = (2160, 1440, 1080, 720, 480)
MIN_VIDEO_BPS = 100_000
# A copied part pays its container header and index on top of the packets.
PART_RESERVE = 64 * 1024
INDEX_BYTES_PER_PACKET = 16
# Splitting a big target without re-encoding keeps the quality and takes
# seconds; a small one (a chat, an e-mail) wants one file, so it is squeezed.
LARGE_TARGET = 1_000_000_000


class SizeTargetError(ValueError):
    """The video cannot be made to fit the way the user asked."""


@dataclass(frozen=True)
class Source:
    duration: float
    size: int
    width: int
    height: int
    fps: float


@dataclass(frozen=True)
class SizePlan:
    kind: str  # "copy" | "copy_split" | "quality" | "bitrate" | "split"
    video_bitrate: int = 0
    width: int = 0
    height: int = 0
    audio_bitrate: int = 0  # set when the audio has to shrink too
    cuts: tuple = ()  # copy_split: ((start, end), ...) in seconds
    keyframe_interval: float = 0  # split: the GOP the encode is cut on later

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else ""


def target_bytes(target_id: str, custom_mb: float = 0) -> int:
    if target_id == "custom":
        if not 1 <= custom_mb <= 1_000_000:
            raise SizeTargetError("Custom size must be between 1 MB and 1 TB")
        return int(custom_mb * 1_000_000)
    for known, _label, size, _shown in TARGETS:
        if known == target_id:
            return size
    raise SizeTargetError(f"Unknown size target: {target_id}")


def shown_size(target_id: str, custom_mb: float = 0) -> str:
    """The size as the user picked it: "16 MB", "4 GB", "50 MB"."""
    if target_id == "custom":
        return f"{custom_mb:g} MB"
    return next(shown for known, _label, _bytes, shown in TARGETS if known == target_id)


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def plan_size(source: Source, target: int, *, strategy="auto", codec="h264",
              copy_ok=False, audio_bps=0, audio_streams=1, packets=None) -> SizePlan:
    """Decide how this source fits under ``target`` bytes.

    ``copy_ok``: the video may be copied (no edits, a container that takes it).
    ``audio_bps``: what the audio will cost as configured, in bits a second.
    ``packets``: from :func:`probe_packets`, needed only to split by copying.
    """
    if strategy not in STRATEGIES:
        raise SizeTargetError(f"Unknown strategy: {strategy}")
    if source.duration <= 0:
        raise SizeTargetError("The video has no duration to divide the size by")
    if copy_ok and source.size <= target:
        return SizePlan("copy")
    if copy_ok and packets and (strategy == "split" or (
            strategy == "auto" and target >= LARGE_TARGET)):
        cuts = keyframe_cuts(packets, target)
        if cuts:
            return SizePlan("copy_split", cuts=cuts)
    if strategy == "split":
        return _split_plan(source, target)
    # A source that already fits converts at the chosen quality: aiming a
    # bitrate at a generous limit would only inflate it. The script checks the
    # result and retries at a bitrate in the rare case it came out bigger.
    if source.size <= target:
        return SizePlan("quality")

    total_bps = target * BITRATE_MARGIN * 8 / source.duration
    audio_bitrate = 0
    # Speech and music survive 64 kbps AAC better than the picture survives
    # losing a quarter of its bits.
    if audio_bps > total_bps / 4:
        audio_bitrate = 64_000
        audio_bps = audio_bitrate * AAC_OVERSHOOT * audio_streams
    video_bps = int(total_bps - audio_bps)

    short = min(source.width, source.height)
    sides = [short] + ([s for s in SHORT_SIDES if s < short] if strategy == "auto" else [])
    minimum = MIN_BPP.get(codec, MIN_BPP["h264"])
    chosen = None
    for side in sides:
        scale = side / short
        width, height = _even(source.width * scale), _even(source.height * scale)
        bpp = video_bps / (width * height * max(source.fps, 1))
        # The smallest size may run at half the ideal density before a split.
        if bpp >= minimum or (side == sides[-1] and bpp >= minimum / 2):
            chosen = (width, height) if side != short else (0, 0)
            break
    if chosen is None:
        if strategy == "auto":
            return _split_plan(source, target)
        if video_bps < MIN_VIDEO_BPS:
            raise SizeTargetError("The video is too long to fit this size at its resolution")
        chosen = (0, 0)
    return SizePlan("bitrate", video_bitrate=video_bps, width=chosen[0], height=chosen[1],
                    audio_bitrate=audio_bitrate)


def _split_plan(source: Source, target: int) -> SizePlan:
    """Encode once at the chosen quality with a GOP short enough to cut on.

    A part should hold at least ten keyframe intervals, so the cuts can even
    the parts out; the source's own bitrate stands in for the encode's.
    """
    part_seconds = target * source.duration / max(source.size, 1)
    interval = min(4.0, max(0.5, round(part_seconds / 10 * 2) / 2))
    return SizePlan("split", keyframe_interval=interval)


def _greedy_cuts(packets, limit: float):
    """Longest parts under ``limit``: cut times, or None if one GOP is too big."""
    cuts, start, part_bytes, part_count = [], packets[0][0], 0, 0
    last_key = None  # (time, part bytes before it, part packets before it)
    for time, size, key in packets:
        if key and time > start:
            last_key = (time, part_bytes, part_count)
        part_bytes += size
        part_count += 1
        if part_bytes + PART_RESERVE + INDEX_BYTES_PER_PACKET * part_count > limit:
            if last_key is None:
                return None
            cuts.append(last_key[0])
            start = last_key[0]
            part_bytes -= last_key[1]
            part_count -= last_key[2]
            last_key = None
    return cuts


def keyframe_cuts(packets, target: int) -> tuple | None:
    """Keyframe cut points that split the stream into even parts under ``target``.

    ``packets``: (seconds, bytes, is_video_keyframe). The fewest parts that fit
    come from filling each part in turn; the cuts then move to the keyframes
    nearest each 1/N of the bytes, so the parts share the size instead of the
    last one getting the remainder. None when a single keyframe interval does
    not fit on its own.
    """
    packets = sorted(packets, key=lambda packet: packet[0])
    if not packets:
        return None
    greedy = _greedy_cuts(packets, target)
    if greedy is None:
        return None
    count = len(greedy) + 1
    keys, total, packets_before = [], 0, 0  # (time, bytes before, packets before)
    for time, size, key in packets:
        if key and time > packets[0][0]:
            keys.append((time, total, packets_before))
        total += size
        packets_before += 1
    even, previous = [], (packets[0][0], 0, 0)
    for part in range(1, count):
        wanted = total * part / count
        later = [k for k in keys if k[0] > previous[0]]
        if not later:
            break
        previous = min(later, key=lambda k: abs(k[1] - wanted))
        even.append(previous)
    bounds = [(packets[0][0], 0, 0), *even, (math.inf, total, len(packets))]
    fits = len(even) == count - 1 and all(
        b[1] - a[1] + PART_RESERVE + INDEX_BYTES_PER_PACKET * (b[2] - a[2]) <= target
        for a, b in zip(bounds, bounds[1:]))
    cuts = [k[0] for k in even] if fits else greedy
    edges = [packets[0][0], *cuts, math.inf]
    return tuple(zip(edges, edges[1:]))


def probe_source(path: str) -> Source:
    data = probe_media(path)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise SizeTargetError("The file has no video stream")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = next((int(float(side.get("rotation", 0)))
                     for side in video.get("side_data_list", ()) if "rotation" in side), 0)
    if rotation % 180:
        width, height = height, width
    rate = next((r for r in (video.get("avg_frame_rate"), video.get("r_frame_rate"))
                 if r and not r.startswith("0")), "30/1")
    numerator, _slash, denominator = rate.partition("/")
    fps = float(numerator) / float(denominator or 1)
    return Source(float(data["format"].get("duration") or 0),
                  int(data["format"].get("size") or 0), width, height, fps)


def probe_packets(path: str) -> list:
    """(seconds, bytes, is_video_keyframe) of every audio and video packet."""
    out = subprocess.run(
        [get_ffprobe_executable(), "-v", "error",
         "-show_entries", "packet=codec_type,pts_time,dts_time,size,flags", "-of", "json", path],
        capture_output=True, text=True, timeout=600, check=True).stdout
    packets = []
    for packet in json.loads(out).get("packets", ()):
        if packet.get("codec_type") not in ("video", "audio"):
            continue
        time = packet.get("pts_time", "N/A")
        if time == "N/A":
            time = packet.get("dts_time", "N/A")
        if time == "N/A":
            continue
        key = packet.get("codec_type") == "video" and "K" in packet.get("flags", "")
        packets.append((float(time), int(packet.get("size", 0)), key))
    return packets


_ENCODABLE = ("h264", "h265", "av1", "vp9")


def _audio_cost(data: dict, env: dict) -> tuple[int, int]:
    """(bits a second, streams) the audio will take as the job is set up."""
    streams = [s for s in data["streams"] if s.get("codec_type") == "audio"]
    handling = env.get("audio_handling", "copy")
    if not streams or handling == "none":
        return 0, 0
    if handling == "copy":
        total = 0
        for stream in streams:
            # Matroska keeps the rate in a BPS tag rather than bit_rate.
            rate = stream.get("bit_rate") or (stream.get("tags") or {}).get("BPS")
            total += int(rate) if str(rate or "").isdigit() else 128_000
        return total, len(streams)
    bitrate = env.get("audio_bitrate") or "128k"
    value = float(bitrate.rstrip("kKmM")) * (1000 if bitrate[-1] in "kK" else
                                             1_000_000 if bitrate[-1] in "mM" else 1)
    return int(value * AAC_OVERSHOOT) * len(streams), len(streams)


def prepare_size_job(context: dict) -> dict:
    """Turn a conversion context into the job that fits its size target.

    Probes the source (off the GTK thread) and returns a new context whose
    "route" says who runs it: "single" (one encode or copy) or "batch" (parts).
    Every encode carries size_limit, so the script re-encodes a result that
    came out over the limit, and refuses to publish one that stays over it.
    """
    target = context["size_target"]["bytes"]
    strategy = context["size_target"]["strategy"]
    source_path = context["input_file"]
    encode_env = dict(context["encode_env"])
    encode_env.pop("force_copy_video", None)
    if encode_env.get("video_encoder") not in _ENCODABLE:
        encode_env["video_encoder"] = "h264"
    data = probe_media(source_path)
    source = probe_source(source_path)
    cuts = context["trim_segments"]
    if cuts:
        # Split cuts all get the bitrate the longest needs, so none is over.
        length = (sum(c["end"] - c["start"] for c in cuts) if context["output_mode"] == "join"
                  else max(c["end"] - c["start"] for c in cuts))
        source = Source(length, int(source.size * length / max(source.duration, 0.001)),
                        source.width, source.height, source.fps)
    copy_ok = not cuts and not any(encode_env.get(key) for key in (
        "video_filter", "video_resolution", "video_fps", "video_stabilize", "source_hdr"))
    if copy_ok and context["output_ext"] == ".mp4":
        from utils.file_info import check_mp4_compatibility

        copy_ok = check_mp4_compatibility(source_path)[0]
    audio_bps, audio_streams = _audio_cost(data, encode_env)
    packets = probe_packets(source_path) if copy_ok and (
        strategy == "split" or target >= LARGE_TARGET) and source.size > target else None
    plan = plan_size(source, target, strategy=strategy, codec=encode_env["video_encoder"],
                     copy_ok=copy_ok, audio_bps=audio_bps, audio_streams=audio_streams,
                     packets=packets)
    if len(cuts) > 1 and context["output_mode"] == "split" and plan.kind == "split":
        raise SizeTargetError(
            "Cuts saved as separate files cannot be split again; join them or choose a larger size")

    copy_env = {key: value for key, value in encode_env.items() if key not in (
        "video_filter", "video_resolution", "video_fps", "video_stabilize", "source_hdr", "video_quality",
        "video_encoder", "preset", "gpu_device", "video_bitrate")}
    copy_env.update(force_copy_video="1", gpu="software")
    limit = {"size_limit": str(target)}
    job = dict(context, plan=plan, route="single", split_size=None)
    if plan.kind == "copy":
        job["env_vars"] = {**copy_env, **limit}
    elif plan.kind == "copy_split":
        # Packet times count from the stream start; a cut counts from the file's.
        offset = float(data["format"].get("start_time") or 0)
        job.update(route="batch", env_vars={**copy_env, **limit}, output_mode="split",
                   trim_segments=[{"start": max(0.0, start - offset),
                                   "end": min(end - offset, source.duration)}
                                  for start, end in plan.cuts])
    elif plan.kind == "split":
        job.update(route="batch", env_vars=encode_env, output_mode="split",
                   split_size=target, keyframe_interval=plan.keyframe_interval,
                   trim_segments=cuts or [{"start": 0.0, "end": source.duration}])
    else:
        env = {**encode_env, **limit}
        if plan.kind == "bitrate":
            env["video_bitrate"] = str(plan.video_bitrate)
            if plan.resolution:
                env["video_resolution"] = plan.resolution
            if plan.audio_bitrate:
                env.update(audio_handling="reencode", audio_codec="aac",
                           audio_bitrate=f"{plan.audio_bitrate // 1000}k")
        job["env_vars"] = env
    return job


def describe_plan(job: dict) -> str:
    """What the size target will do to this video, in one line."""
    plan = job["plan"]
    if plan.kind == "copy":
        return _("Copied as it is: it already fits")
    if plan.kind == "quality":
        return _("Converted at the chosen quality: it already fits")
    if plan.kind == "copy_split":
        count = len(plan.cuts)
        return gettext.ngettext("Split without re-encoding into {0} part",
                                "Split without re-encoding into {0} parts", count).format(count)
    if plan.kind == "split":
        return _("Converted at the chosen quality, then split into even parts")
    picture = plan.resolution.replace("x", "×") if plan.resolution else _("original resolution")
    text = _("{picture} at {rate} Mbit/s").format(
        picture=picture, rate=f"{plan.video_bitrate / 1_000_000:.1f}")
    if plan.audio_bitrate:
        text += " · " + _("audio at {rate} kbit/s").format(rate=plan.audio_bitrate // 1000)
    return text
