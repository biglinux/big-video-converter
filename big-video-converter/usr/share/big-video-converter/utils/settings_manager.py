import json
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

        # Simplified path handling
        config_dir = os.path.expanduser("~/.config/big-video-converter")

        if dev_mode and dev_settings_file:
            self.settings_file = os.path.abspath(dev_settings_file)
        else:
            self.settings_file = os.path.join(config_dir, "settings.json")

        # Create directory if needed
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)

        # Load settings
        self.load_from_disk()

    def load_from_disk(self) -> None:
        """Load settings from JSON file"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r") as f:
                    self.settings = json.load(f)
                logger.debug(f"Loaded settings from: {self.settings_file}")
            else:
                logger.debug("Settings file not found, will use defaults")
                self.settings = {}
        except (ValueError, KeyError, OSError) as e:
            logger.error(f"Error loading settings: {e}")
            self.settings = {}

    def save_to_disk(self) -> bool:
        """Save settings to JSON file"""
        try:
            # Make sure the directory exists
            os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)

            # Save settings
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=2)
            return True
        except (ValueError, KeyError, OSError) as e:
            logger.error(f"Error saving settings: {e}")
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

    def set_value(self, key: str, value: str):
        """Set setting value and save to disk (unless in batch mode)"""
        self.settings[key] = value
        if self._batch_mode:
            return True
        return self.save_to_disk()

    def batch_update(self):
        """Context manager to defer disk writes until all settings are updated.
        Usage:
            with settings_manager.batch_update():
                settings_manager.set_value('key1', val1)
                settings_manager.set_value('key2', val2)
        """
        return self._BatchContext(self)

    class _BatchContext:
        def __init__(self, manager):
            self.manager = manager

        def __enter__(self):
            self.manager._batch_mode = True
            return self.manager

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.manager._batch_mode = False
            self.manager.save_to_disk()
            return False

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
        """Export current conversion settings to a JSON profile file."""
        try:
            profile = {
                "_profile_version": 1,
                "_app": "big-video-converter",
            }
            all_keys = set(self.DEFAULT_VALUES.keys()) | set(self.settings.keys())
            for key in sorted(all_keys - self._PROFILE_EXCLUDE_KEYS):
                profile[key] = self.settings.get(key, self.DEFAULT_VALUES.get(key, ""))
            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
            with open(filepath, "w") as f:
                json.dump(profile, f, indent=2, ensure_ascii=False)
            logger.info(f"Profile exported to: {filepath}")
            return True
        except OSError as e:
            logger.error(f"Error exporting profile: {e}")
            return False

    def import_profile(self, filepath: str) -> bool:
        """Import conversion settings from a JSON profile file."""
        try:
            with open(filepath, "r") as f:
                profile = json.load(f)
            if not isinstance(profile, dict):
                logger.error("Invalid profile: not a JSON object")
                return False
            if profile.get("_app") != "big-video-converter":
                logger.error("Invalid profile: wrong app identifier")
                return False
            with self.batch_update():
                for key, value in profile.items():
                    if key.startswith("_"):
                        continue
                    if key in self._PROFILE_EXCLUDE_KEYS:
                        continue
                    self.set_value(key, value)
            logger.info(f"Profile imported from: {filepath}")
            return True
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Error importing profile: {e}")
            return False

    # Simple aliases for unified API
    def load_setting(self, key: str, default=None):
        return self.get_value(key, default)

    def save_setting(self, key: str, value: str):
        return self.set_value(key, value)
