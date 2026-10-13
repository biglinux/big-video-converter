import os
import threading

import gi

gi.require_version("Gtk", "4.0")
import gettext
import logging

from engine.file_info import display_size, get_video_file_info
from gi.repository import GLib

from ui.progress_page import format_size

logger = logging.getLogger(__name__)

_ = gettext.gettext


class VideoProcessor:
    def __init__(self, page):
        self.page = page
        self._generation = 0

    def invalidate(self):
        """Invalidate outstanding workers without touching GTK from those workers."""
        self._generation += 1

    def load_video(self, file_path: str) -> bool:
        """Starts the asynchronous process of loading video metadata."""
        if not file_path or not os.path.exists(file_path):
            logger.error("Cannot load video - invalid path: %s", file_path)
            self.page.loading_video = False
            return False

        self._generation += 1
        generation = self._generation

        # Start background thread to get info without blocking the UI
        info_thread = threading.Thread(
            target=self._get_video_info_thread, args=(file_path, generation)
        )
        info_thread.daemon = True
        info_thread.start()
        return True

    def _get_video_info_thread(self, file_path, generation):
        """Background thread to read the video metadata."""
        try:
            # Shared helper: it already falls back to parsing ffmpeg output for
            # files ffprobe refuses to analyze.
            info = get_video_file_info(file_path)
            if not info:
                raise ValueError(_("The video metadata could not be read"))
            # Post the successful result back to the main GTK thread
            GLib.idle_add(self._on_video_info_loaded, info, file_path, generation)
        except Exception as e:  # any failure must release the loading lock
            logger.exception("Error getting video info for %s", file_path)
            error_message = _("Error getting video info: {error}").format(error=e)
            GLib.idle_add(self._on_video_info_error, error_message, generation)

    def _on_video_info_error(self, error_message, generation):
        """Ignore errors from an earlier editor session."""
        if generation != self._generation:
            return False
        self.page.app.show_error_dialog(error_message)
        self.page.loading_video = False
        return False

    def _on_video_info_loaded(self, info, file_path, generation):
        """Callback executed on the main thread after ffprobe finishes."""
        if (
            generation != self._generation
            or file_path != self.page.requested_video_path
        ):
            logger.debug(
                "Ignoring stale video info for: %s", os.path.basename(file_path)
            )
            return False
        # Whatever the probe answered, the editor must not stay locked in
        # "loading" with its controls inert.
        try:
            self._show_video(info, file_path)
        except Exception as error:
            logger.exception("Could not show video info for %s", file_path)
            self.page.app.show_error_dialog(
                _("Error getting video info: {error}").format(error=error)
            )
        finally:
            self.page.loading_video = False
        return False

    def _show_video(self, info, file_path):
        video_stream = next(
            (s for s in info.get("streams", []) if s.get("codec_type") == "video"), None
        )
        if not video_stream:
            self.page.app.show_error_dialog(_("Error: No video stream found"))
            return

        self.page.video_width, self.page.video_height = display_size(video_stream)

        duration_str = video_stream.get("duration") or info.get("format", {}).get(
            "duration"
        )
        try:
            self.page.video_duration = max(0.0, float(duration_str or 0))
        except ValueError:  # "N/A" from a stream without a known length
            self.page.video_duration = 0

        self.page.ui.position_scale.set_range(0, self.page.video_duration)

        fps_str = video_stream.get("avg_frame_rate", "0/1").split("/")
        self.page.video_fps = (
            int(fps_str[0]) / int(fps_str[1])
            if len(fps_str) == 2 and int(fps_str[1]) != 0
            else 30
        )

        try:
            size = int(info.get("format", {}).get("size"))
        except (TypeError, ValueError):  # "N/A": the file itself knows
            try:
                size = os.path.getsize(file_path)
            except OSError:
                size = None
        file_size_str = format_size(size) if size is not None else ""

        hours, rem = divmod(self.page.video_duration, 3600)
        minutes, seconds = divmod(rem, 60)
        duration_formatted = f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"

        self.page.ui.info_dimensions_label.set_text(
            f"{self.page.video_width}×{self.page.video_height}"
        )
        self.page.ui.info_codec_label.set_text(video_stream.get("codec_name", "N/A"))
        self.page.ui.info_filesize_label.set_text(file_size_str)
        self.page.ui.info_duration_label.set_text(duration_formatted)

        if (
            hasattr(self.page, "mpv_player")
            and self.page.mpv_player
            and not self.page.mpv_player.load_video(file_path)
        ):
            self.page.app.show_error_dialog(
                _(
                    "Error: Failed to load video file. Please check the file format and try again."
                )
            )
            return

        # From here on, edits belong to this video.
        self.page.current_video_path = file_path
        self.page._load_file_metadata(file_path)

        self.page.update_crop_spinbuttons()

        self.page.current_position = 0
        self.page.ui.position_scale.set_value(0)
        self.page.update_position_display(0)
        self.page.update_frame_counter(0)

        self.page.update_audio_subtitle_controls()

        if hasattr(self.page, "mpv_player") and self.page.mpv_player:
            self.page.mpv_player.play()
            self.page.is_playing = True
            self.page.ui.play_pause_button.set_icon_name(
                "media-playback-pause-symbolic"
            )
            if not self.page.position_update_id:
                self.page.position_update_id = GLib.timeout_add(
                    100, self.page._update_position_callback
                )

        logger.debug("Successfully loaded video: %s", os.path.basename(file_path))
