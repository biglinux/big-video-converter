# Import translation function
import gettext
import os
import re
import shlex
import subprocess
import time

from constants import CONVERT_SCRIPT_PATH
from gi.repository import GLib

_ = gettext.gettext  # Will use the already initialized translation


def detect_bit_depth_info(file_path):
    """Detect and log bit depth and codec information for user awareness"""
    try:
        # Get both pixel format and codec information
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=pix_fmt,codec_name",
                "-of",
                "csv=p=0",
                file_path,
            ],
            capture_output=True,
            text=True,
        )

        output = result.stdout.strip().split(",")
        if len(output) >= 2:
            pix_fmt = output[0]
            codec = output[1]
        else:
            pix_fmt = output[0] if output else ""
            codec = "unknown"

        is_10bit = "p10" in pix_fmt or "10le" in pix_fmt
        is_hevc = codec in ["hevc", "h265"]

        if is_10bit and is_hevc:
            return "ℹ️  Detected H.265 10-bit video - will use optimized GPU conversion for H.264 output"
        elif is_10bit:
            return f"ℹ️  Detected 10-bit video ({codec}) - will use appropriate profile automatically"
        elif is_hevc:
            return "ℹ️  Detected H.265 8-bit video - using standard conversion"
        else:
            return f"ℹ️  Detected 8-bit video ({codec}) - using standard profile"
    except:
        return "ℹ️  Video analysis complete"


def run_with_progress_dialog(
    app,
    cmd,
    title_suffix,
    input_file=None,
    delete_original=False,
    env_vars=None,
    wait_for_completion=False,
    is_segment_batch=False,
    segment_duration=None,
):
    """Run a conversion command and show progress on the Progress page

    Args:
        wait_for_completion: If True, blocks until conversion completes (for sequential processing)
        is_segment_batch: If True, suppresses completion dialogs for individual segments in a batch
        segment_duration: Expected duration of this segment in seconds (overrides ffmpeg-detected duration for progress calculation)
    """
    # Use app's global setting for deleting original files if not explicitly set
    if hasattr(app, "delete_original_after_conversion"):
        delete_original = app.delete_original_after_conversion

    # Initialize env_vars if None
    if env_vars is None:
        env_vars = os.environ.copy()

    # Handle output folder settings - Critical fix for path duplication
    output_folder = app.settings_manager.load_setting("output-folder", "")
    if output_folder and output_folder.strip():
        # Make sure it's absolute and normalized
        output_folder = os.path.normpath(os.path.abspath(output_folder.strip()))

        # Set in environment with no trailing slash to prevent path issues
        if output_folder.endswith(os.sep):
            output_folder = output_folder[:-1]

        env_vars["output_folder"] = output_folder
        print(f"Set output folder: {output_folder}")

    # Ensure trim environment variables are properly set
    # Print trim-related environment variables for debugging
    if "trim_start" in env_vars:
        print(f"Trim setting: trim_start={env_vars['trim_start']}")
    if "trim_end" in env_vars:
        print(f"Trim setting: trim_end={env_vars['trim_end']}")
    if "trim_duration" in env_vars:
        print(f"Trim setting: trim_duration={env_vars['trim_duration']}")

    if not title_suffix or title_suffix == "Unknown file":
        if input_file:
            title_suffix = os.path.basename(input_file)
        else:
            title_suffix = _("Video Conversion")

    cmd_str = " ".join([shlex.quote(arg) for arg in cmd])

    # Increment counter of active conversions
    app.conversions_running += 1

    # Start process
    try:
        # Print command for debugging
        print(f"Executing command: {cmd_str}")

        # Create a process with proper flags to ensure child processes are terminated
        kwargs = {}

        # Print the final environment variables for debugging
        print("Final environment variables for conversion:")
        for key in sorted([
            k
            for k in env_vars.keys()
            if k
            in [
                "gpu",
                "video_quality",
                "video_encoder",
                "preset",
                "subtitle_extract",
                "audio_handling",
                "audio_bitrate",
                "audio_channels",
                "audio_codec",
                "video_resolution",
                "options",
                "gpu_partial",
                "force_copy_video",
                "only_extract_subtitles",
                "video_filter",
                "output_folder",
                "output_file",
                "trim_start",
                "trim_end",
                "trim_duration",
            ]
        ]):
            print(f"  {key}={env_vars[key]}")

        # Use PIPE for stdout and stderr to monitor progress
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
            env=env_vars,
            **kwargs,
        )

        # Create conversion item on progress page
        # CRITICAL: GTK widget creation must happen on the main thread to avoid segfaults
        import threading

        # Check if we're already on the main thread
        main_context = GLib.MainContext.default()
        is_main_thread = main_context.is_owner()

        progress_item_container = [None]
        exception_container = [None]

        if is_main_thread:
            # We're already on the main thread, call directly
            print("Creating progress item directly (already on main thread)")
            try:
                progress_item_container[0] = app.progress_page.add_conversion(
                    title_suffix, input_file, process
                )
            except Exception as e:
                exception_container[0] = e
        else:
            # We're on a background thread, schedule on main thread and wait
            print("Scheduling progress item creation on main thread")
            creation_complete = threading.Event()

            def create_progress_item():
                try:
                    progress_item_container[0] = app.progress_page.add_conversion(
                        title_suffix, input_file, process
                    )
                except Exception as e:
                    exception_container[0] = e
                finally:
                    creation_complete.set()

            GLib.idle_add(create_progress_item)
            creation_complete.wait(timeout=10.0)  # Wait up to 5 seconds

        # Check if an exception occurred during widget creation
        if exception_container[0] is not None:
            raise Exception(
                f"Failed to create progress item on main thread: {exception_container[0]}"
            )

        if progress_item_container[0] is None:
            raise Exception(
                "Failed to create progress item on main thread: timeout or unknown error"
            )

        progress_item = progress_item_container[0]

        # Store segment duration for progress calculation
        # When processing a segment with -ss/-t, ffmpeg detects the full video duration
        # but reports progress based on the segment duration specified in -t
        # We need to use the segment duration for accurate progress calculation
        if segment_duration is not None and segment_duration > 0:
            progress_item.expected_duration = segment_duration
            print(
                f"Segment mode: expected duration={segment_duration:.2f}s (will override ffmpeg-detected duration for progress calculation)"
            )
        else:
            progress_item.expected_duration = None

        # Flag to track if this is part of a queue processing
        if input_file:
            app.current_processing_file = input_file

        # Flag to indicate it's a queue item if queue has files
        is_queue_processing = len(app.conversion_queue) > 0
        progress_item.is_queue_processing = is_queue_processing
        # Flag to indicate this is part of a segment batch (suppress dialogs for individual segments)
        progress_item.is_segment_batch = is_segment_batch
        progress_item.input_file_path = input_file  # Store the input file path

        # Also store the input file path in progress_item for later reference
        progress_item.original_input_file = input_file

        # Configure option to delete original file
        if input_file:
            progress_item.set_delete_original(delete_original)

        # Start thread to monitor progress
        monitor_thread = threading.Thread(
            target=monitor_progress, args=(app, process, progress_item, env_vars)
        )
        monitor_thread.daemon = True
        monitor_thread.start()

        # Function to handle process completion
        def on_conversion_complete(process, result):
            try:
                # Cleanup if we were asked to delete the original file after successful conversion
                if (
                    result == 0
                    and delete_original
                    and input_file
                    and os.path.exists(input_file)
                ):
                    try:
                        os.remove(input_file)
                        print(f"Deleted original file: {input_file}")
                    except Exception as del_error:
                        print(f"Error deleting file {input_file}: {del_error}")

                # Auto-remove successful segment batch items to keep UI clean
                if is_segment_batch and result == 0:
                    print(
                        f"Auto-removing successful segment batch item: {progress_item.conversion_id}"
                    )

                    # Small delay before removal to show completion briefly
                    def remove_after_delay():
                        GLib.timeout_add(
                            50,
                            lambda: app.progress_page.remove_conversion(
                                progress_item.conversion_id
                            )
                            or False,
                        )

                    GLib.idle_add(remove_after_delay)

                # Notify the application that conversion is complete (skip for segment batch)
                if not is_segment_batch:
                    GLib.idle_add(lambda: app.conversion_completed(result == 0))
                else:
                    print(
                        "Skipping conversion_completed callback for segment batch item"
                    )

            except Exception as e:
                print(f"Error in conversion completion handler: {e}")
                # Still notify app even if there's an error in the handler (skip for segment batch)
                if not is_segment_batch:
                    GLib.idle_add(lambda: app.conversion_completed(False))

        # Wait for completion if requested (for sequential processing)
        if wait_for_completion:
            print(f"Waiting for conversion to complete: {title_suffix}")
            returncode = process.wait()
            print(f"Conversion finished with return code: {returncode}")
            on_conversion_complete(process, returncode)
            # Also wait for monitor thread to finish
            monitor_thread.join(timeout=5.0)

    except Exception as e:
        app.show_error_dialog(_("Error starting conversion: {0}").format(e))
        import traceback

        traceback.print_exc()
        app.conversions_running -= 1


def monitor_progress(app, process, progress_item, env_vars=None):
    """Monitor the progress of a running conversion process"""
    # Detect and display bit depth information
    if hasattr(progress_item, "input_file") and progress_item.input_file:
        bit_depth_info = detect_bit_depth_info(progress_item.input_file)
        GLib.idle_add(progress_item.add_output_text, bit_depth_info + "\n")

    # More accurate patterns for FFmpeg output
    time_pattern = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
    duration_pattern = re.compile(r"Duration: (\d+:\d+:\d+\.\d+)")
    output_file_pattern = re.compile(r"Output #0.*?\'(.*?)\'")

    # Add patterns for frame count tracking
    frame_pattern = re.compile(r"frame=\s*(\d+)")
    fps_pattern = re.compile(r"fps=\s*(\d+\.?\d*)")

    # Multiple patterns to get fps from various parts of FFmpeg output
    video_fps_pattern = re.compile(r"Stream #\d+:\d+.*Video:.*\s(\d+(?:\.\d+)?)\s*fps")
    alt_fps_pattern = re.compile(r"Video:.*?(\d+(?:\.\d+)?)\s*(?:tbr|fps)")

    # Encode mode and command patterns
    encode_mode_pattern = re.compile(r"Encode mode:\s*(.*)")
    running_command_pattern = re.compile(r"Running command:\s*(.*)")

    # Map technical encode modes to user-friendly translations
    encode_mode_map = {
        "": _("Software encoding"),
        "Decode GPU, encode GPU": _("Full GPU acceleration"),
        "Decode Software, Encode GPU": _("Software Decoding and GPU encoding"),
        "Decode Software, Encode Software": _("Software encoding"),
    }

    # Track when we detect the encode mode
    encode_mode_detected = False
    encode_mode = _("Unknown")  # Default value
    full_command = None

    # Values to track progress
    duration_secs = None
    duration_str = None
    current_time_secs = 0
    output_file = None
    last_output_time = time.time()
    processing_start_time = time.time()

    # Variables for frame-based progress tracking
    total_frames = None
    current_frame = 0
    video_fps = None
    max_current_frame = 0

    # Flag to track duration detection
    duration_detected = False

    # Variables for improved time estimation
    progress_samples = []
    sample_window = 10

    # Set initial status
    GLib.idle_add(progress_item.update_status, _("Starting process..."))
    GLib.idle_add(progress_item.add_output_text, _("Starting FFmpeg process..."))

    # Helper function to get user-friendly encode mode
    def get_friendly_encode_mode(technical_mode):
        """Convert technical encode mode to user-friendly message"""
        if technical_mode in encode_mode_map:
            return encode_mode_map[technical_mode]

        # Check for GPU usage patterns if not in the map
        technical_mode_lower = technical_mode.lower()
        if "gpu" in technical_mode_lower:
            if (
                "decode gpu" in technical_mode_lower
                and "encode gpu" in technical_mode_lower
            ):
                return _("Full GPU acceleration")
            elif "encode gpu" in technical_mode_lower:
                return _("GPU encoding")
            else:
                return _("GPU processing")

        # Default to the original string if no pattern matches
        return technical_mode

    # Helper function to update status with consistent format
    def update_status_with_mode_and_speed(fps_value=None):
        fps_display = f"{fps_value:.1f}" if fps_value is not None else "N/A"
        friendly_mode = get_friendly_encode_mode(encode_mode)

        if fps_value is not None:
            status_msg = f"{_('Speed')}: {fps_display} fps\n{friendly_mode}"
        else:
            status_msg = f"{friendly_mode}"

        GLib.idle_add(progress_item.update_status, status_msg)
        return friendly_mode

    try:
        import threading

        # Queue to collect output from both streams
        from queue import Empty, Queue

        output_queue = Queue()

        # Threads to read from stdout and stderr
        def read_stdout():
            for line in iter(process.stdout.readline, ""):
                if line:
                    output_queue.put(("stdout", line))
            output_queue.put(("stdout_end", None))

        def read_stderr():
            for line in iter(process.stderr.readline, ""):
                if line:
                    output_queue.put(("stderr", line))
            output_queue.put(("stderr_end", None))

        # Start reader threads
        stdout_thread = threading.Thread(target=read_stdout)
        stderr_thread = threading.Thread(target=read_stderr)
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()

        # Flags to track when streams are done
        stdout_done = False
        stderr_done = False

        # Process lines from both outputs as they come in
        while not (stdout_done and stderr_done) and not progress_item.was_cancelled():
            try:
                try:
                    source, line = output_queue.get(timeout=0.1)
                except Empty:
                    # This is normal - just check if we should continue waiting
                    if time.time() - last_output_time > 15:
                        timeout_msg = _("No progress detected. Process may be stuck.")
                        GLib.idle_add(progress_item.update_status, timeout_msg)
                        GLib.idle_add(progress_item.add_output_text, timeout_msg)
                        print("Process may be stuck - no output for 15 seconds")
                    continue

                if source == "stdout_end":
                    stdout_done = True
                    continue
                elif source == "stderr_end":
                    stderr_done = True
                    continue

                # Reset timeout counter with each line of output
                last_output_time = time.time()

                # Skip processing if line is None
                if line is None:
                    continue

                # Print the raw output for debugging with a simpler format
                print(f"FFMPEG: {line.strip()}")

                # Send output to terminal view
                GLib.idle_add(progress_item.add_output_text, line)

                # Check for encode mode in both stdout and stderr
                mode_match = encode_mode_pattern.search(line)
                if mode_match:
                    detected_mode = mode_match.group(1).strip()
                    if detected_mode:  # Make sure we got a non-empty string
                        encode_mode = detected_mode
                        encode_mode_detected = True

                        # Get user-friendly mode name using helper function
                        friendly_mode = get_friendly_encode_mode(detected_mode)

                        print(f"Detected encode mode from {source}: {encode_mode}")
                        print(f"Converted to friendly mode: {friendly_mode}")

                        GLib.idle_add(
                            progress_item.add_output_text,
                            f"Detected encode mode: {encode_mode} ({friendly_mode})",
                        )

                        # Update the UI immediately with the friendly encode mode
                        GLib.idle_add(progress_item.update_status, f"{friendly_mode}")

                # Check for FFmpeg command
                cmd_match = running_command_pattern.search(line)
                if cmd_match:
                    detected_cmd = cmd_match.group(1).strip()
                    if detected_cmd:  # Make sure we got a non-empty string
                        # Update the command text display in the UI
                        GLib.idle_add(
                            lambda: progress_item.cmd_text.set_text(detected_cmd)
                        )

                        # Don't automatically expand the command expander anymore
                        # Let the user click on it when they want to see the command

                        # Add as a special entry to the terminal with highlighting
                        highlight_text = f"\n{_('FFmpeg command')}:\n{detected_cmd}\n"
                        GLib.idle_add(
                            lambda: progress_item.terminal_buffer.insert(
                                progress_item.terminal_buffer.get_end_iter(),
                                highlight_text,
                            )
                        )

                if source == "stderr":
                    # Original stderr processing for other patterns
                    # Check if the process was cancelled
                    if progress_item.was_cancelled():
                        print("Process was cancelled, stopping monitor thread")
                        GLib.idle_add(
                            progress_item.add_output_text,
                            _("Process cancelled by user"),
                        )
                        break

                    # Capture output file if available
                    if "Output #0" in line and "'" in line:
                        output_match = output_file_pattern.search(line)
                        if output_match:
                            output_file = output_match.group(1)
                            print(f"Detected output file: {output_file}")
                            GLib.idle_add(
                                progress_item.add_output_text,
                                f"Output file: {output_file}",
                            )

                    # Extract video frame rate from input stream info
                    if video_fps is None and "Stream #" in line and "Video:" in line:
                        # Try primary pattern first
                        fps_match = video_fps_pattern.search(line)
                        if fps_match:
                            try:
                                video_fps = float(fps_match.group(1))
                                print(f"Detected video frame rate: {video_fps} fps")
                                GLib.idle_add(
                                    progress_item.add_output_text,
                                    f"Detected video frame rate: {video_fps} fps",
                                )
                            except (ValueError, TypeError) as e:
                                print(f"Error converting fps: {e}")
                        else:
                            # Try alternative pattern
                            alt_match = alt_fps_pattern.search(line)
                            if alt_match:
                                try:
                                    video_fps = float(alt_match.group(1))
                                    print(
                                        f"Detected video frame rate (alt pattern): {video_fps} fps"
                                    )
                                    GLib.idle_add(
                                        progress_item.add_output_text,
                                        f"Detected video frame rate: {video_fps} fps",
                                    )
                                except (ValueError, TypeError) as e:
                                    print(f"Error converting fps (alt pattern): {e}")

                    # Extract duration if not already done
                    if not duration_detected and "Duration" in line:
                        duration_match = duration_pattern.search(line)
                        if duration_match:
                            try:
                                duration_str = duration_match.group(1)
                                h, m, rest = duration_str.split(":")
                                s = rest.split(".")[
                                    0
                                ]  # Get seconds without milliseconds
                                ms = rest.split(".")[1] if "." in rest else "0"

                                # Calculate duration in seconds with millisecond precision
                                detected_duration_secs = (
                                    int(h) * 3600
                                    + int(m) * 60
                                    + int(s)
                                    + (int(ms) / 100)
                                )

                                # Override with expected segment duration if processing a segment
                                if (
                                    hasattr(progress_item, "expected_duration")
                                    and progress_item.expected_duration is not None
                                ):
                                    duration_secs = progress_item.expected_duration
                                    print(
                                        f"FFmpeg detected duration: {detected_duration_secs:.2f}s (full video), using expected segment duration: {duration_secs:.2f}s for progress calculation"
                                    )
                                else:
                                    duration_secs = detected_duration_secs
                                    print(
                                        f"Detected duration: {duration_str} ({duration_secs:.3f} seconds)"
                                    )

                                duration_detected = True

                                GLib.idle_add(
                                    progress_item.add_output_text,
                                    f"Detected duration: {duration_str}",
                                )

                                # Calculate total frames if we have both duration and fps
                                if video_fps is not None and video_fps > 0:
                                    # Sanity check - make sure fps is reasonable (1-120)
                                    if 1 <= video_fps <= 120:
                                        total_frames = int(duration_secs * video_fps)
                                        print(f"Estimated total frames: {total_frames}")
                                        GLib.idle_add(
                                            progress_item.add_output_text,
                                            f"Estimated total frames: {total_frames}",
                                        )
                                    else:
                                        print(
                                            f"Unreasonable fps detected: {video_fps}, not calculating total frames"
                                        )
                            except Exception as e:
                                print(f"Error parsing duration: {e}")

                # Process frame counts from either stream
                if "frame=" in line:
                    frame_match = frame_pattern.search(line)
                    if frame_match:
                        try:
                            current_frame = int(frame_match.group(1))
                            max_current_frame = max(max_current_frame, current_frame)

                            # Also extract current time if available in the same line
                            if "time=" in line:
                                time_match = time_pattern.search(line)
                                if time_match:
                                    try:
                                        time_str = time_match.group(1)
                                        h, m, rest = time_str.split(":")
                                        s = rest.split(".")[0]
                                        ms = rest.split(".")[1] if "." in rest else "0"
                                        current_time_secs = (
                                            int(h) * 3600
                                            + int(m) * 60
                                            + int(s)
                                            + (int(ms) / 100)
                                        )
                                    except Exception as e:
                                        pass  # Ignore time parsing errors

                            # Get info about current fps
                            current_fps = None
                            fps_match = fps_pattern.search(line)
                            if fps_match:
                                try:
                                    current_fps = float(fps_match.group(1))
                                except (ValueError, TypeError):
                                    pass

                            # If we don't have total frames yet but have duration
                            if (
                                total_frames is None
                                and duration_secs is not None
                                and duration_secs > 0
                            ):
                                # Try to use video_fps first (from Stream info)
                                if video_fps is not None and 1 <= video_fps <= 120:
                                    total_frames = int(duration_secs * video_fps)
                                    print(
                                        f"Estimated total frames from video fps: {total_frames} (duration={duration_secs:.2f}s, fps={video_fps})"
                                    )
                                    GLib.idle_add(
                                        progress_item.add_output_text,
                                        f"Estimated total frames: {total_frames} (from video stream fps: {video_fps})",
                                    )
                                # Fallback: use current_fps if available and reasonable
                                elif current_fps is not None and 1 <= current_fps <= 120:
                                    total_frames = int(duration_secs * current_fps)
                                    print(
                                        f"Estimated total frames from current fps: {total_frames} (duration={duration_secs:.2f}s, fps={current_fps})"
                                    )
                                    GLib.idle_add(
                                        progress_item.add_output_text,
                                        f"Estimated total frames: {total_frames} (from current fps: {current_fps})",
                                    )

                            # Sanity check for frame estimate
                            if (
                                total_frames is not None
                                and current_frame > total_frames * 1.5
                            ):
                                # Current frame count exceeds our total estimate by 50% - our estimate is likely wrong
                                # Recalculate based on observed frame count
                                if duration_secs and duration_secs > 0:
                                    processing_time = (
                                        time.time() - processing_start_time
                                    )
                                    # Estimate total frames based on elapsed time and observed frame count
                                    if (
                                        processing_time > 5
                                    ):  # Only do this after 5 seconds of processing
                                        estimated_total = (
                                            int(
                                                (current_frame * duration_secs)
                                                / current_time_secs
                                            )
                                            if current_time_secs > 0
                                            else 0
                                        )
                                        if estimated_total > total_frames:
                                            print(
                                                f"Adjusting total frame estimate from {total_frames} to {estimated_total}"
                                            )
                                            total_frames = estimated_total
                                            GLib.idle_add(
                                                progress_item.add_output_text,
                                                f"Adjusted total frames estimate to {total_frames}",
                                            )

                            # Calculate progress based on frames if total_frames is valid
                            if (
                                total_frames is not None
                                and total_frames > 0
                                and current_frame <= total_frames * 1.5
                            ):
                                # Cap progress at 99% until complete
                                progress = min(0.99, current_frame / total_frames)

                                # Process time estimation
                                processing_diff = time.time() - processing_start_time
                                if len(progress_samples) >= sample_window:
                                    progress_samples.pop(0)

                                if progress > 0:
                                    # Estimate remaining time
                                    eta_seconds = (processing_diff / progress) * (
                                        1 - progress
                                    )
                                    progress_samples.append((progress, eta_seconds))

                                    # Calculate average ETA from recent samples
                                    if len(progress_samples) > 1:
                                        fps_display = (
                                            f"{current_fps:.1f}"
                                            if current_fps is not None
                                            else "N/A"
                                        )

                                        # Get the friendly encode mode for display
                                        friendly_mode = encode_mode
                                        if encode_mode in encode_mode_map:
                                            friendly_mode = encode_mode_map[encode_mode]

                                        status_msg = f"{_('Speed')}: {fps_display} fps\n{friendly_mode}"
                                        GLib.idle_add(
                                            progress_item.update_progress,
                                            progress,
                                            f"{int(progress * 100)}%",
                                        )
                                        GLib.idle_add(
                                            progress_item.update_status, status_msg
                                        )

                            # Fallback to time-based progress if frames approach isn't working
                            elif (
                                "time=" in line
                                and duration_secs is not None
                                and duration_secs > 0
                            ):
                                time_match = time_pattern.search(line)
                                if time_match:
                                    try:
                                        time_str = time_match.group(1)
                                        h, m, rest = time_str.split(":")
                                        s = rest.split(".")[
                                            0
                                        ]  # Get seconds without milliseconds
                                        ms = rest.split(".")[1] if "." in rest else "0"

                                        # Calculate current time in seconds
                                        current_time_secs = (
                                            int(h) * 3600
                                            + int(m) * 60
                                            + int(s)
                                            + (int(ms) / 100)
                                        )
                                        progress = min(
                                            0.99, current_time_secs / duration_secs
                                        )

                                        # Calculate processing time and ETA
                                        processing_diff = (
                                            time.time() - processing_start_time
                                        )
                                        if progress > 0:
                                            eta_seconds = (
                                                processing_diff / progress
                                            ) * (1 - progress)

                                            # Modified status message to show only percentage and speed
                                            fps_display = (
                                                f"{current_fps:.1f}"
                                                if current_fps is not None
                                                else "N/A"
                                            )

                                            # Get the friendly encode mode for display
                                            friendly_mode = encode_mode
                                            if encode_mode in encode_mode_map:
                                                friendly_mode = encode_mode_map[
                                                    encode_mode
                                                ]

                                            status_msg = f"{_('Speed:')} {fps_display} fps\n{friendly_mode}"
                                            GLib.idle_add(
                                                progress_item.update_progress,
                                                progress,
                                                f"{int(progress * 100)}%",
                                            )
                                            GLib.idle_add(
                                                progress_item.update_status, status_msg
                                            )
                                    except Exception as e:
                                        print(f"Error calculating time progress: {e}")

                            # If neither frame nor time progress works, try to estimate from current data
                            else:
                                # Try to calculate total frames if we have duration, current time, and current frame
                                if (
                                    total_frames is None
                                    and duration_secs is not None
                                    and duration_secs > 0
                                    and current_time_secs > 1
                                    and current_frame > 30
                                ):
                                    # Estimate FPS from observed data: fps = frames / time
                                    estimated_fps = current_frame / current_time_secs
                                    if 1 <= estimated_fps <= 120:
                                        total_frames = int(duration_secs * estimated_fps)
                                        print(
                                            f"Estimated total frames from progress data: {total_frames} (fps={estimated_fps:.2f}, duration={duration_secs:.2f}s)"
                                        )
                                        GLib.idle_add(
                                            progress_item.add_output_text,
                                            f"Estimated total frames: {total_frames} (calculated from progress: {estimated_fps:.1f} fps)",
                                        )
                                        # Now recalculate progress with new total_frames
                                        progress = min(0.99, current_frame / total_frames)
                                        fps_display = f"{current_fps:.1f}" if current_fps is not None else f"{estimated_fps:.1f}"
                                        friendly_mode = encode_mode_map.get(encode_mode, encode_mode)
                                        status_msg = f"{_('Speed')}: {fps_display} fps\n{friendly_mode}"
                                        GLib.idle_add(
                                            progress_item.update_progress,
                                            progress,
                                            f"{int(progress * 100)}%",
                                        )
                                        GLib.idle_add(
                                            progress_item.update_status, status_msg
                                        )
                                        # Skip the rest of this fallback section since we now have progress
                                        continue

                                # Modified status message for indeterminate progress
                                if current_fps is not None:
                                    # Get the friendly encode mode for display
                                    friendly_mode = encode_mode
                                    if encode_mode in encode_mode_map:
                                        friendly_mode = encode_mode_map[encode_mode]

                                    status_msg = f"{_('Speed:')} {current_fps:.1f} fps\n{friendly_mode}"
                                else:
                                    # Get the friendly encode mode for display
                                    friendly_mode = encode_mode
                                    if encode_mode in encode_mode_map:
                                        friendly_mode = encode_mode_map[encode_mode]

                                    status_msg = f"{friendly_mode}"

                                # Use an arbitrary progress value based on frames processed
                                if max_current_frame > 0:
                                    arbitrary_progress = min(
                                        0.8,
                                        (current_frame / (max_current_frame + 1000))
                                        + 0.01,
                                    )
                                    GLib.idle_add(
                                        progress_item.update_progress,
                                        arbitrary_progress,
                                    )
                                else:
                                    GLib.idle_add(progress_item.update_progress, 0.01)

                                GLib.idle_add(progress_item.update_status, status_msg)

                        except Exception as e:
                            print(f"Error processing frame progress: {e}")

            except Exception as e:
                if not isinstance(e, Empty):  # Don't log Empty exceptions
                    print(f"Error processing output line: {e}")
                    import traceback

                    traceback.print_exc()

    except (BrokenPipeError, IOError) as e:
        # This can happen if the process is killed during readline
        error_msg = f"Process pipe error: {e} - process likely terminated"
        print(error_msg)
        GLib.idle_add(progress_item.add_output_text, error_msg)
    except Exception as e:
        error_msg = f"Error reading process output: {e}"
        print(error_msg)
        GLib.idle_add(progress_item.add_output_text, error_msg)

    # Process finished or was canceled
    try:
        if progress_item.was_cancelled():
            # This block is now the primary handler for user cancellation.
            # It signals the main app to stop everything.
            print("Monitor detected cancellation. Notifying app to stop queue.")
            GLib.idle_add(lambda: app.conversion_completed(False))

            # Update UI for this specific item
            cancel_msg = _("Conversion cancelled.")
            GLib.idle_add(progress_item.update_status, cancel_msg)
            GLib.idle_add(progress_item.update_progress, 0.0, _("Cancelled"))
            GLib.idle_add(progress_item.cancel_button.set_sensitive, False)

            # Remove this specific conversion item from the page after a delay
            GLib.timeout_add(
                2000,
                lambda: app.progress_page.remove_conversion(
                    progress_item.conversion_id
                ),
            )
        else:
            # Process finished normally, get return code with timeout
            max_wait_seconds = 7200  # 2 hours
            try:
                return_code = process.wait(timeout=max_wait_seconds)
            except subprocess.TimeoutExpired:
                timeout_error = f"ERROR: Process exceeded maximum time limit ({max_wait_seconds / 3600:.1f} hours). Force terminating..."
                print(timeout_error)
                GLib.idle_add(progress_item.add_output_text, timeout_error)

                try:
                    process.terminate()
                    time.sleep(2)
                    if process.poll() is None:
                        process.kill()
                        time.sleep(1)
                except Exception as e:
                    print(f"Error terminating stuck process: {e}")

                try:
                    return_code = process.wait(timeout=5)
                except:
                    return_code = -1

                timeout_msg = _("Conversion timed out after {0} hours").format(
                    max_wait_seconds / 3600
                )
                GLib.idle_add(progress_item.update_status, timeout_msg)
                GLib.idle_add(progress_item.add_output_text, timeout_msg)

            finish_msg = f"Process finished with return code: {return_code}"
            print(finish_msg)
            GLib.idle_add(progress_item.add_output_text, finish_msg)

            # Update user interface from main thread
            if return_code == 0:
                # Mark as successful
                GLib.idle_add(progress_item.mark_success)

                # Update progress bar
                GLib.idle_add(progress_item.update_progress, 1.0, _("Completed!"))
                complete_msg = _("Conversion completed successfully!")
                GLib.idle_add(progress_item.update_status, complete_msg)

                # Check if we should delete the original file
                if progress_item.delete_original and progress_item.input_file:
                    input_file = progress_item.input_file

                    debug_msg = (
                        f"Checking if original file should be deleted: {input_file}"
                    )
                    print(debug_msg)
                    GLib.idle_add(progress_item.add_output_text, debug_msg)

                    output_file_to_check = None

                    input_dirname = os.path.dirname(input_file)
                    input_basename = os.path.splitext(os.path.basename(input_file))[0]
                    possible_mp4 = os.path.join(input_dirname, f"{input_basename}.mp4")

                    if os.path.exists(possible_mp4):
                        output_file_to_check = possible_mp4
                        debug_msg = f"Found MP4 output: {output_file_to_check}"
                        print(debug_msg)
                        GLib.idle_add(progress_item.add_output_text, debug_msg)
                    elif (
                        output_file
                        and os.path.exists(output_file)
                        and output_file.lower().endswith((".mp4", ".mkv", ".avi"))
                    ):
                        output_file_to_check = output_file
                        debug_msg = f"Using FFmpeg detected video: {output_file}"
                        print(debug_msg)
                        GLib.idle_add(progress_item.add_output_text, debug_msg)
                    else:
                        output_folder = os.path.dirname(input_file)
                        if "output_folder" in env_vars and env_vars["output_folder"]:
                            output_folder = env_vars["output_folder"]

                        try:
                            for file in os.listdir(output_folder):
                                file_path = os.path.join(output_folder, file)
                                if file.lower().endswith(".mp4") and file.startswith(
                                    input_basename
                                ):
                                    file_mtime = os.path.getmtime(file_path)
                                    if time.time() - file_mtime < 60:
                                        output_file_to_check = file_path
                                        debug_msg = (
                                            f"Found recent MP4 in folder: {file_path}"
                                        )
                                        print(debug_msg)
                                        GLib.idle_add(
                                            progress_item.add_output_text, debug_msg
                                        )
                                        break
                        except Exception as e:
                            print(f"Error searching for MP4 files: {e}")
                            GLib.idle_add(
                                progress_item.add_output_text,
                                f"Error searching for MP4 files: {e}",
                            )

                    if output_file_to_check and os.path.exists(output_file_to_check):
                        input_size = os.path.getsize(input_file)
                        output_size = os.path.getsize(output_file_to_check)

                        input_size_mb = input_size / (1024 * 1024)
                        output_size_mb = output_size / (1024 * 1024)
                        percentage = (output_size / input_size) * 100

                        size_info = f"Compare: Input={input_size_mb:.2f}MB, Output={output_size_mb:.2f}MB ({percentage:.1f}% of original)"
                        print(size_info)
                        GLib.idle_add(progress_item.add_output_text, size_info)

                        min_size_threshold = input_size * 0.15

                        if output_size > min_size_threshold:
                            try:
                                os.remove(input_file)
                                delete_msg = f"Original file deleted: {input_file}"
                                print(delete_msg)
                                GLib.idle_add(progress_item.add_output_text, delete_msg)
                                is_queue_processing = (
                                    hasattr(progress_item, "is_queue_processing")
                                    and progress_item.is_queue_processing
                                )
                                is_segment_batch = (
                                    hasattr(progress_item, "is_segment_batch")
                                    and progress_item.is_segment_batch
                                )
                                if not is_queue_processing and not is_segment_batch:
                                    GLib.idle_add(
                                        lambda: show_info_dialog_and_close_progress(
                                            app,
                                            _(
                                                "Conversion completed successfully!\n\n"
                                                "The original file <b>{0}</b> was deleted."
                                            ).format(os.path.basename(input_file)),
                                            progress_item,
                                        )
                                    )
                            except Exception as e:
                                error_msg = f"Could not delete the original file: {e}"
                                print(error_msg)
                                GLib.idle_add(progress_item.add_output_text, error_msg)
                                is_queue_processing = (
                                    hasattr(progress_item, "is_queue_processing")
                                    and progress_item.is_queue_processing
                                )
                                is_segment_batch = (
                                    hasattr(progress_item, "is_segment_batch")
                                    and progress_item.is_segment_batch
                                )
                                if not is_queue_processing and not is_segment_batch:
                                    GLib.idle_add(
                                        lambda e=e: show_info_dialog_and_close_progress(
                                            app,
                                            _(
                                                "Conversion completed successfully!\n\n"
                                                "Could not delete the original file: {0}"
                                            ).format(e),
                                            progress_item,
                                        )
                                    )
                        else:
                            size_warning = "The converted file size is suspicious, so the original file was not removed."
                            print(size_warning)
                            GLib.idle_add(progress_item.add_output_text, size_warning)
                            is_queue_processing = (
                                hasattr(progress_item, "is_queue_processing")
                                and progress_item.is_queue_processing
                            )
                            is_segment_batch = (
                                hasattr(progress_item, "is_segment_batch")
                                and progress_item.is_segment_batch
                            )
                            if not is_queue_processing and not is_segment_batch:
                                GLib.idle_add(
                                    lambda: show_info_dialog_and_close_progress(
                                        app,
                                        _(
                                            "Conversion completed successfully!\n\n"
                                            "The converted file size is suspicious, so the original file was not removed."
                                        ),
                                        progress_item,
                                    )
                                )
                    else:
                        error_msg = (
                            f"Output file not found or not accessible: {output_file}"
                        )
                        print(error_msg)
                        GLib.idle_add(progress_item.add_output_text, error_msg)
                        if output_file_to_check:
                            output_dir = os.path.dirname(output_file_to_check)
                            try:
                                files = os.listdir(output_dir)
                                files_info = (
                                    f"Files in output directory: {', '.join(files)}"
                                )
                                print(files_info)
                                GLib.idle_add(progress_item.add_output_text, files_info)
                            except Exception as e:
                                print(f"Error listing directory: {e}")

                        is_queue_processing = (
                            hasattr(progress_item, "is_queue_processing")
                            and progress_item.is_queue_processing
                        )
                        is_segment_batch = (
                            hasattr(progress_item, "is_segment_batch")
                            and progress_item.is_segment_batch
                        )
                        if not is_queue_processing and not is_segment_batch:
                            GLib.idle_add(
                                lambda: show_info_dialog_and_close_progress(
                                    app,
                                    _("Conversion completed successfully!"),
                                    progress_item,
                                )
                            )
                else:
                    is_queue_processing = (
                        hasattr(progress_item, "is_queue_processing")
                        and progress_item.is_queue_processing
                    )
                    is_segment_batch = (
                        hasattr(progress_item, "is_segment_batch")
                        and progress_item.is_segment_batch
                    )

                    if not is_queue_processing and not is_segment_batch:
                        GLib.idle_add(
                            lambda: show_info_dialog_and_close_progress(
                                app,
                                _("Conversion completed successfully!"),
                                progress_item,
                            )
                        )
                    else:
                        if not is_segment_batch:
                            GLib.timeout_add(
                                3000,
                                lambda: app.progress_page.remove_conversion(
                                    progress_item.conversion_id
                                ),
                            )

                if not is_segment_batch:
                    GLib.timeout_add(
                        5000,
                        lambda: app.progress_page.remove_conversion(
                            progress_item.conversion_id
                        ),
                    )

                if not is_segment_batch:
                    GLib.idle_add(lambda: app.conversion_completed(True))

            else:
                error_msg = _("Conversion failed with code {0}").format(return_code)
                GLib.idle_add(progress_item.update_progress, 0.0, _("Error!"))
                GLib.idle_add(progress_item.update_status, error_msg)
                GLib.idle_add(progress_item.add_output_text, error_msg)

                is_queue_processing = (
                    hasattr(progress_item, "is_queue_processing")
                    and progress_item.is_queue_processing
                )
                is_segment_batch = (
                    hasattr(progress_item, "is_segment_batch")
                    and progress_item.is_segment_batch
                )
                if not is_queue_processing and not is_segment_batch:
                    GLib.idle_add(
                        lambda: show_error_dialog_and_close_progress(
                            app,
                            _(
                                "The conversion failed with error code {0}.\n\n"
                                "Check the log for more details."
                            ).format(return_code),
                            progress_item,
                        )
                    )
                else:
                    if not is_segment_batch:
                        GLib.timeout_add(
                            5000,
                            lambda: app.progress_page.remove_conversion(
                                progress_item.conversion_id
                            ),
                        )

                if not is_segment_batch:
                    GLib.idle_add(lambda: app.conversion_completed(False))

            GLib.idle_add(progress_item.cancel_button.set_sensitive, False)
    except Exception as e:
        error_msg = f"FATAL: Exception in progress monitor: {e}"
        print(error_msg)
        import traceback

        traceback.print_exc()

        try:
            GLib.idle_add(progress_item.add_output_text, error_msg)
            GLib.idle_add(progress_item.update_status, _("Monitor error"))
        except:
            pass

        is_segment_batch = (
            hasattr(progress_item, "is_segment_batch")
            and progress_item.is_segment_batch
        )
        if not is_segment_batch:
            print("Notifying app of conversion failure due to monitor exception")
            GLib.idle_add(lambda: app.conversion_completed(False))
    finally:
        app.conversions_running -= 1
        completion_msg = (
            f"Conversion finished, active conversions: {app.conversions_running}"
        )
        print(completion_msg)
        try:
            GLib.idle_add(progress_item.add_output_text, completion_msg)
        except:
            pass


def show_info_dialog_and_close_progress(app, message, progress_item):
    """Shows a system notification instead of dialog"""
    GLib.timeout_add(
        5000, lambda: app.progress_page.remove_conversion(progress_item.conversion_id)
    )
    app.send_system_notification(_("Information"), message)


def show_error_dialog_and_close_progress(app, message, progress_item):
    """Shows an error dialog"""
    GLib.timeout_add(
        5000, lambda: app.progress_page.remove_conversion(progress_item.conversion_id)
    )
    app.show_error_dialog(message)


def build_convert_command(input_file, settings):
    """Build the convert command and environment variables"""
    from utils.file_info import has_audio_streams

    cmd = [CONVERT_SCRIPT_PATH, input_file]
    env_vars = os.environ.copy()

    setting_keys = [
        "gpu",
        "video-quality",
        "video-codec",
        "preset",
        "subtitle-extract",
        "audio-handling",
        "audio-bitrate",
        "audio-channels",
        "audio-codec",
        "video-resolution",
        "additional-options",
        "gpu-partial",
        "force-copy-video",
        "only-extract-subtitles",
        "output-folder",
    ]

    force_copy = (
        settings.get_value("force-copy-video")
        if hasattr(settings, "get_value")
        else settings.get("force-copy-video")
    )
    is_copy_mode = force_copy in ["1", True, "true", "True"]

    for key in setting_keys:
        value = (
            settings.get_value(key)
            if hasattr(settings, "get_value")
            else settings.get(key)
        )

        if is_copy_mode and key in [
            "video-codec",
            "video-quality",
            "video-resolution",
            "gpu",
            "gpu-partial",
            "preset",
        ]:
            print(f"Skipping {key} because copy mode is enabled")
            continue

        if key == "audio-handling" and value:
            if not has_audio_streams(input_file):
                value = "none"
                print(
                    f"No audio streams detected in {os.path.basename(input_file)}, overriding audio_handling to 'none'"
                )

        if value not in [None, "", False]:
            env_key = key.replace("-", "_")
            env_vars[env_key] = str(value)
            print(f"Setting {env_key}={value}")

    trim_start = settings.get_value("video-trim-start")
    trim_end = settings.get_value("video-trim-end")

    if trim_start > 0:
        env_vars["trim_start"] = str(trim_start)
        print(f"Setting trim_start={trim_start}")

    if trim_end > 0:
        env_vars["trim_end"] = str(trim_end)
        print(f"Setting trim_end={trim_end}")

    if "output_folder" not in env_vars and input_file:
        env_vars["output_folder"] = os.path.dirname(input_file)
        print(
            f"Setting output folder to input file directory: {env_vars['output_folder']}"
        )

    return cmd, env_vars
