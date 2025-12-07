import os
import subprocess
import json
import gi
import threading

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gio, Gdk, GdkPixbuf

# Setup translation
import gettext

_ = gettext.gettext


class VideoProcessor:
    def __init__(self, page):
        self.page = page

    def load_video(self, file_path):
        """Starts the asynchronous process of loading video metadata."""
        if not file_path or not os.path.exists(file_path):
            print(f"Cannot load video - invalid path: {file_path}")
            self.page.loading_video = False
            return False

        # Update the UI with the file path immediately
        self.page.current_video_path = file_path

        # Start background thread to get info without blocking the UI
        info_thread = threading.Thread(
            target=self._get_video_info_thread, args=(file_path,)
        )
        info_thread.daemon = True
        info_thread.start()
        return True

    def _get_video_info_thread(self, file_path):
        """Background thread to run ffprobe and get video info."""
        try:
            cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            info = json.loads(result.stdout)
            # Post the successful result back to the main GTK thread
            GLib.idle_add(self._on_video_info_loaded, info, file_path)
        except Exception as e:
            error_message = f"Error getting video info: {e}"
            print(error_message)
            # Post the error back to the main GTK thread and release the lock
            GLib.idle_add(self._on_video_info_error, error_message)

    def _on_video_info_error(self, error_message):
        """Handle errors from the ffprobe thread."""
        self.page.app.show_error_dialog(error_message)
        self.page.loading_video = False

    def _on_video_info_loaded(self, info, file_path):
        """Callback executed on the main thread after ffprobe finishes."""
        # CRITICAL FIX: Check against the requested path, not the current path,
        # as current_path might be cleared by cleanup before this callback runs.
        if file_path != self.page.requested_video_path:
            print(f"Ignoring stale video info for: {os.path.basename(file_path)}")
            # If this was a stale request, we still need to ensure the loading lock isn't stuck.
            # However, only the *correct* callback should release the lock.
            # This logic is now safer because the gatekeeper in set_video is stronger.
            return

        video_stream = next(
            (s for s in info.get("streams", []) if s.get("codec_type") == "video"), None
        )
        if not video_stream:
            self.page.app.show_error_dialog("Error: No video stream found")
            self.page.loading_video = False
            return

        # --- Update all video properties ---
        self.page.video_width = int(video_stream.get("width", 0))
        self.page.video_height = int(video_stream.get("height", 0))

        duration_str = video_stream.get("duration") or info.get("format", {}).get(
            "duration"
        )
        if duration_str:
            self.page.video_duration = float(duration_str)
        else:
            self.page.video_duration = 0

        self.page.ui.position_scale.set_range(0, self.page.video_duration)

        fps_str = video_stream.get("avg_frame_rate", "0/1").split("/")
        self.page.video_fps = (
            int(fps_str[0]) / int(fps_str[1])
            if len(fps_str) == 2 and int(fps_str[1]) != 0
            else 30
        )

        # --- Update UI Labels ---
        file_size_bytes = int(info.get("format", {}).get("size", 0))
        file_size_str = f"{file_size_bytes / (1024 * 1024):.2f} MB"

        hours, rem = divmod(self.page.video_duration, 3600)
        minutes, seconds = divmod(rem, 60)
        duration_formatted = f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"

        # CORRECTION: Update only the labels that exist in the new UI
        self.page.ui.info_dimensions_label.set_text(
            f"{self.page.video_width}×{self.page.video_height}"
        )
        self.page.ui.info_codec_label.set_text(video_stream.get("codec_name", "N/A"))
        self.page.ui.info_filesize_label.set_text(file_size_str)
        self.page.ui.info_duration_label.set_text(duration_formatted)

        # --- Load into video player and finalize ---
        if hasattr(self.page, "mpv_player") and self.page.mpv_player:
            if not self.page.mpv_player.load_video(file_path):
                self.page.app.show_error_dialog(
                    "Error: Failed to load video file. Please check the file format and try again."
                )
                self.page.loading_video = False
                return

        # Load per-file editing metadata now that we have the context
        self.page._load_file_metadata(file_path)

        # Update crop displays
        self.page.update_crop_spinbuttons()

        # Set initial position and update displays
        self.page.current_position = 0
        self.page.ui.position_scale.set_value(0)
        self.page.update_position_display(0)
        self.page.update_frame_counter(0)

        # Update audio and subtitle track controls
        self.page.update_audio_subtitle_controls()

        # Start playback automatically and update UI state
        if hasattr(self.page, "mpv_player") and self.page.mpv_player:
            self.page.mpv_player.play()
            self.page.is_playing = True
            self.page.ui.play_pause_button.set_icon_name('media-playback-pause-symbolic')
            # Start position update timer
            if not self.page.position_update_id:
                self.page.position_update_id = GLib.timeout_add(
                    100, self.page._update_position_callback
                )

        # Finally, release the loading lock
        self.page.loading_video = False
        print(f"Successfully loaded video: {os.path.basename(file_path)}")