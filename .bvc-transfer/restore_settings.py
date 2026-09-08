"""Recover only settings edits omitted by the interrupted upload.

Metadata, GPU and filter fixes belong to restore_ui.py and are not replayed
here. The reconstructed settings source must be reviewed and retested; it is
not claimed to match the unavailable earlier local implementation byte-for-byte.
"""
from pathlib import Path
import ast
import os
import textwrap

ROOT = Path(os.environ['BVC_REPO'])
R = ROOT / 'big-video-converter/usr/share/big-video-converter'
p = R / 'utils/settings_manager.py'
s = p.read_text(encoding='utf-8')
s = s.replace('import json\n', 'import json\nimport copy\nimport math\nimport re\nimport tempfile\nfrom contextlib import contextmanager\n', 1)
s = s.replace('import shutil\n', '')
s = s.replace('config_dir = os.path.expanduser("~/.config/big-video-converter")', '''config_home = os.environ.get("XDG_CONFIG_HOME", "")
        if not config_home or not os.path.isabs(config_home):
            config_home = os.path.expanduser("~/.config")
        config_dir = os.path.join(config_home, "big-video-converter")''', 1)


def replace_method(name, replacement):
    global s
    cls = next(n for n in ast.parse(s).body if isinstance(n, ast.ClassDef) and n.name == 'SettingsManager')
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = s.splitlines(keepends=True)
    start = min([node.lineno] + [n.lineno for n in node.decorator_list]) - 1
    lines[start:node.end_lineno] = [textwrap.indent(textwrap.dedent(replacement).strip(), '    ') + '\n']
    s = ''.join(lines)


replace_method('_read_file', '''
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
''')
replace_method('save_to_disk', '''
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
''')
replace_method('set_value', '''
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
''')
a = s.index('    def suspend_writes(')
b = s.index('    # Legacy methods for compatibility', a)
s = s[:a] + '''    @contextmanager
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

''' + s[b:]
replace_method('export_profile', '''
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
''')
replace_method('import_profile', '''
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
''')
s += '''

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
            valid = isinstance(value, str) and len(value) <= 65536 and "\\0" not in value
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
        if key == "eq-preset" and not re.fullmatch(r"[\\w -]{1,64}", value):
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
'''
ast.parse(s)
p.write_text(s, encoding='utf-8')
