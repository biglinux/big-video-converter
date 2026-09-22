"""Focused native tests for editor controls, cleanup and review evidence."""

from pathlib import Path
import sys
import types
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "big-video-converter/usr/share/big-video-converter"))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

fake_mpv = types.ModuleType("ui.mpv_player")


class StubMPVPlayer:
    use_x11_mode = True


fake_mpv.MPVPlayer = StubMPVPlayer
sys.modules["ui.mpv_player"] = fake_mpv

from ui.video_edit_ui import VideoEditUI
from ui.video_edit_page import VideoEditPage


class FakePlayer:
    def __init__(self):
        self.cleaned = False

    def get_duration(self):
        return 6.0

    def cleanup(self):
        self.cleaned = True


class FakePage:
    def __init__(self):
        self.app = SimpleNamespace()
        self.position_changed_handler_id = None
        self.video_fps = 30
        self.is_playing = False
        self.crop_edit_mode = False
        self.user_is_dragging_slider = False
        self.trim_segments = []
        self.first_segment_point = None
        self.current_video_path = None
        self.mpv_player = FakePlayer()
        self.speed = 1.0

    def __getattr__(self, name):
        if name.startswith(("on_", "reset_", "_on_")):
            return lambda *_args, **_kwargs: None
        raise AttributeError(name)

    def _save_file_metadata(self):
        pass

    def _update_segments_listbox(self):
        pass


class VideoEditUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self.page = FakePage()
        self.ui = VideoEditUI(self.page)
        self.ui.create_page()

    def tearDown(self):
        self.ui.disconnect_all_handlers()

    def test_zero_width_drag_is_ignored(self):
        self.ui.position_scale.set_value(1.25)
        self.ui._update_slider_drag(4, 0)
        self.assertEqual(self.ui.position_scale.get_value(), 1.25)

        self.page.trim_segments = [{"start": 1.0, "end": 3.0}]
        self.ui._dragging_segment = {"segment_index": 0, "edge": "start"}
        self.ui._update_segment_drag(4, 0)
        self.assertEqual(self.page.trim_segments[0]["start"], 1.0)
        self.assertIsNone(self.ui._find_segment_edge_at_position(0, 0))

    def test_disconnect_cancels_hide_timer_and_restores_controls(self):
        self.ui._schedule_hide_controls(delay=60000)
        self.assertIsNotNone(self.ui.hide_timer_id)
        self.ui.overlay_controls.set_visible(False)
        self.ui.disconnect_all_handlers()
        self.assertIsNone(self.ui.hide_timer_id)
        self.assertTrue(self.ui.overlay_controls.get_visible())

    def test_controls_have_contextual_names_and_output_row(self):
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.ui.populate_sidebar(sidebar)

        self.assertEqual(self.ui.position_scale.get_tooltip_text(), "Video position")
        self.assertEqual(self.ui.volume_scale.get_tooltip_text(), "Volume")
        self.assertEqual(self.ui.speed_button.get_tooltip_text(), "Playback speed: 1.0x")
        self.assertEqual(self.ui.output_mode_row.get_title(), "Segment output")
        self.assertEqual(
            self.ui.output_mode_row.get_subtitle(),
            "Choose how marked segments are saved",
        )
        self.assertEqual(
            self.ui.output_mode_combo.get_tooltip_text(),
            "Segment output",
        )
        self.assertEqual(self.ui.crop_left_spin.get_tooltip_text(), "Crop from left")
        self.assertEqual(self.ui.brightness_scale.get_tooltip_text(), "Brightness")

    def test_speed_summary_updates_with_selection(self):
        self.ui._on_speed_btn_clicked(None, 1.5)
        self.assertEqual(self.ui.speed_button.get_label(), "1.5x")
        self.assertEqual(
            self.ui.speed_button.get_tooltip_text(),
            "Playback speed: 1.5x",
        )

    def test_page_cleanup_removes_seek_timer_and_transient_mark(self):
        page = VideoEditPage.__new__(VideoEditPage)
        page.cleanup_called = False
        page.current_video_path = None
        page.app = SimpleNamespace()
        page.processor = SimpleNamespace(invalidate=lambda: None)
        page.crop_edit_mode = False
        page.position_update_id = None
        page._seek_cooldown = True
        page._seek_cooldown_timer_id = GLib.timeout_add(60000, lambda: GLib.SOURCE_REMOVE)
        page.first_segment_point = 1.5
        page.loading_video = False
        page.requested_video_path = None
        page.is_playing = True
        page.mpv_player = FakePlayer()
        page.ui = SimpleNamespace(
            mark_time_label=Gtk.Label(visible=True),
            mark_cancel_button=Gtk.Button(visible=True),
            play_pause_button=Gtk.Button(),
            disconnect_all_handlers=lambda: None,
        )

        page.cleanup()

        self.assertIsNone(page._seek_cooldown_timer_id)
        self.assertFalse(page._seek_cooldown)
        self.assertIsNone(page.first_segment_point)
        self.assertFalse(page.ui.mark_time_label.get_visible())
        self.assertFalse(page.ui.mark_cancel_button.get_visible())
        self.assertTrue(page.mpv_player.cleaned)

    def test_time_fields_are_named(self):
        page = VideoEditPage.__new__(VideoEditPage)
        _box, fields = page._create_time_input_fields(3661.25)
        self.assertEqual(
            [field.get_tooltip_text() for field in fields],
            ["Hours", "Minutes", "Seconds", "Centiseconds"],
        )

    def test_editor_states_are_in_visual_evidence_matrix(self):
        demo = (ROOT / "tools/ui_demo.py").read_text(encoding="utf-8")
        workflow = (
            ROOT / ".github/workflows/exact-ui-review-r2.yml"
        ).read_text(encoding="utf-8")
        for state in ("editor", "editor-crop", "editor-segments"):
            with self.subTest(state=state):
                self.assertIn(f'"{state}"', demo)
                self.assertIn(state, workflow)
        self.assertIn("expected=40", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
