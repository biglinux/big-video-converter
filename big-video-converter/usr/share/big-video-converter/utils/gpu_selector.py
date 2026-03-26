"""Intelligent GPU selection based on codec support and performance."""

import logging
import os
import subprocess
from functools import lru_cache

logger = logging.getLogger(__name__)

# Encoder names per GPU type per codec
_ENCODER_MAP: dict[str, dict[str, str]] = {
    "nvidia": {
        "h264": "h264_nvenc",
        "h265": "hevc_nvenc",
        "av1": "av1_nvenc",
        "vp9": "vp9_nvenc",
    },
    "amd": {
        "h264": "h264_vaapi",
        "h265": "hevc_vaapi",
        "av1": "av1_vaapi",
        "vp9": "vp9_vaapi",
    },
    "intel": {
        "h264": "h264_qsv",
        "h265": "hevc_qsv",
        "av1": "av1_qsv",
        "vp9": "vp9_qsv",
    },
}

# Performance ranking per codec (higher = faster).
# Based on typical hardware encoder throughput.
_PERF_RANK: dict[str, dict[str, int]] = {
    "h264": {"nvidia": 3, "intel": 2, "amd": 1},
    "h265": {"nvidia": 3, "intel": 2, "amd": 1},
    "av1": {"nvidia": 3, "intel": 2, "amd": 1},
    "vp9": {"intel": 2, "amd": 1, "nvidia": 0},
}


@lru_cache(maxsize=1)
def _available_ffmpeg_encoders() -> frozenset[str]:
    """Return the set of encoder names that ffmpeg reports as available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        encoders: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("V"):
                encoders.add(parts[1])
        return frozenset(encoders)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"Failed to query ffmpeg encoders: {e}")
        return frozenset()


def _classify_gpu(name: str) -> str:
    """Classify a GPU name string into nvidia/amd/intel."""
    low = name.lower()
    if "nvidia" in low:
        return "nvidia"
    if "intel" in low:
        return "intel"
    if "amd" in low or " ati " in low or "advanced micro devices" in low:
        return "amd"
    return "unknown"


def _vainfo_profiles(device_path: str) -> set[str]:
    """Query vainfo for supported encode profiles on a specific device.

    Returns set of lowercase profile names (e.g. 'vaprofilehevcmain').
    """
    try:
        env = os.environ.copy()
        env["DISPLAY"] = ""  # avoid X11 issues
        result = subprocess.run(
            ["vainfo", "--display", "drm", "--device", device_path],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            check=False,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return set()
    else:
        profiles: set[str] = set()
        for line in result.stdout.splitlines():
            low = line.strip().lower()
            if "vaprofile" in low and "entrypoint" in low and "encslice" in low:
                profiles.add(low.split()[0])
        return profiles


def _vainfo_supports_codec(device_path: str, codec: str) -> bool:
    """Check if a VAAPI device supports a specific codec for encoding."""
    codec_profile_map = {
        "h264": "vaprofileh264",
        "h265": "vaprofilehevc",
        "av1": "vaprofileav1profile0",
        "vp9": "vaprofilevp9profile0",
    }
    prefix = codec_profile_map.get(codec, "")
    if not prefix:
        return False
    profiles = _vainfo_profiles(device_path)
    return any(prefix in p for p in profiles)


def select_best_gpu(
    detected_gpus: list[dict],
    codec: str,
) -> dict | None:
    """Select the best GPU for the given codec.

    Args:
        detected_gpus: List of dicts with 'name' and 'device' keys.
        codec: Target codec (h264, h265, av1, vp9).

    Returns:
        Dict with 'type' and 'device' keys for the best GPU, or None
        to let the bash script handle auto-detection.
    """
    if not detected_gpus or len(detected_gpus) < 2:
        return None

    codec = codec.lower()
    available_encoders = _available_ffmpeg_encoders()
    perf = _PERF_RANK.get(codec, {})

    candidates: list[tuple[int, str, str, str]] = []  # (score, type, device, name)

    for gpu in detected_gpus:
        gpu_type = _classify_gpu(gpu.get("name", ""))
        if gpu_type == "unknown":
            continue

        encoder_name = _ENCODER_MAP.get(gpu_type, {}).get(codec, "")
        if not encoder_name:
            continue

        # Check if ffmpeg has this encoder compiled in
        if encoder_name not in available_encoders:
            logger.debug(
                f"GPU {gpu['name']}: encoder {encoder_name} not in ffmpeg"
            )
            continue

        # For VAAPI/QSV GPUs, verify hardware actually supports the codec
        if gpu_type in ("amd", "intel"):
            device = gpu.get("device", "")
            if device and not _vainfo_supports_codec(device, codec):
                logger.debug(
                    f"GPU {gpu['name']}: vainfo says no {codec} encode support on {device}"
                )
                continue

        score = perf.get(gpu_type, 0)
        candidates.append((score, gpu_type, gpu.get("device", ""), gpu.get("name", "")))
        logger.debug(
            f"GPU candidate: {gpu['name']} type={gpu_type} encoder={encoder_name} score={score}"
        )

    if not candidates:
        logger.debug("No GPU candidates found, falling back to bash auto-detect")
        return None

    # Sort by score descending
    candidates.sort(key=lambda c: c[0], reverse=True)
    best = candidates[0]

    logger.info(
        f"Selected GPU for {codec}: {best[3]} (type={best[1]}, score={best[0]})"
    )
    return {"type": best[1], "device": best[2]}
