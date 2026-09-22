# Setup translation
import gettext
import os
import shutil
import shlex
import subprocess

import logging

logger = logging.getLogger(__name__)

_ = gettext.gettext


def get_distro_info():
    """Detect host distribution information."""
    distro_info = {'id': None, 'base': None}
    try:
        with open('/etc/os-release', 'r') as f:
            lines = f.readlines()
        for line in lines:
            if line.startswith('ID='):
                distro_info["id"] = line.strip().split("=")[1].strip("\"'")
            elif line.startswith('ID_LIKE='):
                bases = line.strip().split("=")[1].strip("\"'").split()
                if 'arch' in bases:
                    distro_info['base'] = 'arch'
                elif 'debian' in bases:
                    distro_info['base'] = 'debian'
                elif 'fedora' in bases:
                    distro_info['base'] = 'rpm'
    except FileNotFoundError:
        pass

    if not distro_info['base']:
        if distro_info['id'] in ['arch', 'manjaro', 'endeavouros']:
            distro_info['base'] = 'arch'
        elif distro_info['id'] in ['debian', 'ubuntu', 'linuxmint', 'pop']:
            distro_info['base'] = 'debian'
        elif distro_info['id'] in ['fedora', 'centos', 'rhel', 'nobara', 'almalinux']:
            distro_info['base'] = 'rpm'
            
    return distro_info


class DependencyChecker:
    """Checks for ffmpeg and provides installation commands."""

    def __init__(self):
        from utils.ffmpeg_path import get_ffmpeg_executable

        self.distro = get_distro_info()
        # Resolve the same way conversions do, so a bundled or jellyfin build
        # counts as "available" even when nothing is on PATH.
        ffmpeg = get_ffmpeg_executable()
        self.ffmpeg_path = ffmpeg if os.access(ffmpeg, os.X_OK) else shutil.which(ffmpeg)
        self.mpv_path = shutil.which('mpv')

    def are_dependencies_available(self) -> bool:
       """Check if ffmpeg and mpv executables are in PATH and are the correct versions."""
       # First, a basic check if the executables exist at all.
       if not self.ffmpeg_path or not self.mpv_path:
           return False

       # If the distro is RPM-based, we need to ensure it's not the limited 'ffmpeg-free'.
       if self.distro.get('base') == 'rpm':
           try:
               # Ask the system which package owns the ffmpeg executable.
               command = ['rpm', '-qf', self.ffmpeg_path]
               result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=5)
               
               package_name = result.stdout.strip()

               # If the owner package is 'ffmpeg-free', the dependency is not met.
               if 'ffmpeg-free' in package_name:
                   logger.debug("Found 'ffmpeg-free' package. Triggering installation of the full version.")
                   return False
           except (subprocess.SubprocessError, OSError) as e:
               # If the check fails for any reason, it's safer to assume the dependency is not met.
               logger.error(f"Warning: Could not verify the ffmpeg package provider: {e}")
               return False

       # If we passed all checks, the dependencies are considered available.
       return True

    def get_install_command(self):
        """Let the native package manager present and confirm its transaction."""
        base = self.distro.get('base')
        if base == 'arch':
            packages = ['ffmpeg', 'mpv']
            command = ['pkexec', 'pacman', '-S', *packages]
        elif base == 'debian':
            packages = ['ffmpeg', 'mpv', 'libmpv2']
            command = ['pkexec', 'apt', 'install', *packages]
        elif base == 'rpm':
            packages = ['ffmpeg', 'mpv']
            command = ['pkexec', 'dnf', 'install', *packages]
        else:
            return None
        return {'command': command, 'display': shlex.join(command), 'packages': packages}
