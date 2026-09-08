import json
import copy
import math
import re
import tempfile
from contextlib import contextmanager
import os

import logging

logger = logging.getLogger(__name__)

# Remove translation imports if not directly used in this file


class SettingsManager:
    """Simple settings manager using JSON file."""

    # Combined default values
    DEFAULT_VALUES = {
        # General settings
        "last-accessed-directory": "",
        "output-folder": "",
        "delete-original": False,
        "show-single-help-on-startup": True,
        "show-conversion-help-on-startup": True,
        "show-tooltips": True,
        # Window state
        "window-width": 1200,
        "window-height": 720,
        "window-maximized": False,
        "sidebar-position": 430,
        # Batch conversion
        "search-directory": "",
        "max-processes": 2,
        "min-mp4-size": 1024,
        "log-file": "mkv-mp4-convert.log",
        "delete-batch-originals": False,
        # Encoding settings - use strings directly
        "gpu": "auto",
        "video-quality": "default",
        "video-codec": "h264",
        "preset": "default",
        "subtitle-extract": "embedded",
        "audio-handling": "copy",
        # Audio settings
        "audio-bitrate": "",
        "audio-channels": "",
        "audio-codec": "aac",
        # Video settings
        "video-resolution": "",
        "additional-options": "",
        "output-format-index": 0,
        # Feature toggles
        "gpu-partial": False,
        "force-copy-video": False,
        "only-extract-subtitles": False,
        # Noise reduction settings
        "noise-reduction": False,
        "noise-reduction-strength": 1.0,
        "noise-model": 0,
        "noise-model-blend": False,
        "noise-speech-strength": 1.0,
        "noise-lookahead": 50,
        "noise-voice-recovery": 0.75,
        "noise-gate-enabled": False,
        "noise-gate-intensity": 0.5,
        "noise-gate-threshold": -30,
        "noise-gate-range": -60,
        "noise-gate-attack": 20.0,
        "noise-gate-release": 150.0,
        # Compressor settings
        "compressor-enabled": False,
        "compressor-intensity": 1.0,
        # HPF settings
        "hpf-enabled": False,
        "hpf-frequency": 80,
        # Transient settings
        # EQ settings
        "eq-enabled": False,
        "eq-preset": "flat",
        "eq-bands": "0,0,0,0,0,0,0,0,0,0",
        # Normalize
        "normalize-enabled": False,
        "use-custom-output-folder": False,
        # Sidebar state
        "video-profile": "universal",
        "gpu-device-index": 0,
        "show-welcome-dialog": True,
        "preview-rotation": 0,
        "preview-flip-h": False,
        "preview-flip-v": False,
        # Preview settings
        "preview-crop-left": 0,
        "preview-crop-right": 0,
        "preview-crop-top": 0,
        "preview-crop-bottom": 0,
        "preview-brightness": 0.0,
        "preview-saturation": 1.0,
        "preview-hue": 0.0,
        # Video trim settings
        "video-trim-start": 0.0,
        "video-trim-end": -1.0,  # -1 means no end time (use full video)
        # Multi-segment output mode
        "multi-segment-output-mode": "join",  # Options: "join", "split"
        # Video preview rendering mode
        "video-preview-render-mode": "auto",  # Options: "auto", "opengl", "software"
    }

    def __init__(self, app_id, dev_mode=False, dev_settings_file=None):
        self.app_id = app_id
        self.settings = {}
        self._batch_mode = False  # When True, defer disk writes
        self._suspended = False  # When True, writes are ignored (UI loading)

        # Simplified path handling
        config_home = os.environ.get("XDG_CONFIG_HOME", "")
        if not config_home or not os.path.isabs(config_home):
            config_home = os.path.expanduser("~/.config")
        config_dir = os.path.join(config_home, "big-video-converter")

        if dev_mode and dev_settings_file:
            self.settings_file = os.path.abspath(dev_settings_file)
        else:
            self.settings_file = os.path.join(config_dir, "settings.json")

        # Create directory if needed
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)

        # Load settings
        self.load_from_disk()

    def _read_file(self, path: str):
        """Read a bounded JSON object; never treat a corrupt primary as a backup."""
        try:
            with open(path, "r", encoding="utf-8") as file:
                text = file.read(1024 * 1024 + 1)
            if len(text) > 1024 * 1024:
                raise ValueError("Settings file is too large")
            data = json.loads(text, parse_constant=self._reject_constant,
                              object_pairs_hook=self._unique_object)
            if not isinstance(data, dict):
                raise ValueError("Settings must be a JSON object")
            return data
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError, RecursionError) as error:
            logger.warning("Could not read settings from %s: %s", path, error)
            return None

    def load_from_disk(self) -> None:
        """Load settings, falling back to the backup copy when needed.

        A truncated settings.json (process killed while writing, power loss,
        full disk) used to silently reset every preference: the file failed to
        parse, settings became empty, and the next write replaced the file with
        that single key. The backup written by save_to_disk covers that case.
        """
        data = self._read_file(self.settings_file)

        if data is None:
            backup = self._read_file(self.settings_file + ".bak")
            if backup is not None:
                logger.warning(
                    "Settings file unreadable — restored from the backup copy"
                )
                self.settings = backup
                self.save_to_disk()
                return

            logger.debug("No usable settings file, will use defaults")
            self.settings = {}
            return

        self.settings = data
        logger.debug(f"Loaded settings from: {self.settings_file}")

    def save_to_disk(self) -> bool:
        """Atomically replace settings; preserve only a known-good backup."""
        try:
            data = json.dumps(self.settings, indent=2, ensure_ascii=False, allow_nan=False)
            old = self._read_file(self.settings_file)
            if old is not None:
                try:
                    self._atomic_write(self.settings_file + ".bak", json.dumps(
                        old, indent=2, ensure_ascii=False, allow_nan=False))
                except OSError as error:
                    logger.warning("Could not refresh settings backup: %s", error)
            self._atomic_write(self.settings_file, data)
            return True
        except (OSError, ValueError, TypeError, RecursionError) as error:
            logger.error("Error saving settings: %s", error)
            return False

    # Simplified type-specific methods
    def get_value(self, key: str, default=None):
        """Get setting value with appropriate type conversion"""
        if default is None:
            default = self.DEFAULT_VALUES.get(key, "")

        value = self.settings.get(key, default)

        # Convert to appropriate type based on default
        if isinstance(default, bool):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        elif isinstance(default, int):
            try:
                return int(value)
            except (ValueError, TypeError):
                return default
        elif isinstance(default, float):
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        else:
            return str(value) if value is not None else ""

    def set_value(self, key: str, value):
        """Update one value, rolling back memory when persistence fails."""
        if self._suspended:
            return True
        before = copy.deepcopy(self.settings)
        self.settings[key] = value
        if self._batch_mode:
            return True
        if self.save_to_disk():
            return True
        self.settings = before
        return False

    @contextmanager
    def suspend_writes(self):
        """Nested UI restoration scopes must all finish before writes resume."""
        previous = self._suspended
        self._suspended = True
        try:
            yield self
        finally:
            self._suspended = previous

    @contextmanager
    def batch_update(self):
        """A nested transaction persists only at its outermost successful exit."""
        previous = self._batch_mode
        before = copy.deepcopy(self.settings)
        self._batch_mode = True
        try:
            yield self
            if not previous and self.settings != before and not self._suspended:
                if not self.save_to_disk():
                    raise OSError("Could not persist settings transaction")
        except BaseException:
            self.settings = before
            raise
        finally:
            self._batch_mode = previous

    # Legacy methods for compatibility
    def get_string(self, key: str, default=None):
        return self.get_value(key, default)

    def get_boolean(self, key: str, default=None):
        return self.get_value(key, default if default is not None else False)

    def set_string(self, key: str, value: str):
        return self.set_value(key, str(value) if value is not None else "")

    def set_boolean(self, key: str, value: str):
        return self.set_value(key, bool(value))

    def set_int(self, key: str, value: str):
        try:
            return self.set_value(key, int(value))
        except (ValueError, TypeError):
            logger.error(f"Error: Could not convert {value} to integer")
            return False

    def set_double(self, key: str, value: str):
        try:
            return self.set_value(key, float(value))
        except (ValueError, TypeError):
            logger.error(f"Error: Could not convert {value} to float")
            return False

    # Keys excluded from profile export (UI state, not conversion settings)
    _PROFILE_EXCLUDE_KEYS = {
        "last-accessed-directory",
        "output-folder",
        "search-directory",
        "window-width",
        "window-height",
        "window-maximized",
        "sidebar-position",
        "show-single-help-on-startup",
        "show-conversion-help-on-startup",
        "log-file",
    }

    def export_profile(self, filepath: str) -> bool:
        """Export only validated, portable conversion settings."""
        try:
            profile = {"_profile_version": 1, "_app": "big-video-converter"}
            for key in sorted(self.DEFAULT_VALUES.keys() - self._PROFILE_EXCLUDE_KEYS):
                value = self.settings.get(key, self.DEFAULT_VALUES[key])
                self._validate_profile_value(key, value)
                profile[key] = value
            self._atomic_write(filepath, json.dumps(
                profile, indent=2, ensure_ascii=False, allow_nan=False))
            return True
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            logger.error("Error exporting profile: %s", error)
            return False

    def import_profile(self, filepath: str) -> bool:
        """Validate all fields before committing; never import local/destructive state."""
        try:
            profile = self._read_file(filepath)
            if not isinstance(profile, dict) or profile.get("_app") != "big-video-converter":
                raise ValueError("Invalid profile application identifier")
            if type(profile.get("_profile_version")) is not int or profile["_profile_version"] != 1:
                raise ValueError("Unsupported profile version")
            if self._batch_mode or self._suspended:
                raise ValueError("Cannot import during another settings transaction")
            updates = {}
            for key, value in profile.items():
                if key in ("_app", "_profile_version"):
                    continue
                if key not in self.DEFAULT_VALUES:
                    raise ValueError("Unknown profile setting: " + key)
                # Older profiles contained these fields. Ignore them without ever
                # copying deletion flags, device IDs, file paths or preview state.
                if key in self._PROFILE_EXCLUDE_KEYS:
                    continue
                self._validate_profile_value(key, value)
                updates[key] = value
            before = copy.deepcopy(self.settings)
            self.settings.update(updates)
            if not self.save_to_disk():
                self.settings = before
                return False
            return True
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
            logger.error("Error importing profile: %s", error)
            return False

    # Simple aliases for unified API
    def load_setting(self, key: str, default=None):
        return self.get_value(key, default)

    def save_setting(self, key: str, value: str):
        return self.set_value(key, value)


    @staticmethod
    def _reject_constant(value):
        raise ValueError("Non-finite JSON number: " + value)

    @staticmethod
    def _unique_object(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("Duplicate settings key: " + key)
            data[key] = value
        return data

    @staticmethod
    def _atomic_write(path: str, text: str) -> None:
        path = os.path.abspath(path)
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".bvc-settings-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @classmethod
    def _validate_profile_value(cls, key, value):
        default = cls.DEFAULT_VALUES[key]
        if isinstance(default, bool):
            valid = type(value) is bool
        elif isinstance(default, int):
            valid = type(value) is int
        elif isinstance(default, float):
            valid = type(value) in (int, float) and math.isfinite(value)
        else:
            valid = isinstance(value, str) and len(value) <= 65536 and "\0" not in value
        if not valid:
            raise ValueError("Invalid type for " + key)
        enums = {
            "video-codec": {"copy", "h264", "h265", "av1", "vp9", "prores"},
            "video-quality": {"default", "veryhigh", "high", "medium", "low", "verylow", "superlow"},
            "preset": {"default", "ultrafast", "veryfast", "faster", "medium", "slow", "veryslow"},
            "audio-codec": {"aac", "opus", "ac3"},
            "audio-handling": {"copy", "reencode", "none"},
            "subtitle-extract": {"extract", "embedded", "none"},
            "multi-segment-output-mode": {"join", "split"},
            "video-profile": {"copy", "universal", "smaller", "quality", "custom"},
        }
        if key in enums and value not in enums[key]:
            raise ValueError("Unsupported value for " + key)
        ranges = {
            "output-format-index": (0, 3), "noise-model": (0, 2),
            "noise-reduction-strength": (0, 1), "noise-speech-strength": (0, 1),
            "noise-lookahead": (0, 2000), "noise-voice-recovery": (0, 1),
            "noise-gate-intensity": (0, 1), "noise-gate-threshold": (-90, 0),
            "noise-gate-range": (-90, 0), "noise-gate-attack": (0.01, 9000),
            "noise-gate-release": (0.01, 9000), "compressor-intensity": (0, 10),
            "hpf-frequency": (20, 20000),
        }
        if key in ranges and not ranges[key][0] <= value <= ranges[key][1]:
            raise ValueError("Out-of-range value for " + key)
        if key == "audio-channels" and value:
            if not re.fullmatch(r"[1-9][0-9]?", value) or int(value) > 64:
                raise ValueError("Invalid audio channel count")
        if key == "audio-bitrate" and value:
            if not re.fullmatch(r"[1-9][0-9]*[kKmM]?", value):
                raise ValueError("Invalid audio bitrate")
        if key == "video-resolution" and value:
            match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", value)
            if not match or max(map(int, match.groups())) > 32768:
                raise ValueError("Invalid video resolution")
        if key == "eq-bands":
            bands = [float(part) for part in value.split(",")]
            if len(bands) != 10 or any(not math.isfinite(v) or not -24 <= v <= 24 for v in bands):
                raise ValueError("Invalid equalizer bands")
        if key == "eq-preset" and not re.fullmatch(r"[\w -]{1,64}", value):
            raise ValueError("Invalid equalizer preset")
        if key == "additional-options":
            from utils.ffmpeg_options import parse_additional_options
            parse_additional_options(value)


# Local preferences and per-file edits are not portable conversion recipes.
SettingsManager._PROFILE_EXCLUDE_KEYS.update({
    "delete-original", "delete-batch-originals", "gpu-device-index", "gpu",
    "gpu-partial", "max-processes", "min-mp4-size", "use-custom-output-folder",
    "show-tooltips", "show-welcome-dialog", "video-preview-render-mode",
    "video-trim-start", "video-trim-end",
})
SettingsManager._PROFILE_EXCLUDE_KEYS.update(
    key for key in SettingsManager.DEFAULT_VALUES if key.startswith("preview-")
)
