# Setup translation
import gettext
import logging
import os
import shlex
import shutil
import subprocess

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

       # On RPM systems the limited 'ffmpeg-free' package lacks most encoders.
       # A build no package owns (AppImage, /usr/local, jellyfin) is usable,
       # and so is one whose owner cannot be determined.
       if self.distro.get('base') == 'rpm':
           try:
               command = ['rpm', '-qf', '--queryformat', '%{NAME}\\n', self.ffmpeg_path]
               result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
           except (subprocess.SubprocessError, OSError) as e:
               logger.warning("Could not verify the ffmpeg package provider: %s", e)
           else:
               if result.returncode == 0 and 'ffmpeg-free' in result.stdout.split():
                   logger.debug("Found 'ffmpeg-free' package. Triggering installation of the full version.")
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
            packages = ['ffmpeg', 'mpv', _debian_libmpv()]
            command = ['pkexec', 'apt', 'install', *packages]
        elif base == 'rpm':
            # The full ffmpeg (RPM Fusion) conflicts with ffmpeg-free; dnf only
            # swaps them when it may erase the installed package.
            packages = ['ffmpeg', 'mpv']
            command = ['pkexec', 'dnf', 'install', '--allowerasing', *packages]
            note = _("The full FFmpeg comes from the RPM Fusion repository, which must be "
                     "enabled first. The command replaces the limited ffmpeg-free package.")
            return {'command': command, 'display': shlex.join(command), 'packages': packages,
                    'note': note}
        else:
            return None
        return {'command': command, 'display': shlex.join(command), 'packages': packages}


def _debian_libmpv():
    """libmpv2 on Debian 12 and Ubuntu 24.04, libmpv1 on Ubuntu 22.04."""
    for name in ('libmpv2', 'libmpv1'):
        try:
            result = subprocess.run(['apt-cache', 'show', '--no-all-versions', name],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        except (subprocess.SubprocessError, OSError):
            break
        if result.returncode == 0:
            return name
    return 'libmpv2'
