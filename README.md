# Big Video Converter
A modern graphical frontend for converting MKV videos to MP4 format, built with Python and GTK3.

## Description
Comm Big Converter provides an intuitive graphical interface to easily convert video files from MKV to MP4 format using FFmpeg as the conversion engine. This application offers both single-file conversion with advanced options and batch conversion capabilities for processing multiple files at once.

## Features
* **User-friendly interface** with GTK4 and libadwaita
* **Single file conversion** with extensive customization options
* **Batch conversion** for processing multiple MKV files in a directory
* **Real-time progress monitoring** with time remaining estimation
* **Advanced encoding options** including:
   * GPU acceleration support (NVIDIA, AMD, Intel)
   * Video quality adjustment
   * Multiple codec support (h264, h265, AV1, VP9)
   * Compression presets
   * Audio encoding options
   * Subtitle handling
   * Video resolution adjustment
* **Original file management** with option to automatically delete MKV files after successful conversion
* **Log file generation** for batch operations
* **Cross-desktop environment compatibility** (GNOME, XFCE, Cinnamon, KDE)
* **Wayland and X11 compatibility**

## Requirements
* Python 3.6+
* GTK 4.0+
* FFmpeg
* Python GObject Introspection (PyGI)
* Libva Utils
* Libva Mesa Driver

## Installation
From Package (Arch Linux / BigCommunity)

```
sudo pacman -S comm-big-converter
```


## Usage
### Converting a Single File
1. Go to the "Convert Single File" tab
2. Select an input MKV file
3. Configure encoding options as needed
4. Click "Convert File"
5. Monitor the progress in the dialog

### Batch Converting Multiple Files
1. Go to the "Convert Multiple Files" tab
2. Select a directory containing MKV files
3. Set the number of simultaneous processes
4. Specify minimum MP4 size and log file options
5. Choose whether to delete original files
6. Click "Convert All MKVs"

## Presets
Presets are TOML files that describe a complete recipe: codec, quality, speed,
resolution, audio, subtitles, container and, when needed, extra FFmpeg
arguments for a specific encoder. The program keeps deciding everything it
already does well (GPU detection, CPU fallback, 10-bit to 8-bit, HDR tone
mapping); a preset only states the result you want.

* Bundled presets (YouTube, Instagram Reels, X/Twitter, WhatsApp, DaVinci
  Resolve, AV1 archive) live in `/usr/share/big-video-converter/presets/`.
* Your own presets live in `~/.config/big-video-converter/presets/*.toml`.
  Open **Presets** in the sidebar to browse, search, import, duplicate or
  delete them.
* **Create with AI**: describe what you need, click *Copy prompt*, paste the
  prompt into any online assistant, then paste its answer back. The prompt
  carries the exact file format, the encoders available on your machine and
  asks the assistant to answer in your system language.
* From the command line: `preset_file=~/.config/big-video-converter/presets/youtube.toml big-video-converter video.mkv`.
  Variables set explicitly in the environment win over the preset.

A minimal preset:

```toml
format = 1

[preset]
name = "Instagram Reels"
tags = ["instagram", "vertical"]

[video]
codec = "h264"
quality = "high"
resolution = "1080x1920"
fps = 30

[audio]
mode = "reencode"
codec = "aac"
bitrate = "128k"

[container]
format = "mp4"

[encoder.libx264]
args = ["-tune", "film"]
```

## Configuration Options
The application offers extensive configuration options for video conversion:
* **GPU selection**: Auto-detect, NVIDIA, AMD, Intel, or software encoding
* **Video quality**: From very low to very high
* **Video codec**: h264, h265 (HEVC), AV1, VP9
* **Compression preset**: ultrafast, veryfast, faster, medium, slow, veryslow
* **Subtitle handling**: extract to SRT, embed in container, or ignore
* **Audio options**: copy original, re-encode with custom bitrate and channels
* **Resolution**: Set custom output resolution
* **Advanced options**: GPU partial mode, forced software encoding, video copy mode

## License
This project is licensed under the MIT License - see the LICENSE file for details.
