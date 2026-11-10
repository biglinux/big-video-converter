"""Which GPU encodes a job: one rule for the GUI and the conversion script.

Among the GPUs that can encode the chosen codec, the one that alone encodes
AV1 wins, else the one that alone encodes VP9; with equal or unknown support
the order is NVIDIA, AMD, Intel. Support is read from the device (VAAPI
profiles, an NVENC test frame), never assumed from the vendor. Run as a
script, it prints "<vendor> <device> [<CUDA index>]" for the converter.
"""

import logging
import os
import subprocess
import sys
from functools import cache

try:
    from engine.ffmpeg_path import get_ffmpeg_executable
except ImportError:  # run as a script: this directory is on sys.path
    from ffmpeg_path import get_ffmpeg_executable

logger = logging.getLogger(__name__)

CODECS = ("h264", "h265", "av1", "vp9")

# FFmpeg encoder per vendor and codec; "" where the vendor's API has none.
_ENCODERS: dict[str, dict[str, str]] = {
    "nvidia": {
        "h264": "h264_nvenc",
        "h265": "hevc_nvenc",
        "av1": "av1_nvenc",
        "vp9": "",
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

_VENDOR_ORDER = ("nvidia", "amd", "intel")

_VAAPI_PROFILES = {
    "h264": "vaprofileh264",
    "h265": "vaprofilehevc",
    "av1": "vaprofileav1profile0",
    "vp9": "vaprofilevp9profile0",
}

# What NVENC prints when the card lacks the codec, as opposed to a driver or
# CUDA failure, which leaves the answer unknown.
_NVENC_UNSUPPORTED = ("codec not supported", "no capable devices", "unsupported device")

_PCI_VENDORS = {
    "0x10de": ("nvidia", "NVIDIA"),
    "0x1002": ("amd", "AMD"),
    "0x8086": ("intel", "Intel"),
}


@cache
def _available_ffmpeg_encoders() -> frozenset[str]:
    """Video encoder names this FFmpeg build reports."""
    try:
        result = subprocess.run(
            [get_ffmpeg_executable(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("Failed to query ffmpeg encoders: %s", error)
        return frozenset()
    encoders = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    return frozenset(encoders)


def render_devices(drm_dir: str = "/sys/class/drm") -> list[dict]:
    """Every render node with its vendor and PCI slot, read from sysfs.

    The name is a placeholder until name_render_devices() reads lspci, which
    can be slow and so runs on a worker.
    """
    try:
        nodes = sorted(n for n in os.listdir(drm_dir) if n.startswith("renderD"))
    except OSError:
        return []
    gpus = []
    for node in nodes:
        device_dir = os.path.join(drm_dir, node, "device")
        try:
            with open(os.path.join(device_dir, "vendor"), encoding="ascii") as f:
                vendor = f.read().strip().lower()
        except (OSError, UnicodeError):
            vendor = ""
        gpu_type, label = _PCI_VENDORS.get(vendor, ("unknown", ""))
        gpus.append(
            {
                "name": f"{label} ({node})" if label else node,
                "device": os.path.join("/dev/dri", node),
                "type": gpu_type,
                "slot": os.path.basename(os.path.realpath(device_dir)),
                "label": label,
            }
        )
    return gpus


def _pci_name(slot: str, vendor_label: str, run) -> str:
    """Commercial name of one PCI device, e.g. "NVIDIA TU116 [GeForce GTX 1660 Ti]"."""
    try:
        out = run(
            ["lspci", "-vmm", "-s", slot],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return ""
    fields = dict(line.split(":\t", 1) for line in out.splitlines() if ":\t" in line)
    device = fields.get("Device", "").strip()
    if not device:
        return ""
    name = f"{vendor_label or fields.get('Vendor', '').strip()} {device}".strip()
    return name[:47].rstrip() + "..." if len(name) > 50 else name


def name_render_devices(gpus: list[dict], run=subprocess.run) -> list[str]:
    """Display names for ``gpus``, from each node's own PCI device.

    Pairing by PCI slot keeps each name on its node when a VGA device, such
    as a BMC's, has no render node. Names that repeat get the node appended.
    """
    names = [_pci_name(gpu["slot"], gpu["label"], run) or gpu["name"] for gpu in gpus]
    return [
        f"{name} ({os.path.basename(gpu['device'])})" if names.count(name) > 1 else name
        for name, gpu in zip(names, gpus, strict=True)
    ]


@cache
def _vainfo_profiles(device_path: str) -> frozenset[str] | None:
    """Lowercase encode profiles of one VAAPI device, or None if vainfo failed.

    Cached for the session: each vainfo call can take seconds, and a device's
    encoders do not change while the application runs.
    """
    try:
        result = subprocess.run(
            ["vainfo", "--display", "drm", "--device", device_path],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "DISPLAY": ""},
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    profiles = set()
    for line in result.stdout.splitlines():
        low = line.strip().lower()
        if "vaprofile" in low and "entrypoint" in low and "encslice" in low:
            profiles.add(low.split()[0])
    return frozenset(profiles)


@cache
def _nvenc_encodes(encoder: str, cuda_index: int) -> bool | None:
    """Whether one NVIDIA card encodes a test frame with ``encoder``."""
    try:
        result = subprocess.run(
            [
                get_ffmpeg_executable(),
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=gray:s=256x256:r=1",
                "-frames:v",
                "1",
                "-c:v",
                encoder,
                "-gpu",
                str(cuda_index),
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            # -gpu counts the cards in PCI order only with this set.
            env={**os.environ, "CUDA_DEVICE_ORDER": "PCI_BUS_ID"},
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode == 0:
        return True
    error = result.stderr.lower()
    return False if any(text in error for text in _NVENC_UNSUPPORTED) else None


def cuda_index(gpu: dict, gpus: list[dict]) -> int:
    """The NVIDIA card's index in CUDA's PCI_BUS_ID order, as -gpu and cuda=cu: take it."""
    return sorted(g["slot"] for g in gpus if g["type"] == "nvidia").index(gpu["slot"])


def supports(gpu: dict, gpus: list[dict], codec: str) -> bool | None:
    """Whether one of ``gpus`` encodes ``codec``; None when it cannot be told."""
    encoder = _ENCODERS.get(gpu["type"], {}).get(codec, "")
    if not encoder:
        return None if gpu["type"] not in _ENCODERS else False
    if encoder not in _available_ffmpeg_encoders():
        return False
    if gpu["type"] == "nvidia":
        if codec != "av1":
            return True
        # AV1 encoding exists only on recent cards: ask this one.
        return _nvenc_encodes(encoder, cuda_index(gpu, gpus))
    profiles = _vainfo_profiles(gpu["device"])
    if profiles is None:
        return None
    return any(_VAAPI_PROFILES[codec] in profile for profile in profiles)


def choose_gpu(gpus: list[dict], codec: str, supports=supports):
    """The GPU of ``gpus`` that encodes ``codec``, by the rule above, or None."""
    codec = codec.lower()
    usable = [gpu for gpu in gpus if supports(gpu, gpus, codec) is True]
    if len(usable) <= 1:
        return usable[0] if usable else None
    for feature in ("av1", "vp9"):
        answers = [supports(gpu, gpus, feature) for gpu in usable]
        # An unknown answer could be the second card that has it.
        if None not in answers and answers.count(True) == 1:
            return usable[answers.index(True)]
    return min(usable, key=lambda gpu: _VENDOR_ORDER.index(gpu["type"]))


def select_best_gpu(detected_gpus: list[dict], codec: str) -> dict | None:
    """{"type", "device"} for the GUI's job, or None to let the script choose.

    The first call runs ffmpeg, vainfo and an NVENC test frame; call it from a
    worker first, which fills the caches.
    """
    if len(detected_gpus) < 2:
        return None
    best = choose_gpu(detected_gpus, codec)
    if best is None:
        return None
    logger.info("Selected GPU for %s: %s", codec, best["name"])
    return {"type": best["type"], "device": best["device"]}


if __name__ == "__main__":
    args = sys.argv[1:]
    # --without VENDOR leaves out a GPU whose encoder failed its check.
    without = ""
    if len(args) >= 2 and args[-2] == "--without":
        without = args[-1]
        args = args[:-2]
    if len(args) not in (1, 2) or args[0] not in CODECS:
        sys.exit(
            f"usage: {os.path.basename(sys.argv[0])} "
            f"{{{','.join(CODECS)}}} [DEVICE] [--without VENDOR]"
        )
    gpus = [gpu for gpu in render_devices() if gpu["type"] != without]
    if len(args) == 2:
        # A device the caller named is used as asked; only its vendor is needed.
        chosen = next((g for g in gpus if g["device"] == args[1]), None)
        if chosen is not None and chosen["type"] == "unknown":
            chosen = None
    else:
        chosen = choose_gpu(gpus, args[0])
    if chosen is not None:
        # The script initializes CUDA by index, in the PCI order it exports.
        index = cuda_index(chosen, gpus) if chosen["type"] == "nvidia" else ""
        print(chosen["type"], chosen["device"], index)
