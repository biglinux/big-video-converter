"""Focused native tests for editor controls, cleanup and review evidence."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "big-video-converter/usr/share/big-video-converter"))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
from ui import video_processing
from ui.mpv_player import MPVPlayer
from ui.video_edit_page import VideoEditPage
from ui.video_edit_ui import VideoEditUI


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
        self.assertEqual(
            self.ui.speed_button.get_tooltip_text(), "Playback speed: 1.0x"
        )
        self.assertEqual(self.ui.output_mode_combo.get_title(), "Segment output")
        self.assertEqual(
            self.ui.output_mode_combo.get_subtitle(),
            "Join segments into a single file",
        )
        self.assertEqual(
            self.ui.output_mode_combo.get_tooltip_text(),
            "Choose how marked segments are saved",
        )
        self.assertEqual(self.ui.crop_left_spin.get_tooltip_text(), "Crop from left")
        self.assertEqual(self.ui.brightness_scale.get_tooltip_text(), "Brightness")
        self.assertEqual(
            self.ui.sidebar_button.get_tooltip_text(), "Show editing tools (F9)"
        )

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
        page.is_video_fullscreen = False
        page.position_update_id = None
        page._seek_cooldown = True
        page._seek_cooldown_timer_id = GLib.timeout_add(
            60000, lambda: GLib.SOURCE_REMOVE
        )
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


class Settings:
    def get_value(self, _key, default=None):
        return default

    def save_setting(self, *_args):
        pass


def probe_answer(duration="10.0", size="1000", frame_rate="25/1"):
    return {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 640,
                     "height": 360, "duration": duration, "avg_frame_rate": frame_rate}],
        "format": {"size": size},
    }


class VideoEditPageTests(unittest.TestCase):
    """A real editor page; the probe worker and mpv are left out."""

    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self.errors = []
        self.window = Gtk.ApplicationWindow()
        app = SimpleNamespace(
            settings_manager=Settings(), split_view=Adw.OverlaySplitView(),
            window=self.window, conversion_page=object(),
            show_error_dialog=self.errors.append,
        )
        self.page = VideoEditPage(app, SimpleNamespace(file_metadata={}))
        self.page.mpv_player = None
        self.page.app_state.file_metadata = self.metadata = {}
        self.loads = []
        self.page.processor.load_video = lambda path: self.loads.append(path) or True
        self.folder = tempfile.TemporaryDirectory()
        self.videos = []
        for name in ("a.mp4", "b.mp4"):
            path = Path(self.folder.name) / name
            path.write_bytes(b"")
            self.videos.append(str(path))

    def tearDown(self):
        self.page.ui.disconnect_all_handlers()
        self.window.destroy()
        self.folder.cleanup()

    def finish_loading(self, path, info=None):
        processor = self.page.processor
        processor._on_video_info_loaded(info or probe_answer(), path, processor._generation)

    def test_leaving_while_loading_keeps_the_previous_edits_out_of_the_new_video(self):
        first, second = self.videos
        self.assertTrue(self.page.set_video(first))
        self.finish_loading(first)
        self.page.trim_segments.append({"start": 0.1, "end": 0.5})
        self.page.crop_left = 10
        self.page._save_file_metadata()
        self.page.cleanup()

        self.assertTrue(self.page.set_video(second))
        self.page.cleanup()  # back before the slow probe answered
        self.assertNotIn(second, self.metadata)

        self.assertTrue(self.page.set_video(second))
        self.finish_loading(second)
        self.assertEqual(self.page.trim_segments, [])
        self.assertEqual(self.page.crop_left, 0)
        self.page.trim_segments.append({"start": 1.0, "end": 2.0})
        self.page._save_file_metadata()
        self.assertEqual(self.metadata[first]["trim_segments"], [{"start": 0.1, "end": 0.5}])
        self.assertEqual(self.metadata[first]["crop_left"], 10)
        self.assertIsNot(self.metadata[second]["trim_segments"], self.page.trim_segments)

    def test_second_activation_of_the_loading_row_is_the_same_load(self):
        first, _second = self.videos
        self.assertTrue(self.page.set_video(first))
        self.assertTrue(self.page.set_video(first))
        self.assertEqual(self.loads, [first])
        self.assertTrue(self.page.loading_video)

    def test_unreadable_probe_answers_never_leave_the_editor_loading(self):
        first, _second = self.videos
        self.page.set_video(first)
        self.finish_loading(first, probe_answer(duration="N/A", size="N/A"))
        self.assertFalse(self.page.loading_video)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.page.video_duration, 0)
        self.assertEqual(self.page.ui.info_filesize_label.get_text(), "0.00 MB")

        self.page.set_video(first)
        self.finish_loading(first, probe_answer(frame_rate="x/y"))
        self.assertFalse(self.page.loading_video)
        self.assertEqual(len(self.errors), 1)

        def fail(_path):
            raise RuntimeError("boom")

        self.page.set_video(first)
        with mock.patch.object(video_processing, "get_video_file_info", fail), \
                mock.patch.object(video_processing.GLib, "idle_add", lambda f, *a: f(*a)):
            self.page.processor._get_video_info_thread(first, self.page.processor._generation)
        self.assertFalse(self.page.loading_video)
        self.assertEqual(self.errors[-1], "Error getting video info: boom")

    def test_track_menus_follow_the_loaded_video(self):
        player = SimpleNamespace(
            audio_tracks=[{"index": 1, "label": "eng"}, {"index": 2, "label": "por"}],
            subtitle_tracks=[{"index": 1, "label": "eng"}],
            current_audio_track=2, current_subtitle_track=1,
        )
        player.get_audio_tracks = lambda: player.audio_tracks
        player.get_subtitle_tracks = lambda: player.subtitle_tracks
        self.page.mpv_player = player

        def state(name):
            return self.window.lookup_action(name).get_state().get_boolean()

        self.page.update_audio_subtitle_controls()
        self.assertEqual(self.page.ui.audio_track_menu.get_n_items(), 2)
        self.assertTrue(state("audio-track-2"))
        self.assertTrue(state("subtitle-track-1"))

        player.current_audio_track, player.current_subtitle_track = 1, -1
        self.page.update_audio_subtitle_controls()
        self.assertTrue(state("audio-track-1") and not state("audio-track-2"))
        self.assertTrue(state("subtitle-track-disabled") and not state("subtitle-track-1"))

        player.audio_tracks, player.subtitle_tracks = [], []
        self.page.update_audio_subtitle_controls()
        self.assertEqual(self.page.ui.audio_track_menu.get_n_items(), 0)
        self.assertFalse(self.page.ui.audio_track_button.get_visible())
        self.assertFalse(self.page.ui.subtitle_button.get_visible())

    def test_crop_spins_show_visible_edges_within_the_frame(self):
        page = self.page
        page.video_width, page.video_height = 640, 360
        page.crop_left, page.crop_top = 10, 4
        page.rotation = 90
        page._update_transform_state()
        ui = page.ui
        # Turned clockwise, the source left is shown on top, its top on the right.
        self.assertEqual(ui.crop_top_spin.get_value(), 10)
        self.assertEqual(ui.crop_right_spin.get_value(), 4)
        # 360 shown wide: an edge leaves at least two pixels to its opposite.
        self.assertEqual(ui.crop_left_spin.get_adjustment().get_upper(), 354)
        self.assertEqual(ui.crop_bottom_spin.get_adjustment().get_upper(), 628)

        crops = []
        page.mpv_player = SimpleNamespace(set_crop=lambda *margins: crops.append(margins))
        ui.crop_left_spin.set_value(9999)  # typed; the spin clamps it
        # The visible left is the source bottom; mpv crops the source.
        self.assertEqual(crops, [(10, 0, 4, 354)])
        self.assertEqual(ui.crop_right_spin.get_adjustment().get_upper(), 4)

    def test_segments_end_with_the_video(self):
        page = self.page
        page.video_duration = 10.0
        page._show_error_dialog = lambda title, message: self.errors.append(message)
        _box, start = page._create_time_input_fields(12)
        _box, end = page._create_time_input_fields(15)
        self.assertIsNone(page._segment_times(start, end))
        self.assertIn("0:00:10.000", self.errors[-1])
        _box, start = page._create_time_input_fields(1)
        self.assertEqual(page._segment_times(start, end), (1, 10.0))


class MPVPlayerTrackTests(unittest.TestCase):
    def test_tracks_are_reset_per_file_and_reported_when_loaded(self):
        player = MPVPlayer.__new__(MPVPlayer)
        player._reset_tracks()
        player.audio_tracks = [{"index": 1, "label": "old"}]
        player.current_file = None
        reported = []
        player.on_tracks_changed = lambda: reported.append(list(player.audio_tracks))
        player.mpv_instance = SimpleNamespace(
            loadfile=lambda _path: None, duration=None, aid=1, sid=False,
            track_list=[{"id": 1, "type": "audio"}, {"id": 1, "type": "sub", "lang": "por"}],
        )
        with tempfile.NamedTemporaryFile() as video:
            self.assertTrue(player.load_video(video.name))
            self.assertEqual(player.audio_tracks, [])
            player._on_file_loaded()
        self.assertEqual(reported, [[{"index": 1, "label": "Track 1"}]])
        self.assertEqual(player.current_subtitle_track, -1)  # python-mpv's False

        player.current_file = None  # stopped: a late event reports nothing
        player._on_file_loaded()
        self.assertEqual(len(reported), 1)


class FakeMpv(dict):
    width, height, video_rotate = 128, 72, 0

    def __init__(self, file_rotation=0):
        super().__init__(hwdec="no")
        self.video_dec_params = {"rotate": file_rotation}
        self.vf = []

    def command(self, *args):
        if args[:2] == ("vf", "set"):
            self.vf.append(args[2])


class MPVPlayerTransformTests(unittest.TestCase):
    """The GL renderer mirrors the rendered picture; mpv crops and turns it.
    So the preview matches the output: crop, then turn, hflip, vflip."""

    def player(self, file_rotation=0, render_context=None):
        player = MPVPlayer.__new__(MPVPlayer)
        player.mpv_instance = FakeMpv(file_rotation)
        player.render_context = render_context
        player.video_widget = SimpleNamespace(queue_render=lambda: None)
        player._flip_h = player._flip_v = False
        player._user_rotation = 0
        player._base_hwdec = None
        player.crop_left = player.crop_right = player.crop_top = player.crop_bottom = 0
        return player

    def test_a_crop_set_before_the_first_frame_is_applied_on_reconfig(self):
        for render_context in (None, object()):
            player = self.player(render_context=render_context)
            player.mpv_instance.width = player.mpv_instance.height = 0
            player.set_crop(10, 0, 4, 0)
            self.assertNotIn("video-crop", player.mpv_instance)
            player.mpv_instance.width, player.mpv_instance.height = 128, 72
            player._on_video_reconfig()
            self.assertEqual(player.mpv_instance["video-crop"], "118x68+10+4")

    def test_flips_never_become_filters_or_move_the_crop(self):
        player = self.player(render_context=object())
        player.set_crop(10, 0, 4, 0)
        player.set_video_effects("hqdn3d=4", "", "pq")
        player.set_video_flip(True, True)
        player.set_rotation(90)
        player.set_video_effects("", "", "auto")
        self.assertEqual(player.mpv_instance.vf, [
            "format=gamma=pq:primaries=bt.2020:colormatrix=bt.2020-ncl,lavfi=graph=%8%hqdn3d=4",
            ""])
        self.assertEqual(player.mpv_instance["video-crop"], "118x68+10+4")

    def test_the_crop_undoes_the_file_rotation(self):
        player = self.player(file_rotation=90, render_context=object())
        player.set_crop(10, 0, 4, 0)
        self.assertEqual(player.mpv_instance["video-crop"], "124x62+4+0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
