"""An NVIDIA driver of a legacy branch, whose NVENC the packaged FFmpeg refuses.

NVIDIA's 580 branch is the last one for the GeForce 10 series and older cards,
and FFmpeg 9 asks for the NVENC API of driver 610. The Jellyfin portable build
of FFmpeg (``jellyfin-ffmpeg-bin`` in the AUR) still encodes on those drivers.
"""

import subprocess

LEGACY_LAST_BRANCH = 580
PACKAGE = "jellyfin-ffmpeg-bin"


def driver_version(run=subprocess.run) -> str:
    """The NVIDIA kernel module's version, read without loading it; "" without one."""
    try:
        result = run(
            ["modinfo", "-F", "version", "nvidia"],
            capture_output=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def is_legacy(version: str) -> bool:
    """Whether ``version`` belongs to the 580 branch or an older one."""
    major = version.split(".", 1)[0]
    return major.isdigit() and int(major) <= LEGACY_LAST_BRANCH


def package_installed(run=subprocess.run) -> bool:
    try:
        result = run(
            ["pacman", "-Q", PACKAGE], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def should_offer(version: str, asked_for: str, installed: bool) -> bool:
    """Offer the package once per driver version, while it is not installed."""
    return is_legacy(version) and not installed and asked_for != version
