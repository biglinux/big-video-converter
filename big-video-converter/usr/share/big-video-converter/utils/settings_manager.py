import copy
import json
import logging
import math
import os
import re
import shlex
import tempfile
from contextlib import contextmanager
from typing import ClassVar

logger = logging.getLogger(__name__)


class SettingsManager:
    """Simple settings manager using JSON file."""

    # Combined default values
    DEFAULT_VALUES: ClassVar[dict[str, object]] = {
        # General settings
        "last-accessed-directory": "",
        "output-folder": "",
        "delete-original": False,
        "show-tooltips": True,
        # Window state
        "window-width": 1200,
        "window-height": 720,
        "window-maximized": False,
        "sidebar-position": 430,
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
        # Output frame rate ("" keeps the source's), see constants.VIDEO_FPS_VALUES.
        "video-fps": "",
        # Fit every converted video under a size ("" = no limit): a target
        # id from utils/size_target.TARGETS, or "custom" with size-target-mb.
        "size-target": "",
        "size-target-mb": 50.0,
        "size-strategy": "auto",
        "additional-options": "",
        "output-format-index": 0,
        # Feature toggles
        "gpu-partial": False,
        "force-copy-video": False,
        "only-extract-subtitles": False,
        # Noise reduction settings
        "noise-reduction": False,
        "noise-reduction-strength": 1.0,
        # Index into constants.NOISE_MODELS: 0 DeepFilterNet3, 1 DPDFNet-2.
        "noise-model": 0,
        "noise-gate-enabled": False,
        "noise-gate-intensity": 0.5,
        # Compressor settings
        "compressor-enabled": False,
        "compressor-intensity": 1.0,
        # HPF settings
        "hpf-enabled": False,
        "hpf-frequency": 80,
        # EQ settings
        "eq-enabled": False,
        "eq-preset": "flat",
        "eq-bands": "0,0,0,0,0,0,0,0,0,0",
        # Normalize
        "normalize-enabled": False,
        "use-custom-output-folder": False,
        # Sidebar state
        "active-preset": "",
        "gpu-device-index": 0,
        "show-welcome-dialog": True,
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
                raise ValueError("Settings must be a JSON object")  # noqa: TRY004 - invalid file content, reported with the other ValueErrors
            # There are two models (0 and 1); older builds imported 2.
            model = data.get("noise-model")
            if type(model) is int and model > 1:
                data["noise-model"] = 0
            # FFmpeg 7 removed -vsync; -fps_mode takes the same modes by name.
            options = data.get("additional-options")
            if isinstance(options, str) and "-vsync" in options:
                data["additional-options"] = self._migrate_vsync(options)
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
        """Atomically replace settings; preserve only a known-good backup.

        This runs on the GTK thread for every keystroke in the output-folder
        entry, so the backup is a rename of the file that is being replaced
        anyway — copying and syncing it a second time doubled the cost of
        typing. A rename leaves `.bak` as exactly the last published file, and
        load_from_disk already falls back to it.
        """
        try:
            data = json.dumps(self.settings, indent=2, ensure_ascii=False, allow_nan=False)
            if self._read_file(self.settings_file) is not None:
                try:
                    os.replace(self.settings_file, self.settings_file + ".bak")
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
            except (ValueError, TypeError, OverflowError):
                return default
        elif isinstance(default, float):
            try:
                return float(value)
            except (ValueError, TypeError, OverflowError):
                return default
        else:
            return str(value) if value is not None else ""

    def set_value(self, key: str, value):
        """Update one value, rolling back memory when persistence fails."""
        if self._suspended:
            return True
        if (key in self.settings
                and type(self.settings[key]) is type(value)
                and self.settings[key] == value):
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
            if (
                not previous
                and self.settings != before
                and not self._suspended
                and not self.save_to_disk()
            ):
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
    _PROFILE_EXCLUDE_KEYS: ClassVar[set[str]] = {
        "last-accessed-directory",
        "output-folder",
        "window-width",
        "window-height",
        "window-maximized",
        "sidebar-position",
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
        updates = self.read_profile(filepath)
        return updates is not None and self.apply_profile(updates)

    def read_profile(self, filepath: str):
        """The validated portable settings of a profile file, or None."""
        try:
            profile = self._read_file(filepath)
            if not isinstance(profile, dict) or profile.get("_app") != "big-video-converter":
                raise ValueError("Invalid profile application identifier")
            if type(profile.get("_profile_version")) is not int or profile["_profile_version"] != 1:
                raise ValueError("Unsupported profile version")
            updates = {}
            for key, value in profile.items():
                if key in ("_app", "_profile_version"):
                    continue
                # A setting this build does not know is never read, so skipping
                # it lets a profile written by a newer version still import its
                # shared keys. Values that ARE used stay strictly validated.
                if key not in self.DEFAULT_VALUES:
                    logger.info("Ignoring unknown profile setting: %s", key)
                    continue
                # Older profiles contained these fields. Ignore them without ever
                # copying deletion flags, device IDs, file paths or preview state.
                if key in self._PROFILE_EXCLUDE_KEYS:
                    continue
                self._validate_profile_value(key, value)
                updates[key] = value
            return updates
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
            logger.error("Error importing profile: %s", error)
            return None

    def apply_profile(self, updates: dict) -> bool:
        """Commit settings from read_profile atomically; unchanged ones write nothing."""
        if self._batch_mode or self._suspended:
            logger.error("Error importing profile: another settings transaction is open")
            return False
        if all(key in self.settings and type(self.settings[key]) is type(value)
               and self.settings[key] == value for key, value in updates.items()):
            return True
        before = copy.deepcopy(self.settings)
        self.settings.update(updates)
        if not self.save_to_disk():
            self.settings = before
            return False
        return True

    # Simple aliases for unified API
    def load_setting(self, key: str, default=None):
        return self.get_value(key, default)

    def save_setting(self, key: str, value: str):
        return self.set_value(key, value)


    @staticmethod
    def _migrate_vsync(text: str) -> str:
        modes = {"-1": "auto", "0": "passthrough", "1": "cfr", "2": "vfr"}
        try:
            tokens = shlex.split(text)
        except ValueError:
            return text
        for index, token in enumerate(tokens[:-1]):
            mode = tokens[index + 1].lower()
            mode = modes.get(mode, mode)
            if token == "-vsync" and mode in modes.values():
                tokens[index:index + 2] = ["-fps_mode", mode]
        return shlex.join(tokens)

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
            "size-target": {"", "custom", "whatsapp", "discord", "email", "100mb",
                            "telegram", "fat32"},
            "size-strategy": {"auto", "keep_resolution", "split"},
        }
        if key in enums and value not in enums[key]:
            raise ValueError("Unsupported value for " + key)
        ranges = {
            "output-format-index": (0, 3), "noise-model": (0, 1),
            "noise-reduction-strength": (0, 1),
            "noise-gate-intensity": (0, 1), "compressor-intensity": (0, 10),
            "hpf-frequency": (20, 20000), "size-target-mb": (1, 1_000_000),
        }
        if key in ranges and not ranges[key][0] <= value <= ranges[key][1]:
            raise ValueError("Out-of-range value for " + key)
        if key == "audio-channels" and value and (
            not re.fullmatch(r"[1-9][0-9]?", value) or int(value) > 64
        ):
            raise ValueError("Invalid audio channel count")
        if key == "audio-bitrate" and value and not re.fullmatch(r"[1-9][0-9]*[kKmM]?", value):
            raise ValueError("Invalid audio bitrate")
        if key == "video-resolution" and value:
            match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", value)
            if not match or max(map(int, match.groups())) > 32768:
                raise ValueError("Invalid video resolution")
        if key == "video-fps" and value:
            # The script's video_fps: "24", or "30000/1001" from a preset.
            match = re.fullmatch(r"([1-9][0-9]{0,5})(?:/([1-9][0-9]{0,4}))?", value)
            if not match or int(match[1]) > 240 * int(match[2] or 1):
                raise ValueError("Invalid frame rate")
        if key == "eq-bands":
            bands = [float(part) for part in value.split(",")]
            if len(bands) != 10 or any(not math.isfinite(v) or not -24 <= v <= 24 for v in bands):
                raise ValueError("Invalid equalizer bands")
        if key == "eq-preset" and not re.fullmatch(r"[\w -]{1,64}", value):
            raise ValueError("Invalid equalizer preset")
        if key == "additional-options":
            from utils.ffmpeg_options import parse_additional_options
            parse_additional_options(value)


# Local preferences are not portable conversion recipes.
SettingsManager._PROFILE_EXCLUDE_KEYS.update({
    "active-preset",
    "delete-original", "gpu-device-index", "gpu",
    "gpu-partial", "use-custom-output-folder",
    "show-tooltips", "show-welcome-dialog", "video-preview-render-mode",
})
