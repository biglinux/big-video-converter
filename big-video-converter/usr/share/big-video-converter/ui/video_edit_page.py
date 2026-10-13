import os
from copy import deepcopy

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
import gettext

from gi.repository import Gdk, Gio, GLib, GObject, Gtk

_ = gettext.gettext

import logging

from constants import NOISE_MODELS, noise_reduction_filter
from engine.crop_geometry import ASPECT_RATIOS
from engine.job_options import DEFAULT_METADATA

from ui.mpv_player import MPVPlayer
from ui.video_edit_picture import VideoEditPictureMixin
from ui.video_edit_segments import VideoEditSegmentsMixin
from ui.video_edit_ui import VideoEditUI
from ui.video_processing import VideoProcessor

logger = logging.getLogger(__name__)


class VideoEditPage(VideoEditPictureMixin, VideoEditSegmentsMixin):
    def __init__(self, app, app_state):
        self.app = app
        self.app_state = app_state
        self.settings = app.settings_store
        self.current_video_path = None
        self.video_duration = 0
        self.current_position = 0
        self.trim_segments = []
        self.first_segment_point = None  # first press of the mark button
        self.position_update_id = None
        self.position_changed_handler_id = None
        self.cleanup_called = False
        self.video_width = 0
        self.video_height = 0
        self.video_fps = 25
        self.crop_left = 0
        self.crop_right = 0
        self.crop_top = 0
        self.crop_bottom = 0
        self.brightness = 0.0
        self.contrast = 0.0
        self.saturation = 1.0
        self.hue = 0.0
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self.denoise = "off"
        self.sharpen = "off"
        self.stabilize = False
        self.effect_file = ""
        self.source_hdr = "auto"
        self.crop_edit_mode = False
        self.crop_aspect = "free"
        self.output_mode = self.settings.get_value("multi-segment-output-mode", "join")
        self._restoring_metadata = False
        self._updating_crop_spins = False
        self._updating_crop_aspect = False
        self._audio_filter_timer = None
        # Building the UI below already fires handlers that read the player.
        self.mpv_player = None
        self.processor = VideoProcessor(self)
        # Preview only the segments, as the joined export plays them.
        self.segments_only = False
        self.playback_speed = 1.0
        self._segment_jump_id = None
        self.ui = VideoEditUI(self)
        self.page = self.ui.create_page()
        self.mpv_player = MPVPlayer(self.ui.preview_video)
        self.mpv_player.on_tracks_changed = self.update_audio_subtitle_controls
        self.is_playing = False
        self.user_is_dragging_slider = False
        self._seek_cooldown = False
        self._seek_cooldown_timer_id = None
        self.loading_video = False
        self.requested_video_path = None

        self.is_video_fullscreen = False
        self._sidebar_shown_before_fullscreen = True
        app.split_view.bind_property(
            "show-sidebar",
            self.ui.sidebar_button,
            "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        # The compositor or a window-manager shortcut can end fullscreen too.
        app.window.connect("notify::fullscreened", self._on_window_fullscreened)
        # Escape leaves fullscreen wherever focus is, sidebar included; on the
        # window's bubble phase, dialogs and entries still get it first.
        escape = Gtk.EventControllerKey()
        escape.connect("key-pressed", self._on_window_key_pressed)
        app.window.add_controller(escape)

        # Keyboard shortcuts
        self._setup_keyboard_shortcuts()

    def _setup_keyboard_shortcuts(self):
        """Add keyboard shortcuts to the editor page."""
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.page.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts for the video editor."""
        if keyval == Gdk.KEY_space:
            self.on_play_pause_clicked(None)
            return True
        if keyval == Gdk.KEY_Left:
            self.seek_relative(-1)
            return True
        if keyval == Gdk.KEY_Right:
            self.seek_relative(1)
            return True
        if keyval == Gdk.KEY_comma:
            frame = -1 / self.video_fps if self.video_fps > 0 else -1 / 25
            self.seek_relative(frame)
            return True
        if keyval == Gdk.KEY_period:
            frame = 1 / self.video_fps if self.video_fps > 0 else 1 / 25
            self.seek_relative(frame)
            return True
        if keyval == Gdk.KEY_f or keyval == Gdk.KEY_F:
            self.on_toggle_fullscreen(None)
            return True
        if keyval == Gdk.KEY_m or keyval == Gdk.KEY_M:
            self.on_mark_segment_point(None)
            return True
        return False

    def _load_file_metadata(self, file_path):
        if not hasattr(self.app, "conversion_page"):
            return
        metadata = self.app_state.file_metadata.get(file_path)
        # The last join/split choice is the default for a video without one.
        default_output_mode = self.settings.get_value(
            "multi-segment-output-mode", "join"
        )
        if not metadata:
            metadata = self.app_state.file_metadata[file_path] = {
                **deepcopy(DEFAULT_METADATA),
                "output_mode": default_output_mode,
            }
        # A copy: until it is saved, an edit belongs to this video alone.
        self.trim_segments = [dict(s) for s in metadata.get("trim_segments", [])]
        self.crop_left = metadata.get("crop_left", 0)
        self.crop_right = metadata.get("crop_right", 0)
        self.crop_top = metadata.get("crop_top", 0)
        self.crop_bottom = metadata.get("crop_bottom", 0)
        self.brightness = metadata.get("brightness", 0.0)
        self.contrast = metadata.get("contrast", 0.0)
        self.saturation = metadata.get("saturation", 1.0)
        self.hue = metadata.get("hue", 0.0)
        self.rotation = metadata.get("rotation", 0)
        self.flip_h = metadata.get("flip_h", False)
        self.flip_v = metadata.get("flip_v", False)
        self.denoise = metadata.get("denoise", "off")
        self.sharpen = metadata.get("sharpen", "off")
        self.stabilize = metadata.get("stabilize", False)
        self.effect_file = metadata.get("effect_file", "")
        self.source_hdr = metadata.get("source_hdr", "auto")
        self.crop_aspect = metadata.get("crop_aspect", "free")
        self.output_mode = metadata.get("output_mode", default_output_mode)
        self._update_ui_from_metadata()
        self._update_segments_listbox()
        self.ui.update_segment_markers()
        self._sync_pauses()

    def _sync_pauses(self):
        # The pause finder lives in the editing sidebar, which main.py fills
        # after this page exists.
        if hasattr(self.ui, "pause_panel"):
            self.ui.pause_panel.sync()

    def _update_ui_from_metadata(self):
        self._restoring_metadata = True
        self._updating_crop_aspect = True
        self._updating_crop_spins = True
        try:
            if hasattr(self.ui, "brightness_scale"):
                self.ui.brightness_scale.set_value(self.brightness)
            if hasattr(self.ui, "contrast_scale"):
                self.ui.contrast_scale.set_value(self.contrast)
            if hasattr(self.ui, "saturation_scale"):
                self.ui.saturation_scale.set_value(self.saturation)
            if hasattr(self.ui, "hue_scale"):
                self.ui.hue_scale.set_value(self.hue)

            if hasattr(self.ui, "denoise_combo"):
                self.ui.update_effects_state(
                    self.denoise, self.sharpen, self.stabilize, self.effect_file
                )
            if hasattr(self.ui, "source_hdr_combo"):
                self.ui.update_source_hdr(self.source_hdr)

            if hasattr(self.ui, "output_mode_combo"):
                output_mode_index = {"join": 0, "split": 1}.get(self.output_mode, 0)
                self.ui.output_mode_combo.set_selected(output_mode_index)

            if hasattr(self.ui, "crop_aspect_combo"):
                aspect_values = tuple(ASPECT_RATIOS)
                try:
                    aspect_index = aspect_values.index(self.crop_aspect)
                except ValueError:
                    aspect_index = 0
                self.ui.crop_aspect_combo.set_selected(aspect_index)

            self.update_crop_spinbuttons()

            if self.mpv_player:
                self.mpv_player.set_brightness(self.brightness)
                self.mpv_player.set_contrast(self.contrast)
                self.mpv_player.set_saturation(self.saturation)
                self.mpv_player.set_hue(self.hue)
                self.mpv_player.set_crop(
                    self.crop_left, self.crop_right, self.crop_top, self.crop_bottom
                )
                self.mpv_player.set_rotation(self.rotation)
                self.mpv_player.set_video_flip(self.flip_h, self.flip_v)
                self._apply_video_effects()
                # MPV handles render updates internally

            self._update_transform_state()
        finally:
            self._restoring_metadata = False
            self._updating_crop_aspect = False
            self._updating_crop_spins = False

    def _save_file_metadata(self):
        """Persist edits in the per-file in-memory model.

        Called on every change, slider drags included: the state is a dict, so
        writing it is cheap and no pending timer can lose an edit.
        """
        if self._restoring_metadata:
            return
        if not self.current_video_path or not hasattr(self.app, "conversion_page"):
            return
        metadata = self.app_state.file_metadata.get(self.current_video_path, {})
        metadata.update(
            {
                "trim_segments": [dict(s) for s in self.trim_segments],
                "crop_left": self.crop_left,
                "crop_right": self.crop_right,
                "crop_top": self.crop_top,
                "crop_bottom": self.crop_bottom,
                "brightness": self.brightness,
                "contrast": self.contrast,
                "saturation": self.saturation,
                "hue": self.hue,
                "rotation": self.rotation,
                "flip_h": self.flip_h,
                "flip_v": self.flip_v,
                "denoise": self.denoise,
                "sharpen": self.sharpen,
                "stabilize": self.stabilize,
                "effect_file": self.effect_file,
                "source_hdr": self.source_hdr,
                "crop_aspect": self.crop_aspect,
                "output_mode": self.output_mode,
            }
        )
        self.app_state.file_metadata[self.current_video_path] = metadata

    def set_video(self, file_path: str):
        if not file_path or not os.path.exists(file_path):
            return False
        if self.loading_video and self.requested_video_path == file_path:
            # A second activation of the row that is already loading.
            return True
        if not self.loading_video and self.current_video_path == file_path:
            self._load_file_metadata(file_path)
            self.update_nr_button_visibility()
            return True
        # current_video_path names the video whose edits the controls hold.
        # The loader sets it once this video's metadata is in the controls;
        # until then no save may write the previous video's edits into it.
        self.current_video_path = None
        self.loading_video = True
        self.requested_video_path = file_path
        self.cleanup_called = False
        # Reconnect signal handlers if they were disconnected during cleanup
        self.ui.reconnect_handlers()
        # Refresh NR button visibility based on current sidebar state
        self.update_nr_button_visibility()
        self.ui.info_dimensions_label.set_text("...")
        self.ui.info_codec_label.set_text("...")
        self.ui.info_filesize_label.set_text("...")
        self.ui.info_duration_label.set_text("...")
        self.update_audio_subtitle_controls()
        self.ui.position_scale.set_value(0)
        self.update_position_display(0)
        return self.processor.load_video(file_path)

    def get_page(self):
        return self.page

    def cleanup(self) -> None:
        """Clean up resources when leaving the edit page"""
        if self.cleanup_called:
            return
        self._save_file_metadata()
        self._exit_video_fullscreen()
        self.cleanup_called = True
        self.requested_video_path = None
        self.loading_video = False
        self.processor.invalidate()
        logger.debug("VideoEditPage: Starting cleanup")

        # Exit crop edit mode if active
        if self.crop_edit_mode:
            self.crop_edit_mode = False
            self.ui.crop_overlay.set_visible(False)
            self.ui.crop_edit_btn.set_active(False)
            self.ui.video_overlay.set_margin_start(0)
            self.ui.video_overlay.set_margin_end(0)
            self.ui.video_overlay.set_margin_top(0)
            self.ui.video_overlay.set_margin_bottom(0)

        if self.position_update_id:
            GLib.source_remove(self.position_update_id)
            self.position_update_id = None
        self._cancel_segment_jump()

        if self._seek_cooldown_timer_id is not None:
            GLib.source_remove(self._seek_cooldown_timer_id)
            self._seek_cooldown_timer_id = None
        self._seek_cooldown = False

        # A half-finished mark is transient interaction state, not a saved edit.
        self.first_segment_point = None
        if hasattr(self, "ui") and self.ui:
            self.ui.mark_time_label.set_visible(False)
            self.ui.mark_cancel_button.set_visible(False)

        self.is_playing = False
        if hasattr(self, "ui") and self.ui:
            self.ui.play_pause_button.set_icon_name("media-playback-start-symbolic")

        # Explicitly cleanup MPV player (non-blocking)
        if self.mpv_player:
            logger.debug("VideoEditPage: Cleaning up MPV player")
            # Just call cleanup once - it handles everything
            self.mpv_player.cleanup()

        # Disconnect tracked signal handlers
        if hasattr(self, "ui") and self.ui:
            self.ui.disconnect_all_handlers()

        self.current_video_path = None
        # A pause analysis of the video just left is not wanted any more.
        self._sync_pauses()

        logger.debug("VideoEditPage: Cleanup complete")

    def format_time_precise(self, seconds):
        # Rounded to the nearest millisecond as a whole: 2.92 is stored as
        # 2.9199…, which truncation showed as 2.919.
        total, milliseconds = divmod(round((seconds or 0) * 1000), 1000)
        hours, total = divmod(total, 3600)
        minutes, seconds_remainder = divmod(total, 60)
        return f"{hours}:{minutes:02d}:{seconds_remainder:02d}.{milliseconds:03d}"

    def update_position_display(self, position) -> None:
        if self.video_duration > 0:
            time_str = self.format_time_precise(position)
            duration_str = self.format_time_precise(self.video_duration)
            self.ui.position_label.set_text(f"{time_str} / {duration_str}")

    def update_frame_counter(self, position) -> None:
        if self.video_duration > 0 and self.video_fps > 0:
            self.ui.frame_label.set_text(
                _("Frame: {current} / {total}").format(
                    current=int(position * self.video_fps),
                    total=int(self.video_duration * self.video_fps),
                )
            )

    def _refresh_preview(self):
        if self.mpv_player:
            self.mpv_player.seek(self.current_position)

    def on_position_changed(self, scale) -> None:
        position = scale.get_value()
        if abs(position - self.current_position) < 0.001:
            return
        self.current_position = position
        if self.mpv_player:
            self.mpv_player.seek(position)
            # Prevent position polling from overriding this seek
            self._seek_cooldown = True
            if self._seek_cooldown_timer_id is not None:
                GLib.source_remove(self._seek_cooldown_timer_id)
            self._seek_cooldown_timer_id = GLib.timeout_add(
                500, self._end_seek_cooldown
            )
        self.update_position_display(position)
        self.update_frame_counter(position)

    def _end_seek_cooldown(self) -> bool:
        self._seek_cooldown = False
        self._seek_cooldown_timer_id = None
        return False

    def seek_relative(self, offset) -> None:
        new_position = self.current_position + offset
        new_position = max(0, min(new_position, self.video_duration))
        self.ui.position_scale.set_value(new_position)

    def on_play_pause_clicked(self, button) -> None:
        if not self.mpv_player or self.loading_video:
            return
        if self.is_playing:
            self.mpv_player.pause()
            self.is_playing = False
            self.ui.play_pause_button.set_icon_name("media-playback-start-symbolic")
            if self.position_update_id:
                GLib.source_remove(self.position_update_id)
                self.position_update_id = None
            self._cancel_segment_jump()
        else:
            if self.mpv_player.at_end():
                self.ui.position_scale.set_value(0)
            if self.segments_only:
                end, later = self.next_segment_stop(
                    self.trim_segments, self.current_position
                )
                if end is None:
                    # Outside the segments: start at the next one, or the first.
                    first = min((s["start"] for s in self.trim_segments), default=0)
                    self.ui.position_scale.set_value(first if later is None else later)
            self.mpv_player.play()
            self.is_playing = True
            self.ui.play_pause_button.set_icon_name("media-playback-pause-symbolic")
            if not self.position_update_id:
                self.position_update_id = GLib.timeout_add(
                    100, self._update_position_callback
                )

    def _update_position_callback(self):
        if not self.is_playing or not self.mpv_player:
            self.position_update_id = None
            return False
        if self.user_is_dragging_slider or self._seek_cooldown:
            return True
        if self.mpv_player.at_end():
            # mpv holds the last frame; nothing moves until the next play.
            self.mpv_player.pause()
            self.is_playing = False
            self.ui.play_pause_button.set_icon_name("media-playback-start-symbolic")
            self.position_update_id = None
            return False
        pos = self.mpv_player.get_position()
        if pos is None:
            return True
        self.current_position = pos
        if self.position_changed_handler_id:
            self.ui.position_scale.handler_block(self.position_changed_handler_id)
        self.ui.position_scale.set_value(pos)
        if self.position_changed_handler_id:
            self.ui.position_scale.handler_unblock(self.position_changed_handler_id)
        self.update_position_display(pos)
        self.update_frame_counter(pos)
        if self.segments_only:
            self._follow_segments(pos)
        return True

    @staticmethod
    def next_segment_stop(segments, position):
        """Where playing only the segments goes from position.

        Returns the end of the segment being played, or else the start of the
        next one; neither once past the last segment.
        """
        later = None
        for segment in sorted(segments, key=lambda s: s["start"]):
            if segment["start"] <= position < segment["end"]:
                return segment["end"], None
            if segment["start"] > position and later is None:
                later = segment["start"]
        return None, later

    def on_segments_only_toggled(self, button) -> None:
        self.segments_only = button.get_active()
        self._cancel_segment_jump()
        if self.segments_only and self.is_playing:
            self._follow_segments(self.current_position)

    def _follow_segments(self, position):
        """Jump over what the segments leave out; stop after the last one."""
        end, later = self.next_segment_stop(self.trim_segments, position)
        if end is None:
            if later is None:
                self.on_play_pause_clicked(None)
            else:
                self.ui.position_scale.set_value(later)
            return
        # Position polling every 100 ms would let up to 100 ms of the left-out
        # part play: the last stretch of a segment is timed exactly.
        remaining = (end - position) / max(self.playback_speed, 0.1)
        if remaining < 0.15 and self._segment_jump_id is None:
            self._segment_jump_id = GLib.timeout_add(
                max(1, round(remaining * 1000)), self._jump_past_segment, end
            )

    def _jump_past_segment(self, end):
        self._segment_jump_id = None
        if self.segments_only and self.is_playing:
            inside, later = self.next_segment_stop(self.trim_segments, end)
            if inside is None:
                if later is None:
                    self.on_play_pause_clicked(None)
                else:
                    self.ui.position_scale.set_value(later)
        return GLib.SOURCE_REMOVE

    def _cancel_segment_jump(self):
        if self._segment_jump_id is not None:
            GLib.source_remove(self._segment_jump_id)
            self._segment_jump_id = None

    def on_volume_changed(self, scale) -> None:
        volume = scale.get_value()
        if self.mpv_player:
            self.mpv_player.set_volume(volume)
            if hasattr(self.ui, "volume_button"):
                if volume == 0:
                    self.ui.volume_button.set_icon_name("audio-volume-muted-symbolic")
                elif volume < 0.33:
                    self.ui.volume_button.set_icon_name("audio-volume-low-symbolic")
                elif volume < 0.66:
                    self.ui.volume_button.set_icon_name("audio-volume-medium-symbolic")
                else:
                    self.ui.volume_button.set_icon_name("audio-volume-high-symbolic")

    def on_speed_changed(self, speed: float) -> None:
        self.playback_speed = speed
        if self.mpv_player:
            self.mpv_player.set_speed(speed)

    # Noise Reduction Preview

    def update_nr_button_visibility(self) -> None:
        """Show/hide the NR sidebar group and auto-toggle audio filter preview.

        The group is visible when the audio handling is set to re-encode,
        giving the user access to audio cleaning configuration from the editor.
        Audio filters apply whenever one is on and the audio is re-encoded;
        noise reduction is one of them.
        """
        if not hasattr(self.ui, "nr_sidebar_group"):
            return
        audio_reencode = self.app.audio_handling_combo.get_selected() == 1
        self.ui.nr_sidebar_group.set_visible(audio_reencode)
        # Auto-apply or remove audio filters
        if audio_reencode:
            self._apply_audio_filters()
        else:
            if self.mpv_player:
                self.mpv_player.set_audio_filter("")

    def _apply_audio_filters(self) -> None:
        """Rebuilds the filters 150 ms after the last change.

        Prevents rapid-fire micro-seeks that can break the mpv audio pipeline.
        """
        if self._audio_filter_timer:
            GLib.source_remove(self._audio_filter_timer)
        self._audio_filter_timer = GLib.timeout_add(150, self._do_apply_audio_filters)

    def _do_apply_audio_filters(self) -> bool:
        """Apply the export's audio chain, in the converter script's order, to mpv."""
        self._audio_filter_timer = None
        if not (self.mpv_player):
            return False

        sm = self.app.settings_store
        filters = []

        if sm.get_boolean("hpf-enabled", False):
            freq = sm.load_setting("hpf-frequency", 80)
            filters.append(f"highpass=f={freq}:poles=2")

        if sm.get_boolean("compressor-enabled", False):
            import math

            intensity = float(sm.load_setting("compressor-intensity", 1.0))
            threshold_db = -20 - intensity * 20
            ratio = 3 + intensity * 7
            makeup_db = 6 + intensity * 12
            knee_db = 12 + intensity * 4
            threshold_lin = math.exp(threshold_db / 20 * math.log(10))
            makeup_lin = math.exp(makeup_db / 20 * math.log(10))
            knee_lin = math.exp(knee_db / 20 * math.log(10))
            filters.append(
                f"acompressor=threshold={threshold_lin:.6f}:ratio={ratio:.6f}"
                f":attack=150:release=800:makeup={makeup_lin:.6f}"
                f":knee={knee_lin:.6f}:detection=rms"
            )

        if sm.get_boolean("normalize-enabled", False):
            filters.append("speechnorm=e=12.5:r=0.0001:l=1")

        # Noise reduction only when its plugin is installed.
        model = sm.load_setting("noise-model", 0)
        if sm.get_boolean("noise-reduction", False) and os.path.exists(
            NOISE_MODELS[model][0]
        ):
            filters.append(
                noise_reduction_filter(
                    model,
                    sm.load_setting("noise-reduction-strength", 1.0),
                    sm.load_setting("voice-only-sensitivity", 3)
                    if sm.get_boolean("voice-only", False)
                    else None,
                )
            )

        if sm.get_boolean("noise-gate-enabled", False):
            import math

            intensity = float(sm.load_setting("noise-gate-intensity", 0.5))
            threshold_db = -50 + math.sqrt(intensity) * 35
            range_db = -40 - math.sqrt(intensity) * 50
            threshold_lin = math.exp(threshold_db / 20 * math.log(10))
            range_lin = math.exp(range_db / 20 * math.log(10))
            filters.append(
                f"agate=threshold={threshold_lin:.6f}:range={range_lin:.6f}"
                f":attack=10:release=250:ratio=4:detection=rms"
            )

        if sm.get_boolean("eq-enabled", False):
            bands_str = sm.load_setting("eq-bands", "0,0,0,0,0,0,0,0,0,0")
            bands = [float(b) for b in str(bands_str).split(",")]
            freqs = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
            for i, freq in enumerate(freqs):
                gain = bands[i] if i < len(bands) else 0.0
                if gain != 0.0:
                    filters.append(f"equalizer=f={freq}:width_type=o:w=1.5:g={gain}")

        if filters:
            # Each filter in its own lavfi, quoted by byte length: mpv would
            # split a bare graph at commas, and [] quoting ends at the noise
            # chain's first pad label.
            lavfi_filter = ",".join(
                f"lavfi=graph=%{len(f.encode())}%{f}" for f in filters
            )
            self.mpv_player.set_audio_filter(lavfi_filter)
        else:
            self.mpv_player.set_audio_filter("")
        return False

    def on_audio_track_changed(self, track_index: int) -> None:
        if self.mpv_player:
            self.mpv_player.set_audio_track(track_index)
            self._sync_pauses()
            audio_tracks = self.mpv_player.get_audio_tracks()
            for track in audio_tracks:
                action = self.app.window.lookup_action(f"audio-track-{track['index']}")
                if action:
                    action.set_state(
                        GLib.Variant.new_boolean(track["index"] == track_index)
                    )

    def on_subtitle_track_changed(self, track_index: int) -> None:
        if self.mpv_player:
            self.mpv_player.set_subtitle_track(track_index)
            disabled_action = self.app.window.lookup_action("subtitle-track-disabled")
            if disabled_action:
                disabled_action.set_state(GLib.Variant.new_boolean(track_index == -1))
            subtitle_tracks = self.mpv_player.get_subtitle_tracks()
            for track in subtitle_tracks:
                action = self.app.window.lookup_action(
                    f"subtitle-track-{track['index']}"
                )
                if action:
                    action.set_state(
                        GLib.Variant.new_boolean(track["index"] == track_index)
                    )

    def update_audio_subtitle_controls(self) -> None:
        """Show the player's current tracks.

        A newly loaded file has none until mpv's file-loaded event, which
        calls this again through MPVPlayer.on_tracks_changed.
        """
        if not self.mpv_player:
            return
        player = self.mpv_player
        self.ui.audio_track_menu.remove_all()
        self.ui.subtitle_menu.remove_all()
        audio_tracks = player.get_audio_tracks()
        subtitle_tracks = player.get_subtitle_tracks()
        if len(audio_tracks) > 1:
            for track in audio_tracks:
                name = self._track_action(
                    f"audio-track-{track['index']}",
                    track["index"] == player.current_audio_track,
                    lambda a, p, idx=track["index"]: self.on_audio_track_changed(idx),
                )
                self.ui.audio_track_menu.append(track["label"], f"win.{name}")
        self.ui.audio_track_button.set_visible(len(audio_tracks) > 1)
        self._sync_pauses()
        if subtitle_tracks:
            name = self._track_action(
                "subtitle-track-disabled",
                player.current_subtitle_track == -1,
                lambda a, p: self.on_subtitle_track_changed(-1),
            )
            self.ui.subtitle_menu.append(_("Disabled"), f"win.{name}")
            for track in subtitle_tracks:
                name = self._track_action(
                    f"subtitle-track-{track['index']}",
                    track["index"] == player.current_subtitle_track,
                    lambda a, p, idx=track["index"]: self.on_subtitle_track_changed(
                        idx
                    ),
                )
                self.ui.subtitle_menu.append(track["label"], f"win.{name}")
        self.ui.subtitle_button.set_visible(bool(subtitle_tracks))

    def _track_action(self, name, active, on_activate):
        """The window action for one track, its state set for this video."""
        action = self.app.window.lookup_action(name)
        if action is None:
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(active)
            )
            action.connect("activate", on_activate)
            self.app.window.add_action(action)
        else:
            action.set_state(GLib.Variant.new_boolean(active))
        return name

    def on_toggle_fullscreen(self, button) -> None:
        """Toggle video-only fullscreen."""
        if self.is_video_fullscreen:
            self._exit_video_fullscreen()
        else:
            self._enter_video_fullscreen()

    def _on_fullscreen_changed(self):
        """Update fullscreen button icon based on current state"""
        if self.is_video_fullscreen:
            self.ui.fullscreen_button.set_icon_name("view-restore-symbolic")
        else:
            self.ui.fullscreen_button.set_icon_name("view-fullscreen-symbolic")

    def _enter_video_fullscreen(self):
        """Video-only fullscreen: chrome and margins go, the tools stay one toggle away."""
        if self.is_video_fullscreen:
            return
        self.is_video_fullscreen = True
        self.ui.toolbar.set_visible(False)
        self.app.right_toolbar_view.set_reveal_top_bars(False)
        self._sidebar_shown_before_fullscreen = self.app.split_view.get_show_sidebar()
        self.app.split_view.set_show_sidebar(False)
        self.ui.sidebar_button.set_visible(True)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.page, f"set_margin_{side}")(0)
        self.app.window.fullscreen()
        self._on_fullscreen_changed()
        self.ui.overlay_controls.set_visible(True)

    def _exit_video_fullscreen(self):
        """Leave fullscreen and restore what entering it changed."""
        if not self.is_video_fullscreen:
            return
        self.is_video_fullscreen = False
        self.app.window.unfullscreen()
        self.app.right_toolbar_view.set_reveal_top_bars(True)
        self.app.split_view.set_show_sidebar(self._sidebar_shown_before_fullscreen)
        self.ui.sidebar_button.set_visible(False)
        self.page.set_margin_start(18)
        self.page.set_margin_end(18)
        self.page.set_margin_top(14)
        self.page.set_margin_bottom(14)
        self.ui.toolbar.set_visible(True)
        self._on_fullscreen_changed()

    def _on_window_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape and self.is_video_fullscreen:
            self._exit_video_fullscreen()
            return True
        return False

    def _on_window_fullscreened(self, window, _pspec):
        if not window.is_fullscreen():
            self._exit_video_fullscreen()
