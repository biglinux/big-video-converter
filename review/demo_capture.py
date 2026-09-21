#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "big-video-converter/usr/share/big-video-converter"
sys.path.insert(0, os.fspath(APP))

from gi.repository import GLib
from main import VideoConverterApp

OUT = Path(os.environ.get("BVC_SCREENSHOT_DIR", "/tmp/bvc-screenshots"))
OUT.mkdir(parents=True, exist_ok=True)
FILES = [
    Path(os.environ["BVC_DEMO_VIDEO_1"]),
    Path(os.environ["BVC_DEMO_VIDEO_2"]),
    Path(os.environ["BVC_DEMO_VIDEO_3"]),
]


def shot(name: str) -> None:
    subprocess.run(["scrot", "-o", os.fspath(OUT / f"{name}.png")], check=True)


app = VideoConverterApp()
app.settings_manager.settings.update(
    {
        "show-welcome-dialog": False,
        "video-preview-render-mode": "software",
        "window-width": 1440,
        "window-height": 900,
        "window-maximized": False,
    }
)
app.settings_manager.save_to_disk()


def later(delay_ms, func):
    def run():
        try:
            func()
        except Exception as exc:
            print(f"DEMO ERROR: {exc!r}", file=sys.stderr)
            app.quit()
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(delay_ms, run)


def on_ready():
    app.window.set_default_size(1440, 900)
    app.window.present()
    later(1200, lambda: shot("00-empty-queue"))

    def queue():
        for path in FILES:
            app.add_file_to_queue(os.fspath(path))
        app.show_queue_view()
        later(1500, lambda: shot("01-queue"))
    later(1700, queue)

    def welcome():
        app.on_welcome_action(None, None)
        later(900, lambda: shot("02-welcome"))
        later(1300, lambda: subprocess.run(["xdotool", "key", "Escape"], check=False))
    later(3600, welcome)

    def presets():
        app._on_presets_activated(None)
        later(900, lambda: shot("03-presets"))
        later(1300, lambda: subprocess.run(["xdotool", "key", "Escape"], check=False))
    later(5400, presets)

    def encoding():
        app._on_video_encoding_activated(None)
        later(900, lambda: shot("04-encoding-dialog"))
        later(1300, lambda: subprocess.run(["xdotool", "key", "Escape"], check=False))
    later(7200, encoding)

    def editor():
        app.show_editor_for_file(os.fspath(FILES[0]))
        later(3500, lambda: shot("05-editor"))
    later(9000, editor)

    def progress():
        app.show_queue_view()
        app.progress_page.initialize_queue([os.fspath(p) for p in FILES])
        rows = list(app.progress_page.queue_items.values())
        rows[0].start_conversion(None, "demo-1")
        rows[0].update_progress(0.42, "Encoding on GPU | 128 fps")
        rows[1]._set_state("pending")
        rows[2].mark_complete(True, "/tmp/output.mp4")
        app.progress_page.completed_count = 1
        app.progress_page._update_overall_progress()
        app.show_progress_page()
        later(1200, lambda: shot("06-progress"))
    later(14000, progress)

    def progress_details():
        rows = list(app.progress_page.queue_items.values())
        rows[0].cmd_text.set_text("ffmpeg -i input.mp4 -c:v h264 output.mp4")
        rows[0].add_output_text("Encode mode: Decode GPU, encode GPU")
        rows[0].add_output_text("frame=245 fps=128 time=00:00:10.21")
        rows[0].details_button.set_active(True)
        later(900, lambda: shot("07-progress-details"))
    later(15800, progress_details)

    def complete():
        rows = list(app.progress_page.queue_items.values())
        if rows[0].status == "active":
            rows[0].mark_complete(True, "/tmp/output-1.mp4")
        if rows[1].status == "pending":
            rows[1].mark_complete(False)
        app.progress_page.completed_count = 3
        app.progress_page._show_completion_summary()
        later(900, lambda: shot("08-complete"))
        later(1500, app.quit)
    later(17600, complete)

    return GLib.SOURCE_REMOVE

GLib.timeout_add(900, on_ready)
raise SystemExit(app.run(["big-video-converter-demo"]))
