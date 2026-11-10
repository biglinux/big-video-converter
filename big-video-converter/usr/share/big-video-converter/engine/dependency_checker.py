import gettext
import importlib
import logging
import os
import shlex
import shutil
import subprocess

logger = logging.getLogger(__name__)

_ = gettext.gettext


def get_distro_info():
    """Detect host distribution information."""
    distro_info = {"id": None, "base": None}
    try:
        with open("/etc/os-release", "r") as f:
            lines = f.readlines()
        for line in lines:
            if line.startswith("ID="):
                distro_info["id"] = line.strip().split("=")[1].strip("\"'")
            elif line.startswith("ID_LIKE="):
                bases = line.strip().split("=")[1].strip("\"'").split()
                if "arch" in bases:
                    distro_info["base"] = "arch"
                elif "debian" in bases:
                    distro_info["base"] = "debian"
                elif "fedora" in bases:
                    distro_info["base"] = "rpm"
    except FileNotFoundError:
        pass

    if not distro_info["base"]:
        if distro_info["id"] in ["arch", "manjaro", "endeavouros"]:
            distro_info["base"] = "arch"
        elif distro_info["id"] in ["debian", "ubuntu", "linuxmint", "pop"]:
            distro_info["base"] = "debian"
        elif distro_info["id"] in ["fedora", "centos", "rhel", "nobara", "almalinux"]:
            distro_info["base"] = "rpm"

    return distro_info


def _loads(module: str) -> bool:
    """python-mpv raises OSError when libmpv itself is missing."""
    try:
        importlib.import_module(module)
    except (ImportError, OSError):
        return False
    return True


# What each distro family installs. The AppImage carries python-mpv and
# PyOpenGL in its own Python, so it only needs FFmpeg and libmpv from the
# system: the distro's python3-mpv would go to a Python the AppImage never
# runs, and Fedora does not even package it.
_PACKAGES = {
    "arch": (["ffmpeg", "python-mpv", "python-opengl"], ["ffmpeg", "mpv"]),
    "debian": (["ffmpeg", "python3-mpv", "python3-opengl"], ["ffmpeg", "libmpv2"]),
    "rpm": (["ffmpeg", "python3-mpv", "python3-pyopengl"], ["ffmpeg", "mpv-libs"]),
}


def _packages_for(base, appimage: bool) -> list:
    native, bundled = _PACKAGES.get(base, ([], []))
    return list(bundled if appimage else native)


class DependencyChecker:
    """FFmpeg for conversions and the preview's python-mpv and PyOpenGL."""

    def __init__(self):
        from engine.ffmpeg_path import get_ffmpeg_executable

        self.distro = get_distro_info()
        # Resolve the same way conversions do, so a bundled or jellyfin build
        # counts as "available" even when nothing is on PATH.
        ffmpeg = get_ffmpeg_executable()
        self.ffmpeg_path = (
            ffmpeg if os.access(ffmpeg, os.X_OK) else shutil.which(ffmpeg)
        )
        self.preview_available = _loads("mpv") and _loads("OpenGL.GL")

    def are_dependencies_available(self) -> bool:
        if not self.ffmpeg_path or not self.preview_available:
            return False
        # On RPM systems the limited 'ffmpeg-free' package lacks most encoders.
        # A build no package owns (/usr/local, jellyfin) is usable, and so is
        # one whose owner cannot be determined. Every activation asks, and the
        # owner cannot change while the application runs: ask rpm once.
        if self.distro.get("base") == "rpm":
            if getattr(self, "_ffmpeg_free", None) is None:
                self._ffmpeg_free = self._owned_by_ffmpeg_free()
            return not self._ffmpeg_free
        return True

    def _owned_by_ffmpeg_free(self) -> bool:
        command = ["rpm", "-qf", "--queryformat", "%{NAME}\\n", self.ffmpeg_path]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=5, check=False
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("Could not verify the ffmpeg package provider: %s", e)
            return False
        return result.returncode == 0 and "ffmpeg-free" in result.stdout.split()

    def get_install_command(self):
        """Let the native package manager present and confirm its transaction."""
        from engine.ffmpeg_path import _running_from_appimage

        base = self.distro.get("base")
        packages = _packages_for(base, _running_from_appimage())
        if base == "arch":
            command = ["pkexec", "pacman", "-S", *packages]
        elif base == "debian":
            command = ["pkexec", "apt", "install", *packages]
        elif base == "rpm":
            # The full ffmpeg (RPM Fusion) conflicts with ffmpeg-free; dnf only
            # swaps them when it may erase the installed package.
            command = ["pkexec", "dnf", "install", "--allowerasing", *packages]
            note = _(
                "The full FFmpeg comes from the RPM Fusion repository, which must be "
                "enabled first. The command replaces the limited ffmpeg-free package."
            )
            return {
                "command": command,
                "display": shlex.join(command),
                "packages": packages,
                "note": note,
            }
        else:
            return None
        return {
            "command": command,
            "display": shlex.join(command),
            "packages": packages,
        }
