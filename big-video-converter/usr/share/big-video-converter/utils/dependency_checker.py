"""
System dependency checker for ffmpeg.
"""

import shutil
import subprocess
from gi.repository import GLib

# Setup translation
import gettext
_ = gettext.gettext


def get_distro_info():
    """Detect host distribution information."""
    distro_info = {'id': None, 'base': None}
    try:
        with open('/etc/os-release', 'r') as f:
            lines = f.readlines()
        for line in lines:
            if line.startswith('ID='):
                distro_info['id'] = line.strip().split('=')[1].strip('"')
            elif line.startswith('ID_LIKE='):
                bases = line.strip().split('=')[1].strip('"').split()
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
        self.distro = get_distro_info()
        self.ffmpeg_path = shutil.which('ffmpeg')
        self.mpv_path = shutil.which('mpv')

    def are_dependencies_available(self):
       """Check if ffmpeg and mpv executables are in PATH."""
       return self.ffmpeg_path is not None and self.mpv_path is not None

    def get_install_command(self):
        """Get the installation command for ffmpeg based on the distribution."""
        packages = ['ffmpeg', 'mpv']
        distro_base = self.distro.get('base')

        if distro_base == 'arch':
            # Use -Sy to ensure package databases are synced, similar to 'apt update'
            full_command_str = f"pacman -Sy --noconfirm {' '.join(packages)}"
            return {
                'command': ['pkexec', 'sh', '-c', full_command_str],
                'display': f"pkexec sh -c \"{full_command_str}\"",
                'packages': packages
            }
        
        elif distro_base == 'debian':
            # Combine update and install into a single command
            full_command_str = f"apt-get update && apt-get install -y {' '.join(packages)}"
            return {
                'command': ['pkexec', 'sh', '-c', full_command_str],
                'display': f"pkexec sh -c \"{full_command_str}\"",
                'packages': packages
            }
        
        elif distro_base == 'rpm':
            # dnf handles metadata updates automatically
            full_command_str = f"dnf install -y {' '.join(packages)}"
            return {
                'command': ['pkexec', 'sh', '-c', full_command_str],
                'display': f"pkexec sh -c \"{full_command_str}\"",
                'packages': packages
            }
        
        return None