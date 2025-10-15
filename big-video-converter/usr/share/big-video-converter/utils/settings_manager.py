import os
import json

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
    }

    def __init__(self, app_id, dev_mode=False, dev_settings_file=None):
        self.app_id = app_id
        self.settings = {}

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

    def load_from_disk(self):
        """Load settings from JSON file"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r") as f:
                    self.settings = json.load(f)
                print(f"Loaded settings from: {self.settings_file}")
            else:
                print("Settings file not found, will use defaults")
                self.settings = {}
        except Exception as e:
            print(f"Error loading settings: {e}")
            self.settings = {}

    def save_to_disk(self):
        """Save settings to JSON file"""
        try:
            # Make sure the directory exists
            os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)

            # Save settings
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving settings: {e}")
            return False

    # Simplified type-specific methods
    def get_value(self, key, default=None):
        """Get setting value with appropriate type conversion"""
        if default is None:
            default = self.DEFAULT_VALUES.get(key, "")

        value = self.settings.get(key, default)

        # Convert to appropriate type based on default
        if isinstance(default, bool):
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

    def set_value(self, key, value):
        """Set setting value and save to disk"""
        self.settings[key] = value
        return self.save_to_disk()

    # Legacy methods for compatibility
    def get_string(self, key, default=None):
        return self.get_value(key, default)

    def get_boolean(self, key, default=None):
        return self.get_value(key, default if default is not None else False)

    def set_string(self, key, value):
        return self.set_value(key, str(value) if value is not None else "")

    def set_boolean(self, key, value):
        return self.set_value(key, bool(value))

    def set_int(self, key, value):
        try:
            return self.set_value(key, int(value))
        except (ValueError, TypeError):
            print(f"Error: Could not convert {value} to integer")
            return False

    def set_double(self, key, value):
        try:
            return self.set_value(key, float(value))
        except (ValueError, TypeError):
            print(f"Error: Could not convert {value} to float")
            return False

    # Simple aliases for unified API
    def load_setting(self, key, default=None):
        return self.get_value(key, default)

    def save_setting(self, key, value):
        return self.set_value(key, value)
