import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
# Setup translation
import gettext

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

_ = gettext.gettext

import logging

from constants import NOISE_MODELS, noise_reduction_filter
from utils.crop_geometry import ASPECT_RATIOS, centered_crop
from utils.job_options import EFFECT_LEVELS, SOURCE_HDR_MODES
from utils.video_settings import SHADER_SUFFIXES, effects_graph

# Import the modules we've split off
from ui.crop_overlay import display_to_source, displayed_size, source_to_display
from ui.mpv_player import MPVPlayer
from ui.video_edit_ui import VideoEditUI
from ui.video_processing import VideoProcessor

logger = logging.getLogger(__name__)


class VideoEditPage:
    def __init__(self, app, app_state):
        self.app = app
        self.app_state = app_state
        self.settings = app.settings_manager
        self.current_video_path = None
        self.video_duration = 0
        self.current_position = 0
        self.trim_segments = []
        self.first_segment_point = None  # For the new single-button marking logic
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
        # Load default output mode from settings (last used by user)
        self.output_mode = self.settings.get_value("multi-segment-output-mode", "join")
        self.processor = VideoProcessor(self)
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
            "show-sidebar", self.ui.sidebar_button, "active",
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

    def __del__(self):
        try:
            self.cleanup()
        except AttributeError:
            pass

    def _load_file_metadata(self, file_path):
        if not hasattr(self.app, "conversion_page"):
            return
        metadata = self.app_state.file_metadata.get(file_path)
        if not metadata:
            # Load the default output mode from settings (last used by user)
            default_output_mode = self.settings.get_value(
                "multi-segment-output-mode", "join"
            )
            self.app_state.file_metadata[file_path] = {
                "trim_segments": [],
                "crop_left": 0,
                "crop_right": 0,
                "crop_top": 0,
                "crop_bottom": 0,
                "brightness": 0.0,
                "contrast": 0.0,
                "saturation": 1.0,
                "hue": 0.0,
                "rotation": 0,
                "flip_h": False,
                "flip_v": False,
                "crop_aspect": "free",
                "output_mode": default_output_mode,
            }
            metadata = self.app_state.file_metadata[file_path]
        # Load the default output mode from settings for fallback
        default_output_mode = self.settings.get_value(
            "multi-segment-output-mode", "join"
        )
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

    def _update_ui_from_metadata(self):
        self._restoring_metadata = True
        self._updating_crop_aspect = True
        self._updating_crop_spins = True
        try:
            # Update UI sliders to reflect current video's values
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
                    self.denoise, self.sharpen, self.stabilize, self.effect_file)
            if hasattr(self.ui, "source_hdr_combo"):
                self.ui.update_source_hdr(self.source_hdr)

            # Update output mode combo
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

            # Update crop spinbuttons
            self.update_crop_spinbuttons()

            # Apply values to MPV player
            if hasattr(self, "mpv_player") and self.mpv_player:
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

            # Update flip button state
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
        if getattr(self, "_restoring_metadata", False):
            return
        if not self.current_video_path or not hasattr(self.app, "conversion_page"):
            return
        metadata = self.app_state.file_metadata.get(self.current_video_path, {})
        metadata.update({
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
        })
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
        # Reset cleanup flag when loading a new video
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
        if getattr(self, "cleanup_called", False):
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

        # Stop position updates
        if hasattr(self, "position_update_id") and self.position_update_id:
            logger.debug("VideoEditPage: Removing position update timer")
            GLib.source_remove(self.position_update_id)
            self.position_update_id = None

        if self._seek_cooldown_timer_id is not None:
            GLib.source_remove(self._seek_cooldown_timer_id)
            self._seek_cooldown_timer_id = None
        self._seek_cooldown = False

        # A half-finished mark is transient interaction state, not a saved edit.
        self.first_segment_point = None
        if hasattr(self, "ui") and self.ui:
            self.ui.mark_time_label.set_visible(False)
            self.ui.mark_cancel_button.set_visible(False)

        # Update UI immediately before stopping playback
        self.is_playing = False
        if hasattr(self, "ui") and self.ui:
            self.ui.play_pause_button.set_icon_name("media-playback-start-symbolic")

        # Explicitly cleanup MPV player (non-blocking)
        if hasattr(self, "mpv_player") and self.mpv_player:
            logger.debug("VideoEditPage: Cleaning up MPV player")
            # Just call cleanup once - it handles everything
            self.mpv_player.cleanup()

        # Disconnect tracked signal handlers
        if hasattr(self, "ui") and self.ui:
            self.ui.disconnect_all_handlers()

        # Clear video path
        if hasattr(self, "current_video_path"):
            self.current_video_path = None

        logger.debug("VideoEditPage: Cleanup complete")

    def on_brightness_changed(self, scale) -> None:
        self.brightness = scale.get_value()
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)

    def on_contrast_changed(self, scale) -> None:
        self.contrast = scale.get_value()
        self._save_file_metadata()
        if getattr(self, "mpv_player", None):
            self.mpv_player.set_contrast(self.contrast)

    def on_crop_value_changed(self, spinbutton) -> None:
        """Handle crop value changes - ensures preview updates immediately"""
        if getattr(self, "_updating_crop_spins", False):
            return
        if not hasattr(self, "mpv_player") or not hasattr(self.ui, "crop_left_spin"):
            return

        # The spins name the edges as the preview shows them.
        left, right, top, bottom = display_to_source(
            (
                int(self.ui.crop_left_spin.get_value()),
                int(self.ui.crop_right_spin.get_value()),
                int(self.ui.crop_top_spin.get_value()),
                int(self.ui.crop_bottom_spin.get_value()),
            ),
            self.rotation, self.flip_h, self.flip_v,
        )

        self.crop_aspect = "free"
        if hasattr(self.ui, "crop_aspect_combo"):
            self.ui.crop_aspect_combo.set_selected(0)

        # Update instance variables
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom

        # Save metadata so values persist
        self._save_file_metadata()
        # The opposite edge's bound follows this one.
        self.update_crop_spinbuttons()

        # Update crop overlay if visible
        if self.crop_edit_mode:
            self.ui.crop_overlay.set_crop_values(
                int(left), int(right), int(top), int(bottom)
            )
        else:
            # Apply crop to MPV only when not in edit mode
            self.mpv_player.set_crop(left, right, top, bottom)

    def on_crop_aspect_changed(self, combo, _pspec=None) -> None:
        if getattr(self, "_updating_crop_aspect", False):
            return
        values = tuple(ASPECT_RATIOS)
        selected = combo.get_selected()
        if selected >= len(values):
            return
        self.crop_aspect = values[selected]
        if self.crop_aspect in {"free", "original"}:
            if self.crop_aspect == "original":
                self.crop_left = self.crop_right = 0
                self.crop_top = self.crop_bottom = 0
                self._apply_crop_state()
            self._save_file_metadata()
            return
        if self.video_width <= 1 or self.video_height <= 1:
            return
        margins = centered_crop(
            self.video_width,
            self.video_height,
            self.crop_aspect,
            rotation=self.rotation,
        )
        (
            self.crop_left,
            self.crop_right,
            self.crop_top,
            self.crop_bottom,
        ) = margins
        self._apply_crop_state()
        self._save_file_metadata()

    def _apply_crop_state(self) -> None:
        self._updating_crop_spins = True
        try:
            self.update_crop_spinbuttons()
        finally:
            self._updating_crop_spins = False
        if self.crop_edit_mode:
            self.ui.crop_overlay.set_crop_values(
                int(self.crop_left),
                int(self.crop_right),
                int(self.crop_top),
                int(self.crop_bottom),
            )
        elif getattr(self, "mpv_player", None):
            self.mpv_player.set_crop(
                self.crop_left,
                self.crop_right,
                self.crop_top,
                self.crop_bottom,
            )

    def _reapply_crop_aspect(self) -> None:
        if self.crop_aspect in {"free", "original"}:
            return
        if hasattr(self.ui, "crop_aspect_combo"):
            self.on_crop_aspect_changed(self.ui.crop_aspect_combo)

    def on_crop_edit_toggled(self, active: bool) -> None:
        """Toggle visual crop editor mode."""
        self.crop_edit_mode = active
        self.ui.crop_overlay.set_visible(active)

        _CROP_MARGIN = 20

        if active:
            # Hide overlay controls (seekbar, transport buttons) during crop edit
            if hasattr(self.ui, "overlay_controls"):
                self.ui.overlay_controls.set_visible(False)

            # Add margin around the video area to prevent accidental window resize
            self.ui.video_overlay.set_margin_start(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_end(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_top(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_bottom(_CROP_MARGIN)

            # Enter crop edit mode: remove crop from MPV, show overlay
            self.mpv_player.clear_crop()

            # The overlay maps crop margins in displayed pixels (display_size),
            # not mpv's decoded size, which is sideways for phone recordings,
            # and shows them turned and mirrored like the preview.
            self.ui.crop_overlay.set_video_dimensions(self.video_width, self.video_height)
            self.ui.crop_overlay.set_transform(self.rotation, self.flip_h, self.flip_v)

            # Set current crop values on overlay
            self.ui.crop_overlay.set_crop_values(
                int(self.crop_left),
                int(self.crop_right),
                int(self.crop_top),
                int(self.crop_bottom),
            )

            # Connect overlay drag callback to update spinbuttons
            self.ui.crop_overlay.set_on_crop_changed(self._on_crop_overlay_changed)
        else:
            # Remove margins
            self.ui.video_overlay.set_margin_start(0)
            self.ui.video_overlay.set_margin_end(0)
            self.ui.video_overlay.set_margin_top(0)
            self.ui.video_overlay.set_margin_bottom(0)

            # Restore overlay controls
            if hasattr(self.ui, "overlay_controls"):
                self.ui.overlay_controls.set_visible(True)

            # Exit crop edit mode: re-apply crop to MPV
            self.ui.crop_overlay.set_on_crop_changed(None)
            self.mpv_player.set_crop(
                self.crop_left,
                self.crop_right,
                self.crop_top,
                self.crop_bottom,
            )

    def _on_crop_overlay_changed(
        self, left: int, right: int, top: int, bottom: int
    ) -> None:
        """Called when user drags crop boundaries on the overlay."""
        self.crop_aspect = "free"
        if hasattr(self.ui, "crop_aspect_combo"):
            self.ui.crop_aspect_combo.set_selected(0)
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom
        self._save_file_metadata()
        self.update_crop_spinbuttons()

    def on_rotate(self, degrees: int) -> None:
        """Rotate video preview by given degrees (cumulative)."""
        self.rotation = (getattr(self, "rotation", 0) + degrees) % 360
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_rotation(self.rotation)
        self._update_transform_state()
        self._reapply_crop_aspect()

    def on_flip(self, direction: str) -> None:
        """Toggle horizontal or vertical flip."""
        if direction == "horizontal":
            self.flip_h = not getattr(self, "flip_h", False)
        else:
            self.flip_v = not getattr(self, "flip_v", False)
        self._save_file_metadata()
        self._update_transform_state()
        self._apply_video_flip()

    def on_reset_transform(self) -> None:
        """Reset rotation and flip to default."""
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_rotation(0)
            self.mpv_player.set_video_flip(False, False)
        self._update_transform_state()
        self._reapply_crop_aspect()

    def on_effect_changed(self, name: str, value) -> None:
        """Noise reduction, sharpening, stabilization or the effect file."""
        if getattr(self, "_restoring_metadata", False):
            return
        if name in ("denoise", "sharpen") and value not in EFFECT_LEVELS:
            return
        setattr(self, name, value)
        self._save_file_metadata()
        self.ui.update_effects_state(self.denoise, self.sharpen, self.stabilize, self.effect_file)
        self._apply_video_effects()

    def on_source_hdr_changed(self, mode: str) -> None:
        if getattr(self, "_restoring_metadata", False) or mode not in SOURCE_HDR_MODES:
            return
        self.source_hdr = mode
        self._save_file_metadata()
        self.ui.update_source_hdr(mode)
        self._apply_video_effects()

    def on_reset_effects(self) -> None:
        self.denoise, self.sharpen, self.stabilize, self.effect_file = "off", "off", False, ""
        self._save_file_metadata()
        self._update_ui_from_metadata()

    def _apply_video_effects(self) -> None:
        """Show the effects in the preview; stabilization needs the whole
        file analysed first, so only the conversion applies it."""
        if not getattr(self, "mpv_player", None):
            return
        shader = self.effect_file if os.path.splitext(self.effect_file)[1].lower() in SHADER_SUFFIXES else ""
        self.mpv_player.set_video_effects(
            effects_graph(self.denoise, self.sharpen, self.effect_file), shader, self.source_hdr)

    def _update_transform_state(self) -> None:
        if hasattr(self.ui, "rotation_row"):
            self.ui.update_transform_state(self.rotation, self.flip_h, self.flip_v)
        # The crop controls name the edges as the preview shows them.
        if hasattr(self.ui, "crop_overlay"):
            self.ui.crop_overlay.set_transform(self.rotation, self.flip_h, self.flip_v)
        self.update_crop_spinbuttons()

    def _apply_video_flip(self) -> None:
        """Apply flip state to MPV preview."""
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_video_flip(
                getattr(self, "flip_h", False), getattr(self, "flip_v", False)
            )

    def format_time_precise(self, seconds):
        # Rounded to the nearest millisecond as a whole: 2.92 is stored as
        # 2.9199…, which truncation showed as 2.919.
        total, milliseconds = divmod(round((seconds or 0) * 1000), 1000)
        hours, total = divmod(total, 3600)
        minutes, seconds_remainder = divmod(total, 60)
        return f"{hours}:{minutes:02d}:{seconds_remainder:02d}.{milliseconds:03d}"

    def on_mark_segment_point(self, button) -> None:
        if self.first_segment_point is None:
            # First click: store point and show feedback
            self.first_segment_point = self.current_position
            self.ui.mark_time_label.set_text(
                self.format_time_precise(self.first_segment_point)
            )
            self.ui.mark_time_label.set_visible(True)
            self.ui.mark_cancel_button.set_visible(True)
            self.ui.update_segment_markers()  # Show first mark immediately
        else:
            # Second click: create segment
            second_point = self.current_position
            start_time = min(self.first_segment_point, second_point)
            end_time = max(self.first_segment_point, second_point)
            # mpv's clock can run a little past the probed duration.
            if self.video_duration > 0:
                end_time = min(end_time, self.video_duration)

            if end_time > start_time:
                new_segment = {"start": start_time, "end": end_time}
                self.trim_segments.append(new_segment)
                self.trim_segments.sort(key=lambda s: s["start"])
                self._save_file_metadata()
                self._update_segments_listbox()
                self.ui.update_segment_markers()

            # Reset for next marking
            self.on_mark_cancel(None)

    def on_mark_cancel(self, button) -> None:
        self.first_segment_point = None
        self.ui.mark_time_label.set_visible(False)
        self.ui.mark_cancel_button.set_visible(False)
        self.ui.update_segment_markers()  # Hide first mark line

    def _update_segments_listbox(self):
        if not hasattr(self.ui, "segments_listbox"):
            return
        # remove_all() would drop the placeholder too.
        while row := self.ui.segments_listbox.get_row_at_index(0):
            self.ui.segments_listbox.remove(row)
        for i, segment in enumerate(self.trim_segments):
            # Activating the row seeks to the segment start.
            row = Adw.ActionRow(activatable=True)
            row.segment_start = segment["start"]
            row.set_title(_("Segment {num}").format(num=i + 1))
            row.set_subtitle(
                _("{start} → {end} ({duration})").format(
                    start=self.format_time_precise(segment["start"]),
                    end=self.format_time_precise(segment["end"]),
                    duration=self.format_time_precise(segment["end"] - segment["start"]),
                )
            )
            row.add_css_class("property")
            row.set_tooltip_text(_("Go to segment start"))
            for icon, label, handler, arg in (
                ("document-edit-symbolic", _("Edit segment times"), self._on_edit_segment_clicked, i),
                ("edit-delete-symbolic", _("Remove segment"), self._on_remove_segment_clicked, segment),
            ):
                button = Gtk.Button(
                    icon_name=icon, css_classes=["flat"], tooltip_text=label,
                    valign=Gtk.Align.CENTER,
                )
                button.update_property([Gtk.AccessibleProperty.LABEL], [label])
                button.connect("clicked", handler, arg)
                row.add_suffix(button)
            self.ui.segments_listbox.append(row)
        self.ui.update_segment_actions(len(self.trim_segments))
        self.ui.update_segment_markers()

    def _on_goto_segment_clicked(self, button, start_time):
        self.ui.position_scale.set_value(start_time)

    def _create_time_input_fields(self, time_seconds=0):
        """Create separate input fields for hours, minutes, seconds, centiseconds"""
        # Parse time into components
        hours = int(time_seconds // 3600)
        remaining = time_seconds % 3600
        minutes = int(remaining // 60)
        seconds = int(remaining % 60)
        centiseconds = int((time_seconds - int(time_seconds)) * 100)

        # Create container
        fields_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Hours
        fields_box.append(Gtk.Label(label="h"))
        h_spin = Gtk.SpinButton()
        h_spin.set_range(0, 99)
        h_spin.set_increments(1, 1)
        h_spin.set_value(hours)
        h_spin.set_width_chars(3)
        h_spin.set_tooltip_text(_("Hours"))
        h_spin.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Hours")]
        )
        fields_box.append(h_spin)

        # Minutes
        fields_box.append(Gtk.Label(label="m"))
        m_spin = Gtk.SpinButton()
        m_spin.set_range(0, 59)
        m_spin.set_increments(1, 1)
        m_spin.set_value(minutes)
        m_spin.set_width_chars(3)
        m_spin.set_tooltip_text(_("Minutes"))
        m_spin.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Minutes")]
        )
        fields_box.append(m_spin)

        # Seconds
        fields_box.append(Gtk.Label(label="s"))
        s_spin = Gtk.SpinButton()
        s_spin.set_range(0, 59)
        s_spin.set_increments(1, 1)
        s_spin.set_value(seconds)
        s_spin.set_width_chars(3)
        s_spin.set_tooltip_text(_("Seconds"))
        s_spin.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Seconds")]
        )
        fields_box.append(s_spin)

        # Centiseconds (hundredths)
        fields_box.append(Gtk.Label(label="cs"))
        cs_spin = Gtk.SpinButton()
        cs_spin.set_range(0, 99)
        cs_spin.set_increments(1, 10)
        cs_spin.set_value(centiseconds)
        cs_spin.set_width_chars(3)
        cs_spin.set_tooltip_text(_("Centiseconds"))
        cs_spin.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Centiseconds")]
        )
        fields_box.append(cs_spin)

        return fields_box, (h_spin, m_spin, s_spin, cs_spin)

    def _get_time_from_spinbuttons(self, spinbuttons):
        """Convert spinbutton values to total seconds"""
        h_spin, m_spin, s_spin, cs_spin = spinbuttons
        hours = h_spin.get_value()
        minutes = m_spin.get_value()
        seconds = s_spin.get_value()
        centiseconds = cs_spin.get_value()

        total_seconds = hours * 3600 + minutes * 60 + seconds + (centiseconds / 100.0)
        return total_seconds

    def _on_edit_segment_clicked(self, button, segment_index):
        """Show dialog to edit segment start and end times"""
        if segment_index >= len(self.trim_segments):
            return

        segment = self.trim_segments[segment_index]

        dialog = Adw.MessageDialog.new(
            self.app.window,
            _("Edit Segment"),
        )
        dialog.set_body(_("Edit start and end times for this segment"))

        # Create content with separate time input fields
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        # Start time fields
        start_label = Gtk.Label(label=_("Start Time:"), xalign=0)
        content_box.append(start_label)
        start_fields_box, start_spinbuttons = self._create_time_input_fields(
            segment["start"]
        )
        content_box.append(start_fields_box)

        # End time fields
        end_label = Gtk.Label(label=_("End Time:"), xalign=0)
        end_label.set_margin_top(6)
        content_box.append(end_label)
        end_fields_box, end_spinbuttons = self._create_time_input_fields(segment["end"])
        content_box.append(end_fields_box)

        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        # Connect response handler with segment index and spinbuttons
        dialog.connect(
            "response",
            self._on_edit_segment_dialog_response_spinbuttons,
            segment_index,
            start_spinbuttons,
            end_spinbuttons,
        )
        dialog.present()

    def _on_edit_segment_dialog_response_spinbuttons(
        self, dialog, response, segment_index, start_spinbuttons, end_spinbuttons
    ):
        """Handle the edit segment dialog response with spinbuttons"""
        if response == "save":
            try:
                times = self._segment_times(start_spinbuttons, end_spinbuttons)
                if times is None:
                    return

                # Update the segment
                self.trim_segments[segment_index]["start"] = times[0]
                self.trim_segments[segment_index]["end"] = times[1]
                self.trim_segments.sort(key=lambda s: s["start"])
                self._update_segments_listbox()
                self._save_file_metadata()

            except (ValueError, IndexError) as e:
                self._show_error_dialog(_("Error"), str(e))

    def _segment_times(self, start_spinbuttons, end_spinbuttons):
        """(start, end) typed into a segment dialog, the end clamped to the
        video, or None after telling the user why the times cannot be used."""
        start_time = self._get_time_from_spinbuttons(start_spinbuttons)
        end_time = self._get_time_from_spinbuttons(end_spinbuttons)
        if start_time < 0 or end_time < 0:
            self._show_error_dialog(
                _("Invalid Time"), _("Time values cannot be negative")
            )
            return None
        if self.video_duration > 0:
            # A cut past the end never reaches 100%, so the conversion
            # would not count as finished.
            if start_time >= self.video_duration:
                self._show_error_dialog(
                    _("Invalid Time Range"),
                    _("Start time must be before the end of the video ({duration})").format(
                        duration=self.format_time_precise(self.video_duration)),
                )
                return None
            end_time = min(end_time, self.video_duration)
        if start_time >= end_time:
            self._show_error_dialog(
                _("Invalid Time Range"), _("Start time must be before end time")
            )
            return None
        return start_time, end_time

    def _show_error_dialog(self, title, message):
        """Show a simple error dialog"""
        error_dialog = Adw.MessageDialog.new(
            self.app.window,
            title,
        )
        error_dialog.set_body(message)
        error_dialog.add_response("ok", _("OK"))
        error_dialog.set_default_response("ok")
        error_dialog.present()

    def _on_remove_segment_clicked(self, button, segment):
        # Equal start times do not make two selections the same segment.
        self.trim_segments = [s for s in self.trim_segments if s is not segment]
        self._save_file_metadata()
        self._update_segments_listbox()

    def _on_clear_all_segments_with_confirmation(self, button):
        """Show confirmation dialog before clearing all segments"""
        if not self.trim_segments:
            return  # Nothing to clear

        dialog = Adw.MessageDialog.new(
            self.app.window,
            _("Clear All Segments?"),
        )
        dialog.set_body(
            _("This will remove all trim segments. This action cannot be undone.")
        )

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear All"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        dialog.connect("response", self._on_clear_confirmation_response)
        dialog.present()

    def _on_clear_confirmation_response(self, dialog, response):
        """Handle the clear confirmation dialog response"""
        if response == "clear":
            self.trim_segments = []
            self._save_file_metadata()
            self._update_segments_listbox()

    def _on_add_manual_segment_clicked(self, button):
        """Show dialog to manually add a new segment"""
        dialog = Adw.MessageDialog.new(
            self.app.window,
            _("Add Segment Manually"),
        )
        dialog.set_body(_("Enter start and end times for the new segment"))

        # Create content with separate time input fields
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        # Start time fields
        start_label = Gtk.Label(label=_("Start Time:"), xalign=0)
        content_box.append(start_label)
        start_fields_box, start_spinbuttons = self._create_time_input_fields(0)
        content_box.append(start_fields_box)

        # End time fields
        end_label = Gtk.Label(label=_("End Time:"), xalign=0)
        end_label.set_margin_top(6)
        content_box.append(end_label)
        end_fields_box, end_spinbuttons = self._create_time_input_fields(0)
        content_box.append(end_fields_box)

        dialog.set_extra_child(content_box)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add Segment"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")

        # Connect response handler with spinbuttons
        dialog.connect(
            "response",
            self._on_add_manual_segment_response_spinbuttons,
            start_spinbuttons,
            end_spinbuttons,
        )
        dialog.present()

    def _on_add_manual_segment_response_spinbuttons(
        self, dialog, response, start_spinbuttons, end_spinbuttons
    ):
        """Handle the add manual segment dialog response with spinbuttons"""
        if response == "add":
            try:
                times = self._segment_times(start_spinbuttons, end_spinbuttons)
                if times is None:
                    return

                # Add the new segment
                new_segment = {"start": times[0], "end": times[1]}
                self.trim_segments.append(new_segment)
                self.trim_segments.sort(key=lambda s: s["start"])
                self._update_segments_listbox()
                self._save_file_metadata()

            except (ValueError, IndexError) as e:
                self._show_error_dialog(_("Error"), str(e))

    def update_crop_spinbuttons(self) -> None:
        """Show the crop as displayed, each edge bounded so that at least
        two pixels of the loaded video remain between it and its opposite."""
        if not hasattr(self.ui, "crop_left_spin"):
            return
        transform = (self.rotation, self.flip_h, self.flip_v)
        width, height = displayed_size(self.video_width, self.video_height, self.rotation)
        left, right, top, bottom = source_to_display(
            (self.crop_left, self.crop_right, self.crop_top, self.crop_bottom), *transform)
        previous = getattr(self, "_updating_crop_spins", False)
        self._updating_crop_spins = True
        try:
            for spin, value, extent, opposite in (
                (self.ui.crop_left_spin, left, width, right),
                (self.ui.crop_right_spin, right, width, left),
                (self.ui.crop_top_spin, top, height, bottom),
                (self.ui.crop_bottom_spin, bottom, height, top),
            ):
                # Before a video is loaded its size is unknown.
                upper = max(0, extent - 2 - opposite) if extent > 2 else 9999
                spin.set_range(0, max(upper, value))
                spin.set_value(value)
        finally:
            self._updating_crop_spins = previous

    def on_saturation_changed(self, scale) -> None:
        self.saturation = scale.get_value()
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_saturation(self.saturation)

    def on_hue_changed(self, scale) -> None:
        self.hue = scale.get_value()
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_hue(self.hue)

    def _on_output_mode_changed(self, combo, pspec):
        """Handle output mode combo box change"""
        selected = combo.get_selected()
        self.output_mode = "join" if selected == 0 else "split"
        # Save to per-video metadata
        self._save_file_metadata()
        # Save to global settings as the new default for future videos
        if not getattr(self, "_restoring_metadata", False):
            self.settings.save_setting("multi-segment-output-mode", self.output_mode)

    def reset_brightness(self) -> None:
        self.brightness = 0.0
        self.ui.brightness_scale.set_value(self.brightness)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)
            if not self.is_playing:
                self._refresh_preview()

    def reset_contrast(self) -> None:
        self.contrast = 0.0
        self.ui.contrast_scale.set_value(self.contrast)
        self._save_file_metadata()
        if getattr(self, "mpv_player", None):
            self.mpv_player.set_contrast(self.contrast)
            if not self.is_playing:
                self._refresh_preview()

    def reset_saturation(self) -> None:
        self.saturation = 1.0
        self.ui.saturation_scale.set_value(self.saturation)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_saturation(self.saturation)
            if not self.is_playing:
                self._refresh_preview()

    def reset_hue(self) -> None:
        self.hue = 0.0
        self.ui.hue_scale.set_value(self.hue)
        self._save_file_metadata()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_hue(self.hue)
            if not self.is_playing:
                self._refresh_preview()

    def update_position_display(self, position) -> None:
        if self.video_duration > 0:
            time_str = self.format_time_precise(position)
            duration_str = self.format_time_precise(self.video_duration)
            self.ui.position_label.set_text(f"{time_str} / {duration_str}")

    def update_frame_counter(self, position) -> None:
        if (
            self.video_duration > 0
            and hasattr(self, "video_fps")
            and self.video_fps > 0
        ):
            current_frame = int(position * self.video_fps)
            total_frames = int(self.video_duration * self.video_fps)
            self.ui.frame_label.set_text(
                _("Frame: {current}/{total}").format(
                    current=current_frame, total=total_frames
                )
            )

    def _refresh_preview(self):
        if (
            not hasattr(self, "mpv_player")
            or not self.mpv_player
            or not hasattr(self, "current_position")
        ):
            return
        current_pos = self.current_position
        self.mpv_player.seek(current_pos)

    def on_position_changed(self, scale) -> None:
        position = scale.get_value()
        if abs(position - self.current_position) < 0.001:
            return
        self.current_position = position
        if hasattr(self, "mpv_player") and self.mpv_player:
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
            self.ui.play_pause_button.set_icon_name('media-playback-start-symbolic')
            if self.position_update_id:
                GLib.source_remove(self.position_update_id)
                self.position_update_id = None
        else:
            self.mpv_player.play()
            self.is_playing = True
            self.ui.play_pause_button.set_icon_name('media-playback-pause-symbolic')
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
        return True

    def on_volume_changed(self, scale) -> None:
        volume = scale.get_value()
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_volume(volume)
            if hasattr(self.ui, "volume_button"):
                if volume == 0:
                    self.ui.volume_button.set_icon_name('audio-volume-muted-symbolic')
                elif volume < 0.33:
                    self.ui.volume_button.set_icon_name('audio-volume-low-symbolic')
                elif volume < 0.66:
                    self.ui.volume_button.set_icon_name('audio-volume-medium-symbolic')
                else:
                    self.ui.volume_button.set_icon_name('audio-volume-high-symbolic')

    def on_speed_changed(self, speed: float) -> None:
        """Handle playback speed change."""
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_speed(speed)

    # --- Noise Reduction Preview ---

    def update_nr_button_visibility(self) -> None:
        """Show/hide the NR sidebar group and auto-toggle audio filter preview.

        The group is visible when the audio handling is set to re-encode,
        giving the user access to audio cleaning configuration from the editor.
        Audio filters are applied whenever any filter is enabled and audio
        is set to re-encode — NR is just one optional filter in the chain.
        """
        if not hasattr(self.ui, "nr_sidebar_group"):
            return
        audio_reencode = self.app.audio_handling_combo.get_selected() == 1
        self.ui.nr_sidebar_group.set_visible(audio_reencode)
        # Auto-apply or remove audio filters
        if audio_reencode:
            self._apply_audio_filters()
        else:
            if hasattr(self, "mpv_player") and self.mpv_player:
                self.mpv_player.set_audio_filter("")

    def _apply_audio_filters(self) -> None:
        """Debounced wrapper — schedules actual filter rebuild after 150ms.

        Prevents rapid-fire micro-seeks that can break the mpv audio pipeline.
        """
        if hasattr(self, "_audio_filter_timer") and self._audio_filter_timer:
            GLib.source_remove(self._audio_filter_timer)
        self._audio_filter_timer = GLib.timeout_add(150, self._do_apply_audio_filters)

    def _do_apply_audio_filters(self) -> bool:
        """Build and apply the full audio filter chain to the mpv player.

        Chain order: HPF → Compressor → Normalize → [NR] → Gate → EQ
        Each filter works independently — NR is optional.
        """
        self._audio_filter_timer = None
        if not (hasattr(self, "mpv_player") and self.mpv_player):
            return False

        sm = self.app.settings_manager
        filters = []

        # 1. High-Pass Filter
        if sm.get_boolean("hpf-enabled", False):
            freq = sm.load_setting("hpf-frequency", 80)
            filters.append(f"highpass=f={freq}:poles=2")

        # 2. Compressor
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

        # 3. Volume Normalization (before NR for consistent input level)
        if sm.get_boolean("normalize-enabled", False):
            filters.append("speechnorm=e=12.5:r=0.0001:l=1")

        # 4. AI Noise Reduction (only if NR enabled AND its plugin exists)
        model = sm.load_setting("noise-model", 0)
        if sm.get_boolean("noise-reduction", False) and os.path.exists(
            NOISE_MODELS[model][0]
        ):
            filters.append(noise_reduction_filter(
                model, sm.load_setting("noise-reduction-strength", 1.0)
            ))

        # 5. Noise Gate (after NR — post-NR audio is mostly speech, so full-band detection is effective)
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

        # 6. Equalizer
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
        if hasattr(self, "mpv_player") and self.mpv_player:
            self.mpv_player.set_audio_track(track_index)
            audio_tracks = self.mpv_player.get_audio_tracks()
            for track in audio_tracks:
                action = self.app.window.lookup_action(f"audio-track-{track['index']}")
                if action:
                    action.set_state(
                        GLib.Variant.new_boolean(track["index"] == track_index)
                    )

    def on_subtitle_track_changed(self, track_index: int) -> None:
        if hasattr(self, "mpv_player") and self.mpv_player:
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
        if not getattr(self, "mpv_player", None):
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
                    lambda a, p, idx=track["index"]: self.on_subtitle_track_changed(idx),
                )
                self.ui.subtitle_menu.append(track["label"], f"win.{name}")
        self.ui.subtitle_button.set_visible(bool(subtitle_tracks))

    def _track_action(self, name, active, on_activate):
        """The window action for one track, its state set for this video."""
        action = self.app.window.lookup_action(name)
        if action is None:
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(active))
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
            self.ui.fullscreen_button.set_icon_name('view-restore-symbolic')
        else:
            self.ui.fullscreen_button.set_icon_name('view-fullscreen-symbolic')

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
