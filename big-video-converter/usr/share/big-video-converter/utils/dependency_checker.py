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
       """Check if ffmpeg and mpv executables are in PATH and are the correct versions."""
       # First, a basic check if the executables exist at all.
       if not self.ffmpeg_path or not self.mpv_path:
           return False

       # If the distro is RPM-based, we need to ensure it's not the limited 'ffmpeg-free'.
       if self.distro.get('base') == 'rpm':
           try:
               # Ask the system which package owns the ffmpeg executable.
               command = ['rpm', '-qf', self.ffmpeg_path]
               result = subprocess.run(command, capture_output=True, text=True, check=True)
               
               package_name = result.stdout.strip()

               # If the owner package is 'ffmpeg-free', the dependency is not met.
               if 'ffmpeg-free' in package_name:
                   print("Found 'ffmpeg-free' package. Triggering installation of the full version.")
                   return False
           except (subprocess.CalledProcessError, FileNotFoundError) as e:
               # If the check fails for any reason, it's safer to assume the dependency is not met.
               print(f"Warning: Could not verify the ffmpeg package provider: {e}")
               return False

       # If we passed all checks, the dependencies are considered available.
       return True

    def get_install_command(self):
        """Get the installation command for ffmpeg based on the distribution."""
        distro_base = self.distro.get('base')

        if distro_base == 'arch':
            packages = ['ffmpeg', 'mpv']
            # The full command that will be executed
            full_command_str = f"pacman -Sy --noconfirm {' '.join(packages)}"
            # A simple, user-friendly string for the GUI
            display_str = f"pacman -Sy {' '.join(packages)}"
            return {
                'command': ['pkexec', 'sh', '-c', full_command_str],
                'display': display_str,
                'packages': packages
            }
        
        elif distro_base == 'debian':
            packages = ['ffmpeg', 'mpv', 'libmpv2']
            # The full command to be executed, including the update
            full_command_str = f"apt update && apt install -y {' '.join(packages)}"
            # A simple, user-friendly string for the GUI, avoiding '&&'
            display_str = f"apt install -y {' '.join(packages)}"
            return {
                'command': ['pkexec', 'sh', '-c', full_command_str],
                'display': display_str,
                'packages': packages
            }
        
        elif distro_base == 'rpm':
            packages = ['ffmpeg', 'mpv']
            # Command to install RPM Fusion repos
            rpm_fusion_install = "dnf install -y https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm"
            
            # Command to install the packages, automatically replacing 'ffmpeg-free'
            package_install = f"dnf install -y {' '.join(packages)} --allowerasing"

            # The full, robust command that will be executed
            full_command_str = f"{rpm_fusion_install} && {package_install}"
            
            # A simple, user-friendly string for the GUI
            display_str = f"dnf install -y {' '.join(packages)}"
            
            return {
                'command': ['pkexec', 'sh', '-c', full_command_str],
                'display': display_str,
                'packages': packages
            }
        
        return None