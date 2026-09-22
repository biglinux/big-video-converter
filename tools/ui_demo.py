#!/usr/bin/env python3
"""Render deterministic application states for UI review and screenshot CI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "big-video-converter/usr/share/big-video-converter"
sys.path.insert(0, os.fspath(SHARE))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from main import VideoConverterApp
from utils.job_options import normalize_metadata


def make_media(directory: Path) -> list[Path]:
    """Create small, visually distinct videos when no fixtures were supplied."""

    directory.mkdir(parents=True, exist_ok=True)
    specifications = (
        ("travel-montage.mp4", "0x315CFF", "0x7F5AF0", "Trip to Iceland"),
        ("family-portrait.mp4", "0xEE5E7A", "0xF5A524", "Family portrait"),
        ("product-demo.mp4", "0x00A896", "0x19B5FE", "Product demo"),
    )
    paths: list[Path] = []
    for name, first, second, label in specifications:
        path = directory / name
        paths.append(path)
        if path.exists():
            continue
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"color=c={first}:s=1280x720:d=6:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-vf",
            (
                f"drawbox=x='mod(t*170,iw)':y=90:w=280:h=540:color={second}@0.88:t=fill,"
                "drawbox=x=70:y=470:w=760:h=150:color=black@0.42:t=fill,"
                f"drawtext=text='{label}':x=105:y=510:fontsize=52:fontcolor=white"
            ),
            "-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", os.fspath(path),
        ]
        subprocess.run(command, check=True, timeout=45)
    return paths


class DemoApp(VideoConverterApp):
    def __init__(self, args: argparse.Namespace, media: list[Path]):
        self.demo_args = args
        self.demo_media = media
        super().__init__()

    def on_activate(self, _app) -> None:
        self._setup_icon_theme()
        if not hasattr(self, "window") or self.window is None:
            self._create_window()
        manager = Adw.StyleManager.get_default()
        manager.set_color_scheme(
            Adw.ColorScheme.FORCE_DARK
            if self.demo_args.theme == "dark"
            else Adw.ColorScheme.FORCE_LIGHT
        )
        self.window.set_default_size(self.demo_args.width, self.demo_args.height)
        self.window.present()
        GLib.timeout_add(180, self._populate_demo)

    def _metadata(self, profile: str, resolution: str) -> dict:
        metadata = normalize_metadata(None)
        metadata["preset_snapshot"] = {"name": profile}
        metadata["resolution_mode"] = resolution
        return metadata

    def _populate_queue(self) -> None:
        self.conversion_queue.clear()
        metadata = (
            self._metadata("WhatsApp", "720x1280"),
            self._metadata("YouTube 1080p", "1920x1080"),
            self._metadata("Archive · AV1", "original"),
        )
        for path, options in zip(self.demo_media, metadata):
            self.conversion_queue.append(os.fspath(path))
            self.conversion_page.file_metadata[os.fspath(path)] = options
        self.show_queue_view()
        self.conversion_page.update_queue_display()
        if hasattr(self.header_bar, "update_queue_count"):
            self.header_bar.update_queue_count(len(self.conversion_queue))

    def _populate_progress(self, complete: bool = False) -> None:
        paths = [os.fspath(path) for path in self.demo_media]
        self.progress_page.initialize_queue(paths)
        rows = list(self.progress_page.queue_items.values())
        if complete:
            for row in rows:
                row.start_conversion(None, "demo")
                row.update_progress(1.0, "Finished")
                row.mark_complete(True)
            self.progress_page.completed_count = len(rows)
            self.progress_page._update_overall_progress()
            self.progress_page._show_completion_summary()
        else:
            rows[0].start_conversion(None, "demo-0")
            rows[0].update_progress(0.68, "GPU encoding · H.264 | 124 fps")
            rows[0].add_output_text(
                "Preparing the output safely\n"
                "Hardware encoder validated\n"
                "Encoding frame 3680 of 5412"
            )
            rows[1].start_conversion(None, "demo-1")
            rows[1].update_progress(0.26, "Preparing audio")
            rows[2]._set_state("pending")
            self.progress_page.completed_count = 0
            self.progress_page._update_overall_progress()
        self.show_progress_page()

    def _populate_demo(self) -> bool:
        screen = self.demo_args.screen
        if screen == "queue-empty":
            self.conversion_queue.clear()
            self.show_queue_view()
            self.conversion_page.update_queue_display()
        elif screen == "queue":
            self._populate_queue()
        elif screen == "progress":
            self._populate_progress(False)
        elif screen == "complete":
            self._populate_progress(True)
        elif screen == "editor":
            self._populate_queue()
            self.show_editor_for_file(os.fspath(self.demo_media[0]))
        elif screen == "welcome":
            from ui.welcome_dialog import WelcomeDialog
            WelcomeDialog(self.window, self.settings_manager).present()
        elif screen == "presets":
            self._populate_queue()
            from ui.presets_dialog import show_presets_dialog
            show_presets_dialog(self.window, self)
        elif screen == "video-options":
            self._populate_queue()
            self.conversion_page.on_file_options_by_path(os.fspath(self.demo_media[0]))
        elif screen == "audio":
            self._populate_queue()
            from ui.audio_dialog import show_audio_dialog
            show_audio_dialog(self.window, self)
        elif screen == "noise":
            self._populate_queue()
            from ui.noise_dialog import show_noise_dialog
            show_noise_dialog(self.window, self)
        elif screen == "subtitles":
            self._populate_queue()
            from ui.subtitles_dialog import show_subtitles_dialog
            show_subtitles_dialog(self.window, self)
        elif screen == "advanced":
            self._populate_queue()
            from ui.extra_dialog import show_extra_dialog
            show_extra_dialog(self.window, self)
        else:
            raise ValueError(f"Unknown demo screen: {screen}")

        GLib.timeout_add(2400 if screen == "editor" else 900, self._mark_ready)
        return GLib.SOURCE_REMOVE

    def _mark_ready(self) -> bool:
        if self.demo_args.ready:
            Path(self.demo_args.ready).write_text("ready\n", encoding="utf-8")
        if self.demo_args.auto_quit:
            GLib.timeout_add_seconds(self.demo_args.auto_quit, self._quit_demo)
        return GLib.SOURCE_REMOVE

    def _quit_demo(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--screen",
        choices=(
            "queue-empty", "queue", "progress", "complete", "editor",
            "welcome", "presets", "video-options", "audio", "noise",
            "subtitles", "advanced",
        ),
        default="queue",
    )
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=760)
    parser.add_argument("--ready")
    parser.add_argument("--media-dir")
    parser.add_argument("--auto-quit", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    media_dir = Path(args.media_dir or tempfile.mkdtemp(prefix="bvc-demo-"))
    media = make_media(media_dir)
    app = DemoApp(args, media)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
