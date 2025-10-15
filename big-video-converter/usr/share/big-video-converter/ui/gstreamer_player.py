"""
GStreamer-based video player for real-time preview with filter controls.
Simplified version using only videobalance for color adjustments.
"""

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GLib

# Initialize GStreamer
try:
    Gst.init(None)
except Exception as e:
    print(f"Failed to initialize GStreamer: {e}")


class GStreamerPlayer:
    """
    Real-time video player using GStreamer with dynamic filter controls.
    Designed for preview only - final render uses external script.
    Uses Gtk.Picture widget with gtk4paintablesink for display.
    Uses videobalance element for color adjustments.
    """

    def __init__(self, video_widget):
        """
        Initialize GStreamer player

        Args:
            video_widget: Gtk.Picture widget for rendering
        """
        self.video_widget = video_widget
        self.pipeline = None

        # Filter elements
        self.videobalance = None
        self.videocrop = None
        self.volume = None

        # Audio/subtitle tracks
        self.audio_tracks = []
        self.subtitle_tracks = []
        self.current_audio_track = -1
        self.current_subtitle_track = -1
        self._detect_tracks_attempts = 0

        # State
        self.is_playing = False
        self.duration = 0
        self.current_file = None
        self.current_volume = 1.0

    def load_video(self, file_path):
        """
        Load a video file and build the GStreamer pipeline with filters and audio support.
        Uses playbin for automatic audio/video/subtitle handling with custom video filters.

        Args:
            file_path: Path to video file

        Returns:
            bool: True if successful
        """
        try:
            # Clear the current video widget to prevent showing old video
            self.video_widget.set_paintable(None)

            # Stop and clean up existing pipeline before creating a new one
            self.cleanup()

            self.current_file = file_path

            # Use playbin for automatic audio/video/subtitle stream handling
            self.pipeline = Gst.ElementFactory.make("playbin", "player")

            if not self.pipeline:
                print("Failed to create playbin element")
                return False

            # Set the video file URI
            import pathlib

            file_uri = pathlib.Path(file_path).as_uri()
            self.pipeline.set_property("uri", file_uri)

            # Create custom video filter bin for our video processing
            video_bin = Gst.Bin.new("video-filters")

            # Video processing chain with capsfilter to force raw video format
            videoconvert1 = Gst.ElementFactory.make("videoconvert", "videoconvert1")
            self.videobalance = Gst.ElementFactory.make("videobalance", "balance")
            self.videocrop = Gst.ElementFactory.make("videocrop", "crop")
            capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
            gtksink = Gst.ElementFactory.make("gtk4paintablesink", "gtksink")

            if not all([
                videoconvert1,
                self.videobalance,
                self.videocrop,
                capsfilter,
                gtksink,
            ]):
                print("Failed to create video filter elements")
                return False

            # Force raw video format after crop - this is critical for videocrop to work
            # Without this, videocrop fails with "Downstream doesn't support crop for non-raw caps"
            caps = Gst.Caps.from_string("video/x-raw")
            capsfilter.set_property("caps", caps)

            # Add elements to video bin
            video_bin.add(videoconvert1)
            video_bin.add(self.videobalance)
            video_bin.add(self.videocrop)
            video_bin.add(capsfilter)
            video_bin.add(gtksink)

            # Link video elements: videoconvert -> videobalance -> videocrop -> capsfilter -> gtksink
            videoconvert1.link(self.videobalance)
            self.videobalance.link(self.videocrop)
            self.videocrop.link(capsfilter)
            capsfilter.link(gtksink)

            # Create ghost pad for video bin input
            sink_pad = videoconvert1.get_static_pad("sink")
            ghost_pad = Gst.GhostPad.new("sink", sink_pad)
            video_bin.add_pad(ghost_pad)

            # Set video bin as video sink
            self.pipeline.set_property("video-sink", video_bin)

            # Create audio processing chain with volume control
            audio_bin = Gst.Bin.new("audio-filters")

            audioconvert = Gst.ElementFactory.make("audioconvert", "audioconvert")
            audioresample = Gst.ElementFactory.make("audioresample", "audioresample")
            self.volume = Gst.ElementFactory.make("volume", "volume")
            autoaudiosink = Gst.ElementFactory.make("autoaudiosink", "audiosink")

            if not all([audioconvert, audioresample, self.volume, autoaudiosink]):
                print(
                    "Warning: Failed to create audio elements - audio will be disabled"
                )
                # Continue without audio
            else:
                # Add elements to audio bin
                audio_bin.add(audioconvert)
                audio_bin.add(audioresample)
                audio_bin.add(self.volume)
                audio_bin.add(autoaudiosink)

                # Link audio elements
                audioconvert.link(audioresample)
                audioresample.link(self.volume)
                self.volume.link(autoaudiosink)

                # Create ghost pad for audio bin input
                audio_sink_pad = audioconvert.get_static_pad("sink")
                audio_ghost_pad = Gst.GhostPad.new("sink", audio_sink_pad)
                audio_bin.add_pad(audio_ghost_pad)

                # Set audio bin as audio sink
                self.pipeline.set_property("audio-sink", audio_bin)

            # Connect gtk4paintablesink to the Gtk.Picture widget
            paintable = gtksink.get_property("paintable")
            self.video_widget.set_paintable(paintable)

            # Enable subtitle rendering
            self.pipeline.set_property(
                "flags", self.pipeline.get_property("flags") | (1 << 2)
            )  # GST_PLAY_FLAG_TEXT (subtitles)

            # Set to paused state to load video
            self.pipeline.set_state(Gst.State.PAUSED)

            # Wait longer for pipeline to reach PAUSED state and for playbin to discover streams
            # Use CLOCK_TIME_NONE to wait indefinitely (but it will timeout if something is wrong)
            ret = self.pipeline.get_state(3 * Gst.SECOND)
            if (
                ret[0] == Gst.StateChangeReturn.SUCCESS
                or ret[0] == Gst.StateChangeReturn.ASYNC
            ):
                self._query_duration_delayed()
                self._detect_tracks()
            else:
                print(
                    f"Warning: Pipeline state change did not complete successfully: {ret[0]}"
                )

            return True

        except Exception as e:
            print(f"Error loading video in GStreamer: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _query_duration_delayed(self):
        """Query duration. Called via timeout if state change is async."""
        if not self.pipeline:
            return False

        success, duration_ns = self.pipeline.query_duration(Gst.Format.TIME)
        if success:
            self.duration = duration_ns / Gst.SECOND
            return False  # Don't repeat

        GLib.timeout_add(50, self._query_duration_delayed)
        return False

    def _detect_tracks(self):
        """Detect available audio and subtitle tracks"""
        if not self.pipeline:
            return

        # Start detection attempts with retry logic
        self._detect_tracks_attempts = 0
        print("Starting track detection with retry logic...")
        GLib.timeout_add(100, self._do_detect_tracks)

    def _do_detect_tracks(self):
        """Actually detect the tracks after streams are ready"""
        if not self.pipeline:
            return False

        # Get number of audio tracks
        n_audio = self.pipeline.get_property("n-audio")
        n_text = self.pipeline.get_property("n-text")

        print(
            f"Track detection attempt {self._detect_tracks_attempts + 1}: found {n_audio} audio tracks, {n_text} subtitle tracks"
        )

        # If no tracks detected yet and we haven't exceeded max attempts, retry
        if n_audio == 0 and n_text == 0 and self._detect_tracks_attempts < 20:
            self._detect_tracks_attempts += 1
            return True  # Retry after timeout

        # Process audio tracks
        self.audio_tracks = []
        for i in range(n_audio):
            # Get tags for this audio stream
            tags = self.pipeline.emit("get-audio-tags", i)
            lang = "Unknown"
            if tags:
                success, language = tags.get_string("language-code")
                if success:
                    lang = language
                else:
                    # Try language name
                    success, language = tags.get_string("language-name")
                    if success:
                        lang = language

            self.audio_tracks.append({
                "index": i,
                "language": lang,
                "label": f"Audio Track {i + 1} ({lang})",
            })

        # Process subtitle tracks
        self.subtitle_tracks = []
        for i in range(n_text):
            # Get tags for this subtitle stream
            tags = self.pipeline.emit("get-text-tags", i)
            lang = "Unknown"
            if tags:
                success, language = tags.get_string("language-code")
                if success:
                    lang = language
                else:
                    success, language = tags.get_string("language-name")
                    if success:
                        lang = language

            self.subtitle_tracks.append({
                "index": i,
                "language": lang,
                "label": f"Subtitle Track {i + 1} ({lang})",
            })

        # Get current selections
        self.current_audio_track = self.pipeline.get_property("current-audio")
        self.current_subtitle_track = self.pipeline.get_property("current-text")

        print(
            f"Track detection complete: Detected {n_audio} audio tracks and {n_text} subtitle tracks"
        )
        return False  # Don't repeat

    def play(self):
        """Start playback"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.is_playing = True

    def pause(self):
        """Pause playback"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PAUSED)
            self.is_playing = False

    def stop(self):
        """Stop playback immediately and wait for state change"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            # Wait for state change to complete (with short timeout)
            self.pipeline.get_state(100 * Gst.MSECOND)
            self.is_playing = False

    def seek(self, position_seconds):
        """
        Seek to a specific position with accurate frame positioning.
        Works correctly even when paused.

        Args:
            position_seconds: Position in seconds
        """
        if not self.pipeline:
            return False

        # Ensure pipeline is at least in PAUSED state for seeking to work
        current_state = self.pipeline.get_state(0)[1]
        if current_state == Gst.State.NULL or current_state == Gst.State.READY:
            self.pipeline.set_state(Gst.State.PAUSED)
            # Wait for state change to complete
            self.pipeline.get_state(Gst.CLOCK_TIME_NONE)

        position_ns = int(position_seconds * Gst.SECOND)
        # Use ACCURATE flag for precise frame-by-frame seeking instead of KEY_UNIT
        # FLUSH ensures the pipeline processes the seek immediately
        result = self.pipeline.seek_simple(
            Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, position_ns
        )

        # When paused, we need to wait for preroll to show the new frame
        if not self.is_playing and result:
            # Wait for preroll to complete (with timeout to avoid blocking)
            # This ensures the new frame is rendered before returning
            self.pipeline.get_state(100 * Gst.MSECOND)

        return result

    def get_position(self):
        """Get current playback position in seconds"""
        if not self.pipeline:
            return 0

        success, position_ns = self.pipeline.query_position(Gst.Format.TIME)
        if success:
            return position_ns / Gst.SECOND
        return 0

    def get_duration(self):
        """Get video duration in seconds"""
        return self.duration

    def set_brightness(self, value):
        """Set brightness adjustment (-1.0 to 1.0)"""
        if self.videobalance:
            self.videobalance.set_property("brightness", value)

    def set_saturation(self, value):
        """Set saturation adjustment (0.0 to 2.0)"""
        if self.videobalance:
            self.videobalance.set_property("saturation", value)

    def set_hue(self, value):
        """Set hue adjustment (-1.0 to 1.0)"""
        if self.videobalance:
            self.videobalance.set_property("hue", value)

    def set_crop(self, left, right, top, bottom):
        """
        Set crop values and trigger caps renegotiation.
        For playbin with dynamic video-sink, we need RECONFIGURE event.
        When paused, we also need to seek to refresh the display.
        """
        if self.videocrop:
            # Set crop properties
            self.videocrop.set_property("left", int(left))
            self.videocrop.set_property("right", int(right))
            self.videocrop.set_property("top", int(top))
            self.videocrop.set_property("bottom", int(bottom))

            # Send RECONFIGURE event to trigger caps renegotiation
            # This is needed because playbin's video-sink requires explicit notification
            pad = self.videocrop.get_static_pad("src")
            if pad:
                event = Gst.Event.new_reconfigure()
                pad.send_event(event)

            # When paused, seek to current position to refresh the frame with new crop
            if not self.is_playing:
                current_pos = self.get_position()
                if current_pos >= 0:
                    self.seek(current_pos)

    def set_volume(self, volume):
        """Set audio volume (0.0 to 1.0)"""
        if self.volume:
            self.volume.set_property("volume", float(volume))
            self.current_volume = volume

    def set_audio_track(self, track_index):
        """Switch to a different audio track"""
        if self.pipeline and 0 <= track_index < len(self.audio_tracks):
            self.pipeline.set_property("current-audio", track_index)
            self.current_audio_track = track_index
            print(f"Switched to audio track {track_index}")

    def set_subtitle_track(self, track_index):
        """Switch to a different subtitle track (-1 to disable)"""
        if self.pipeline:
            if track_index == -1:
                # Disable subtitles
                flags = self.pipeline.get_property("flags")
                flags = flags & ~(1 << 2)  # Remove GST_PLAY_FLAG_TEXT
                self.pipeline.set_property("flags", flags)
            else:
                # Enable subtitles and switch track
                flags = self.pipeline.get_property("flags")
                flags = flags | (1 << 2)  # Add GST_PLAY_FLAG_TEXT
                self.pipeline.set_property("flags", flags)
                if 0 <= track_index < len(self.subtitle_tracks):
                    self.pipeline.set_property("current-text", track_index)
            self.current_subtitle_track = track_index
            print(f"Switched to subtitle track {track_index}")

    def get_audio_tracks(self):
        """Get list of available audio tracks"""
        return self.audio_tracks

    def get_subtitle_tracks(self):
        """Get list of available subtitle tracks"""
        return self.subtitle_tracks

    def cleanup(self):
        """Clean up resources completely"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None

        self.is_playing = False
        self.duration = 0
        # Clear element references
        self.videobalance = None
        self.videocrop = None
        self.volume = None
        # Clear track info
        self.audio_tracks = []
        self.subtitle_tracks = []
        self.current_audio_track = -1
        self.current_subtitle_track = -1
