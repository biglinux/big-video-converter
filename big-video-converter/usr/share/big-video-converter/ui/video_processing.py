import os
import subprocess
import json
import gi

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
        """Load video metadata and extract the first frame"""
        if not file_path or not os.path.exists(file_path):
            print(f"Cannot load video - invalid path: {file_path}")
            return False

        # Update the UI with the file path - set this early to prevent race conditions
        self.page.current_video_path = file_path

        # Get video duration and dimensions using FFmpeg
        try:
            # Run FFprobe to get video metadata
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

            # Find the video stream
            video_stream = None
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_stream = stream
                    break

            if not video_stream:
                print("Error: No video stream found")
                return False

            # Get video dimensions
            self.page.video_width = int(video_stream.get("width", 0))
            self.page.video_height = int(video_stream.get("height", 0))

            # Get video duration (in seconds)
            duration_str = video_stream.get("duration") or info.get("format", {}).get(
                "duration"
            )
            if duration_str:
                self.page.video_duration = float(duration_str)
            else:
                # If duration not available, estimate it from bitrate and filesize
                format_info = info.get("format", {})
                if "size" in format_info and "bit_rate" in format_info:
                    size_bytes = float(format_info["size"])
                    bit_rate = float(format_info["bit_rate"])
                    self.page.video_duration = (size_bytes * 8) / bit_rate

            # Update position slider range
            self.page.ui.position_scale.set_range(0, self.page.video_duration)

            # Get FPS info
            fps = video_stream.get("avg_frame_rate", "unknown").split("/")
            if len(fps) == 2 and int(fps[1]) != 0:
                fps_value = round(int(fps[0]) / int(fps[1]), 2)
                # Store fps for frame calculations
                self.page.video_fps = fps_value
            else:
                fps_value = "unknown"
                self.page.video_fps = 30  # Default to 30fps if unknown

            # Get file size and format it
            file_size_bytes = 0
            try:
                file_size_bytes = int(info.get("format", {}).get("size", 0))
            except (ValueError, TypeError):
                file_size_bytes = os.path.getsize(file_path)

            # Format file size
            if file_size_bytes < 1024:
                file_size_str = f"{file_size_bytes} B"
            elif file_size_bytes < 1024 * 1024:
                file_size_str = f"{file_size_bytes / 1024:.2f} KB"
            elif file_size_bytes < 1024 * 1024 * 1024:
                file_size_str = f"{file_size_bytes / (1024 * 1024):.2f} MB"
            else:
                file_size_str = f"{file_size_bytes / (1024 * 1024 * 1024):.2f} GB"

            # Format duration in a more readable way
            hours = int(self.page.video_duration // 3600)
            minutes = int((self.page.video_duration % 3600) // 60)
            seconds = int(self.page.video_duration % 60)

            if hours > 0:
                duration_str = f"{hours}h {minutes}m {seconds}s"
            else:
                duration_str = f"{minutes}m {seconds}s"

            # Update all info labels
            filename = os.path.basename(file_path)
            self.page.ui.info_filename_label.set_text(filename)
            self.page.ui.info_dimensions_label.set_text(
                f"{self.page.video_width}×{self.page.video_height}"
            )
            self.page.ui.info_codec_label.set_text(
                video_stream.get("codec_name", "unknown")
            )

            # Get and display format_long_name
            format_info = info.get("format", {})
            format_long_name = format_info.get("format_long_name", "Unknown format")
            self.page.ui.info_format_label.set_text(format_long_name)

            self.page.ui.info_filesize_label.set_text(file_size_str)
            self.page.ui.info_duration_label.set_text(duration_str)
            self.page.ui.info_fps_label.set_text(f"{fps_value} fps")

            # Set current position to middle of video for better initial preview
            # (first frame is often black or blank)
            self.page.current_position = self.page.video_duration / 2

            # Update slider to middle position
            self.page.ui.position_scale.set_value(self.page.current_position)

            # Extract a frame from the middle of the video
            self.extract_frame(self.page.current_position)

            return True

        except Exception as e:
            print(f"Error getting video info: {e}")
            import traceback

            traceback.print_exc()
            # Clear current_video_path on failure
            self.page.current_video_path = None
            self.page.loading_video = False  # Ensure loading flag is reset on error
            return False

    def extract_frame(self, position):
        """Extract a frame at the specified position using FFmpeg directly to memory"""
        try:
            # Validate position is within valid range
            safe_end = max(0, self.page.video_duration - 0.1)
            if position >= safe_end:
                position = safe_end
                # Update current_position and slider without triggering events
                self.page.current_position = position
                if hasattr(self.page.ui, "position_scale") and hasattr(
                    self.page, "position_changed_handler_id"
                ):
                    self.page.ui.position_scale.handler_block(
                        self.page.position_changed_handler_id
                    )
                    self.page.ui.position_scale.set_value(position)
                    self.page.ui.position_scale.handler_unblock(
                        self.page.position_changed_handler_id
                    )

            # Build filter string for FFmpeg
            filters = []

            # Add crop filter if needed
            if (
                self.page.crop_left > 0
                or self.page.crop_right > 0
                or self.page.crop_top > 0
                or self.page.crop_bottom > 0
            ):
                crop_width = (
                    self.page.video_width - self.page.crop_left - self.page.crop_right
                )
                crop_height = (
                    self.page.video_height - self.page.crop_top - self.page.crop_bottom
                )
                filters.append(
                    f"crop={crop_width}:{crop_height}:{self.page.crop_left}:{self.page.crop_top}"
                )

            # Add hue adjustment
            if self.page.hue != 0.0:
                hue_degrees = self.page.hue * 180 / 3.14159
                filters.append(f"hue=h={hue_degrees}")

            # Add color adjustments
            eq_parts = []
            if self.page.brightness != 0:
                eq_parts.append(f"brightness={self.page.brightness}")
            if self.page.contrast != 1.0:
                contrast_delta = self.page.contrast - 1.0
                ff_contrast = 1.0 + (contrast_delta * 2.0)
                eq_parts.append(f"contrast={ff_contrast}")
            if self.page.saturation != 1.0:
                eq_parts.append(f"saturation={self.page.saturation}")
            if self.page.gamma != 1.0:
                eq_parts.append(f"gamma={self.page.gamma}")
            if self.page.gamma_r != 1.0:
                eq_parts.append(f"gamma_r={self.page.gamma_r}")
            if self.page.gamma_g != 1.0:
                eq_parts.append(f"gamma_g={self.page.gamma_g}")
            if self.page.gamma_b != 1.0:
                eq_parts.append(f"gamma_b={self.page.gamma_b}")
            if self.page.gamma_weight != 1.0:
                eq_parts.append(f"gamma_weight={self.page.gamma_weight}")

            if eq_parts:
                filters.append("eq=" + ":".join(eq_parts))

            filter_arg = ",".join(filters) if filters else "null"

            # Optimized FFmpeg command - using MJPEG which is faster to encode/decode than PNG
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",  # Reduce log output for performance
                "-ss",
                str(position),
                "-i",
                self.page.current_video_path,
                "-vf",
                filter_arg,
                "-vframes",
                "1",
                "-c:v",
                "mjpeg",  # Use MJPEG instead of PNG - much faster
                "-q:v",
                "3",  # Quality setting (1-31, lower is better)
                "-f",
                "image2pipe",
                "-",
            ]

            # Execute FFmpeg directly and capture output
            process = subprocess.run(cmd, capture_output=True, check=False)

            if process.returncode != 0:
                print(
                    f"FFmpeg error: {process.stderr.decode('utf-8', errors='replace')}"
                )
                return False

            # Create a memory input stream directly from the stdout bytes
            if process.stdout:
                # Convert the byte data directly to a memory stream
                input_stream = Gio.MemoryInputStream.new_from_bytes(
                    GLib.Bytes.new(process.stdout)
                )

                # Create a pixbuf from the stream first
                pixbuf = GdkPixbuf.Pixbuf.new_from_stream(input_stream, None)

                # Then create a texture from the pixbuf
                texture = Gdk.Texture.new_for_pixbuf(pixbuf)

                # Set the image in the UI
                self.page.ui.preview_image.set_paintable(texture)

                # Update position tracking
                self.page.current_position = position
                self.page.update_position_display(position)
                self.page.update_frame_counter(position)

                return True
            else:
                print("Error: No image data received from ffmpeg")
                return False

        except Exception as e:
            print(f"Error extracting frame: {e}")
            import traceback

            traceback.print_exc()
            return False
