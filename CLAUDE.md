# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Big Video Converter is a GTK4/libadwaita-based graphical frontend for video conversion, built with Python. It provides an intuitive interface for converting video files (primarily MKV to MP4) using FFmpeg as the conversion engine, with support for GPU acceleration, batch processing, and advanced video editing features.

**Technology Stack:**
- Python 3.6+ with GTK4 and libadwaita (via PyGObject)
- FFmpeg for video conversion (core engine)
- MPV for video playback/preview
- Bash script wrapper for FFmpeg command execution

## Architecture

### Application Structure

The codebase follows a modular architecture with clear separation of concerns:

**Entry Point:**
- `big-video-converter/usr/share/big-video-converter/main.py` - Main application class (`VideoConverterApp`) and GTK4/Adwaita UI initialization

**Core Modules:**
- `constants.py` - Application-wide constants, UI options, translations, and tooltip definitions
- `queue_manager.py` / `file_handler.py` / `profile_manager.py` / `audio_settings.py` / `sidebar_builder.py` - Mixins composed into `VideoConverterApp`

**UI Components (ui/):**
- `conversion_page.py` - File queue management and conversion initiation
- `progress_page.py` - Real-time conversion progress tracking with FFmpeg output parsing
- `video_edit_page.py` - Video editor with trim/crop/color adjustment features
- `video_edit_ui.py` - Video editing UI controls (sidebar panels for editing tools)
- `mpv_player.py` - MPV-based video player for preview and editing
- `header_bar.py` - Application header with contextual buttons
- `settings_page.py` - Advanced settings dialog
- `welcome_dialog.py` - First-run welcome screen
- `dependency_dialog.py` - FFmpeg/MPV dependency installation dialog

**Utilities (utils/):**
- `conversion.py` - Conversion execution logic, progress monitoring, FFmpeg process management
- `file_info.py` - Video file metadata extraction using FFprobe
- `settings_manager.py` - GSettings-based persistence for user preferences
- `video_settings.py` - Video filter calculations (brightness, saturation, hue conversions)
- `subtitle_processor.py` - Subtitle extraction and handling
- `dependency_checker.py` - Runtime dependency verification
- `tooltip_helper.py` - Contextual tooltip management system

**Conversion Engine:**
- `big-video-converter/usr/bin/big-video-converter` - Bash script that wraps FFmpeg with GPU detection, encoder selection, and parameter validation

### Key Architectural Patterns

**Dual-Stack Navigation:**
The UI uses two ViewStacks for navigation:
1. `main_stack` - Switches between main view and progress view
2. `right_stack` - Within main view, switches between queue view and editor view
3. `left_stack` - Contextual sidebar that changes between conversion settings and editing tools

**State Management:**
- Global app state in `VideoConverterApp` class (queue, conversion state, video editing parameters)
- Per-file metadata stored in `conversion_page.file_metadata` dict (trim segments, crop values, color adjustments)
- User preferences in GSettings via `SettingsManager`

**Process Management:**
- Conversions run as subprocess.Popen instances
- Progress parsing via FFmpeg stderr output (frame, fps, time, bitrate)
- Process tree termination using pkill for cleanup
- Queue processing with sequential execution managed by `process_next_in_queue()`

**Translation System:**
- Uses gettext for i18n
- Translation files expected in `/usr/share/locale` or AppImage `locale/` directory
- Constants refreshed after gettext initialization via `refresh_translations()`

## Development Commands

### Package Building
```bash
# Build Arch Linux package (uses PKGBUILD in pkgbuild/)
cd pkgbuild
makepkg -si
```

### Running the Application

**From system installation:**
```bash
big-video-converter-gui
# or
/usr/bin/big-video-converter-gui
```

**From source (development):**
```bash
cd big-video-converter/usr/share/big-video-converter
python3 main.py
```

**Using the conversion script directly (CLI):**
```bash
# Show help
big-video-converter --help

# Convert with auto GPU detection
gpu=auto big-video-converter input.mkv

# Convert with custom settings
gpu=nvidia video_quality=high preset=slow big-video-converter input.mkv

# Extract subtitles only
subtitle_extract=extract big-video-converter input.mkv
```

### File Structure for Package Installation

The application expects this installation structure:
```
/usr/share/big-video-converter/    # Python application code
/usr/bin/big-video-converter        # FFmpeg wrapper script
/usr/bin/big-video-converter-gui    # GUI launcher
/usr/share/applications/            # Desktop files
/usr/share/icons/                   # Application icons
/usr/share/locale/                  # Translation files
```

## Important Development Notes

### Video Editing Metadata
- Each file in the queue has associated metadata stored in `conversion_page.file_metadata[file_path]`
- Metadata includes: `trim_segments`, `crop_left/right/top/bottom`, `brightness`, `saturation`, `hue`
- Metadata is initialized when files are added to queue and cleared when removed
- Trim segments are arrays of `{start, end, keep}` dicts representing time ranges

### FFmpeg Command Construction
- `ui/conversion_page.py` builds the environment for a conversion; the bash script assembles the actual FFmpeg command
- User-supplied "additional options" are validated by `utils/ffmpeg_options.py` before being exported: `ALLOWED_FFMPEG_FLAGS` whitelists the flags, shell metacharacters are rejected, and accepted tokens are re-quoted with `shlex.quote` because the script expands `$options` through `eval`
- Environment variables pass settings to the bash wrapper script
- The bash script (`usr/bin/big-video-converter`) handles GPU detection and encoder selection
- `utils/ffmpeg_path.py` resolves which ffmpeg/ffprobe to use (override env var → AppImage bundle → jellyfin-ffmpeg → install prefix → PATH) and the GUI exports it as `ffmpeg_executable` so the script uses the same binary

### Files televisions can open (decided in the bash script)
- Output wider than 1920 px with H.264 switches to H.265 only when `uhd_h265=1` (the GUI's "Switch 4K H.264 output to H.265 automatically" switch, setting `uhd-h265`, off by default). TVs decode UHD only in HEVC/VP9/AV1; H.264 4K plays on PCs alone
- An HDR source (PQ/HLG, `is_hdr`) going to an 8-bit encoder is tone mapped (`zscale`+`tonemap`) and tagged bt709 — never truncated with the bt2020/PQ tags left on, which produced "HDR10 in 8 bits" files that TVs refused. Tone mapping forces Stage 2 (software decode)
- The H.264 level is computed from output size and frame rate (`h264_level_for`); it was a fixed 4.1 in libx264/VAAPI/QSV, i.e. a 1080p limit stamped on 4K streams
- HEVC in MP4/MOV is tagged `hvc1`
- `tests/test_video_format.py` proves each of these with generated media

### Probing media files
- Never treat a non-zero ffprobe exit as "corrupted file": ffprobe aborts without output when it cannot open a decoder for *any* stream (dvd_subtitle tracks on FFmpeg 9 do this), while ffmpeg converts the file fine
- The bash script falls back to parsing `ffmpeg -i` (`ffmpeg_probe_streams`, `fb_audio_info`, `fb_subtitle_codecs`, `fb_audio_channels`); `utils/file_info.py` does the same in Python via `_probe_with_ffmpeg`
- When audio detection is inconclusive, assume audio exists — the opposite silently drops the audio tracks
- ffprobe 9 prints the `stream_groups` section (MOV Track Reference groups, present whenever the file has a `tmcd` timecode track) for every `-show_entries stream=...` query: the CSV output gains a blank row and repeats the referenced streams without their tags. Never read `csv=p=0` output raw in the bash script; go through `probe_rows`/`probe_value`, which keep one row per index. Issue #27 (`-ac:a:0` without a value, exit 127) was this
- Launch-time probes in Python (`has_audio_streams`, `get_video_dimensions`, `check_mp4_compatibility`) are memoized by file identity in `utils/file_info.py`; the queue warms them on a worker thread (`warm_probe_cache`) so `force_start_conversion` never blocks the GTK loop on ffprobe

### Driver failures (decided in the bash script)
- Before the first GPU stage, `hw_encoder_smoke_test` encodes two synthetic frames with the selected hardware encoder under `timeout`; failure or a hang (`gpu_probe_timeout`, 30 s) means software encoding with a "GPU encoder check failed" line in the log. `gpu_smoke_test=0` skips it
- `run_ffmpeg` runs ffmpeg in the background with `-progress` and kills it when frame/time/size stop changing for `stall_timeout` seconds (600 by default, 0 disables); the attempt returns 124 and the stage chain continues. `cleanup` kills that child on INT/TERM
- Exit code 255 from ffmpeg is an ordinary error (`AVERROR(EPERM)`), not a cancel: cancels end the script through its traps (130/143) and never reach the fallback logic
- MP4 output clears the video `timecode` tag so the muxer does not add a `tmcd` data track (`keep_timecode=1` keeps it; MOV output is untouched)
- `tests/test_probe_and_driver_fallback.py` proves all of this with generated media and fake ffmpeg wrappers
- A sized (two-pass) libx265 encode uses `-pass` + `-x265-stats` on FFmpeg 8+, and `-x265-params ...:pass=N:stats=` on older releases (`x265_takes_stats` asks the binary once). Debian 13, the CI image, ships FFmpeg 7.1

### GPU Acceleration
- Auto-detection checks for NVIDIA (NVENC), AMD/Intel (VAAPI), Intel (QSV), or Vulkan support
- Falls back to software encoding if GPU encoding fails
- Partial GPU mode: CPU decode + GPU encode (for compatibility)
- GPU settings: `auto`, `nvidia`, `amd`, `intel`, `vulkan`, `software`

### Progress Tracking
- FFmpeg stderr is parsed line-by-line for progress info
- Key patterns: `frame=`, `fps=`, `time=`, `bitrate=`, `speed=`
- Duration extracted from FFmpeg output or ffprobe
- Progress percentage calculated as `(current_time / total_duration) * 100`

### Translation Management
- Translatable strings use `_("text")` with gettext
- Constants module has translation refresh function called after gettext setup
- Tooltips are defined in `get_tooltips()` function in constants.py

### Conversion Queue Processing
- Queue stored as deque in `app.conversion_queue`
- Single file conversion mode vs batch queue mode (different state management)
- `conversion_completed(success)` callback handles queue advancement
- Re-entry prevention via `_processing_completion` flag
- Current file tracked in `current_processing_file` to prevent double-processing

### Window State Persistence
- Window size, position, maximized state saved on close
- Sidebar position persisted
- Last accessed directory remembered for file choosers

### Dependency Handling
- On first run, checks for FFmpeg and MPV using `DependencyChecker`
- Shows installation dialog with distro-specific commands if missing
- Supports Arch, Debian/Ubuntu, Fedora, openSUSE package managers

## Code Style Notes

- GTK4 and libadwaita widgets used throughout (Adw.ApplicationWindow, Adw.PreferencesGroup, etc.)
- Signal connections use lambda for simple callbacks or dedicated methods for complex logic
- GLib.idle_add and GLib.timeout_add for thread-safe UI updates
- File paths always normalized and made absolute to prevent path traversal issues
- Process cleanup on window close via `_on_window_close_request()` handler
