"""Supervise conversion processes without guessing outputs or touching GTK off-thread."""

from collections import deque
import codecs
import gettext
import logging
import os
import re
import selectors
import subprocess
import threading
import time

from gi.repository import GLib

from utils.ffmpeg_path import get_ffmpeg_executable, get_ffprobe_executable
from utils.ffmpeg_options import parse_additional_options
from utils.media_validation import (
    ConversionResult, FileIdentity, OutputDurationMismatch, discard_job_paths,
    media_duration, probe_media, remove_original, stream_count,
    terminate_process_group, validate_output,
)

logger = logging.getLogger(__name__)
_ = gettext.gettext
STUCK_WARNING_SECONDS = 180
MAX_CONVERSION_SECONDS = 24 * 3600


def _ffmpeg_error_map() -> list[tuple[str, str]]:
    """Common FFmpeg error patterns mapped to user-friendly messages.

    Built on every call, not at import time: this module is imported before
    gettext is bound, so a module-level list would freeze the English strings.
    Keys are lowercased substrings matched against stderr lines.
    """
    return [
        ("no space left on device", _("There is no space left on the disk.")),
        ("permission denied", _("Permission denied — check file/folder permissions.")),
        ("no such file or directory", _("File or folder not found.")),
        ("invalid data found when processing input", _("The file appears to be corrupted or in an unsupported format.")),
        ("codec not currently supported", _("This codec is not supported on your system.")),
        ("unknown decoder", _("A required decoder is not installed.")),
        ("unknown encoder", _("A required encoder is not installed.")),
        ("encoder setup failed", _("Failed to initialize the encoder — the selected settings may be incompatible.")),
        ("hardware accel", _("Hardware acceleration failed. Try disabling GPU encoding.")),
        ("out of memory", _("Not enough memory to complete the conversion.")),
        ("does not contain any stream", _("The file does not contain a valid media stream.")),
        ("moov atom not found", _("The video file is incomplete or damaged (missing metadata).")),
        ("decoding for stream", _("Could not decode the file — it may be corrupted.")),
    ]


def _friendly_ffmpeg_error(stderr_lines: list[str]) -> str:
    """Return a user-friendly message for the first matching FFmpeg error pattern."""
    error_map = _ffmpeg_error_map()
    for line in stderr_lines:
        lower = line.lower()
        for pattern, message in error_map:
            if pattern in lower:
                return message
    return ""



def call_on_main(callback, *args, wait=False):
    """Dispatch a one-shot callback; a timeout cannot create a late GTK widget."""
    if threading.current_thread() is threading.main_thread():
        return callback(*args)
    done = threading.Event()
    expired = threading.Event()
    values = []
    errors = []

    def dispatch():
        try:
            if not expired.is_set():
                values.append(callback(*args))
        except Exception as error:
            errors.append(error)
            logger.exception("Main-loop callback failed")
        finally:
            done.set()
        return False

    GLib.idle_add(dispatch)
    if wait:
        if not done.wait(10):
            expired.set()
            raise TimeoutError("Timed out waiting for the GTK main loop")
        if errors:
            raise errors[0]
        return values[0] if values else None
    return None


class _Updates:
    """At most one pending UI callback, with bounded logs and latest progress."""

    def __init__(self, item):
        self.item = item
        self.lock = threading.Lock()
        self.lines = deque(maxlen=256)
        self.progress = None
        self.status = None
        self.command = None
        self.pending = False
        self.closed = False

    def push(self, *, text=None, progress=None, status=None, command=None):
        with self.lock:
            if self.closed:
                return
            if text:
                self.lines.append(text[-8192:])
            if progress is not None:
                self.progress = progress
            if status is not None:
                self.status = status
            if command is not None:
                self.command = command
            if not self.pending:
                self.pending = True
                GLib.timeout_add(100, self.flush)

    def flush(self):
        with self.lock:
            lines = list(self.lines)
            self.lines.clear()
            progress, status, command = self.progress, self.status, self.command
            self.progress = self.status = self.command = None
            self.pending = False
        if lines:
            self.item.add_output_text("".join(line if line.endswith("\n") else line + "\n" for line in lines))
        if progress is not None:
            self.item.update_progress(progress)
        if status is not None:
            self.item.update_status(status)
        if command is not None:
            self.item.cmd_text.set_text(command)
        return False

    def close(self):
        with self.lock:
            self.closed = True
        self.flush()


def detect_bit_depth_info(file_path: str):
    """Read named fields: FFprobe does not promise show_entries CSV order."""
    try:
        data = probe_media(file_path)
        stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
        pixel_format = stream.get("pix_fmt", "unknown")
        codec = stream.get("codec_name", "unknown")
        return f"Video stream: codec={codec}, pixel format={pixel_format}"
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
        return "Could not analyze the video stream with ffprobe"


def _time_value(value: str) -> float:
    parts = value.split(":")
    result = 0.0
    for part in parts:
        result = result * 60 + float(part)
    return result


TEXT_SUBTITLE_CODECS = frozenset({"subrip", "ass", "ssa", "mov_text", "text", "webvtt"})
TEXT_ONLY_CONTAINERS = frozenset({".mp4", ".mov", ".m4v", ".webm"})


def _expected_subtitles(data, destination):
    """Count the subtitle streams the container can actually carry.

    The script skips a bitmap track that has no text form in MP4, MOV or WebM
    rather than refusing the job, so expecting all of them would report a good
    conversion as failed.
    """
    streams = [s for s in data["streams"] if s.get("codec_type") == "subtitle"]
    if os.path.splitext(destination or "")[1].lower() in TEXT_ONLY_CONTAINERS:
        streams = [s for s in streams if s.get("codec_name") in TEXT_SUBTITLE_CODECS]
    return len(streams)


def _expected_media(source, env, duration, destination=None):
    data = probe_media(source, executable=env.get("ffprobe_executable")) if source else None
    options = parse_additional_options(env.get("options", ""))
    if duration is None and data:
        duration = media_duration(data)
        start = 0.0
        for index, token in enumerate(options):
            if token == "-ss":
                start = _time_value(options[index + 1])
        if duration is not None:
            duration = max(0.0, duration - start)
        for index, token in enumerate(options):
            if token == "-t":
                requested = _time_value(options[index + 1])
                duration = min(duration, requested) if duration is not None else requested
            elif token == "-to":
                requested = max(0.0, _time_value(options[index + 1]) - start)
                duration = min(duration, requested) if duration is not None else requested
    # Explicit user maps may legitimately change the default stream inventory.
    expected = None
    if data and "-map" not in options:
        expected = {"video": 1}
        expected["audio"] = 0 if env.get("audio_handling") == "none" or "-an" in options else stream_count(data, "audio")
        embedded = env.get("subtitle_extract") == "embedded" and "-sn" not in options
        expected["subtitle"] = _expected_subtitles(data, destination) if embedded else 0
    return duration, expected


def run_with_progress_dialog(app, cmd: list, title_suffix, input_file=None,
                             delete_original=None, env_vars=None,
                             wait_for_completion=False, is_segment_batch=False,
                             segment_duration=None, *, job_id=None,
                             cancel_event=None, progress_item=None,
                             source_file=None, output_file=None):
    """Launch one stage; synchronous callers receive its validated result.

    Only this supervisor completes a stage. Segment batches own the one terminal
    notification for their parent job. Waiting on the GTK thread is prohibited.
    """
    if wait_for_completion and threading.current_thread() is threading.main_thread():
        raise RuntimeError("A synchronous conversion must run off the GTK thread")
    env = dict(os.environ if env_vars is None else env_vars)
    env.setdefault("ffmpeg_executable", get_ffmpeg_executable())
    env.setdefault("ffprobe_executable", get_ffprobe_executable())
    source_file = source_file or input_file
    destination = output_file or env.get("output_file")
    if destination:
        destination = os.path.abspath(destination)
        env["output_file"] = destination
        env.pop("output_folder", None)
    if delete_original is None:
        delete_original = bool(getattr(app, "delete_original_after_conversion", False))
    cancel_event = cancel_event or threading.Event()
    result_box = []
    process = None
    counted = False

    try:
        def prepare_item():
            nonlocal progress_item, counted
            if progress_item is None:
                progress_item = app.progress_page.add_conversion(
                    title_suffix or os.path.basename(input_file or ""), input_file, process)
            else:
                progress_item.process = process
            progress_item.cancel_event = cancel_event
            progress_item.job_id = job_id
            progress_item.is_segment_batch = is_segment_batch
            progress_item.expected_duration = segment_duration
            progress_item.delete_original = bool(delete_original)
            progress_item.expected_output = destination
            app.conversions_running += 1
            counted = True
            return progress_item

        call_on_main(prepare_item, wait=True)
        identity = None
        if source_file:
            # Symlink inputs may be converted, but never authorize removal.
            try:
                identity = FileIdentity.capture(source_file)
            except ValueError:
                if not os.path.isfile(source_file):
                    raise
        if cancel_event.is_set():
            raise InterruptedError("Conversion cancelled before starting")
        process = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, env=env, start_new_session=True,
        )

        call_on_main(setattr, progress_item, "process", process, wait=True)
        thread = threading.Thread(
            target=monitor_progress,
            args=(app, process, progress_item, env),
            kwargs=dict(source_file=source_file, identity=identity,
                        result_box=result_box, job_id=job_id),
            daemon=True,
        )
        thread.start()
        if wait_for_completion:
            thread.join()
            return result_box[0]
        return None
    except Exception as error:
        logger.exception("Could not start conversion")
        if process is not None:
            try:
                terminate_process_group(process)
            except (OSError, subprocess.SubprocessError):
                logger.exception("Could not reap failed conversion")
            for pipe in (process.stdout, process.stderr):
                if pipe:
                    pipe.close()
        was_cancelled = isinstance(error, InterruptedError) or cancel_event.is_set()
        def failed(message=str(error)):
            if counted:
                app.conversions_running = max(0, app.conversions_running - 1)
            if not was_cancelled:
                app.show_error_dialog(_("Error starting conversion: {0}").format(message))
            if not is_segment_batch:
                if progress_item is not None:
                    progress_item.process = None
                    if was_cancelled:
                        progress_item.mark_cancelled()
                        app.progress_page.mark_conversion_complete(progress_item.conversion_id, False)
                    else:
                        progress_item.mark_failure()
                app.conversion_completed(False, file_path=input_file, job_id=job_id)
        call_on_main(failed)
        return ConversionResult(False, -1, cancelled=was_cancelled, error=str(error))


def monitor_progress(app, process, progress_item, env_vars=None, *, source_file=None,
                     identity=None, result_box=None, job_id=None):
    """Drain both pipes with a monotonic deadline and one terminal callback."""
    env = env_vars or {}
    cancelled = progress_item.cancel_event
    destination = progress_item.expected_output
    updates = _Updates(progress_item)
    stderr_tail = deque(maxlen=40)
    selector = selectors.DefaultSelector()
    decoders = {}
    buffers = {}
    started = last_output = time.monotonic()
    duration = progress_item.expected_duration
    expected_streams = None
    result = ConversionResult(False, -1, destination)
    stage_mode = _("Software encoding")
    warning_shown = False
    monitor_error = None
    encoded_frames = None
    reported_duration = None
    workspace = None

    def consume(text, source):
        nonlocal duration, stage_mode, encoded_frames, workspace, reported_duration
        updates.push(text=text)
        # The script announces the directory it owns, so a job killed before
        # its own cleanup leaves nothing for the user to find and wonder about.
        if text.startswith("Job workspace:"):
            workspace = text.partition(":")[2].strip()
        if source == "stderr":
            stderr_tail.append(text.strip())
        if text.startswith("Running command:"):
            updates.push(command=text.partition(":")[2].strip())
        if text.startswith("Encode mode:"):
            technical = text.partition(":")[2].strip()
            stage_mode = {
                "Decode GPU, encode GPU": _("Full GPU acceleration"),
                "Decode Software, Encode GPU": _("Software Decoding and GPU encoding"),
            }.get(technical, _("Software encoding"))
            updates.push(status=stage_mode)
        # FFmpeg's own frame tally, used later to prove the muxed file is not
        # short of what the encoder said it wrote.
        written = re.search(r"frame=\s*(\d+)", text)
        if written:
            encoded_frames = int(written[1])
        # FFmpeg reports the input's own duration, which keeps the bar moving
        # for the files FFprobe cannot read at all (Matroska with DVD
        # subtitles, for one). Only progress uses it: the validated duration
        # decides whether an original may be deleted.
        if reported_duration is None:
            reported = re.search(r"Duration:\s*(\d+:\d+:\d+(?:\.\d+)?)", text)
            if reported:
                reported_duration = _time_value(reported[1])
        match = re.search(r"time=\s*(\d+:\d+:\d+(?:\.\d+)?)", text)
        total = duration or reported_duration
        if match and total and total > 0:
            progress = min(0.99, max(0.0, _time_value(match[1]) / total))
            fps = re.search(r"fps=\s*(\d+(?:\.\d+)?)", text)
            status = f"{stage_mode} | {fps[1]} fps" if fps else stage_mode
            updates.push(progress=progress, status=status)
        if "Waiting for audio NR" in text or "waiting for audio NR" in text:
            updates.push(status=_("Improving audio quality"))

    try:
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            selector.register(pipe, selectors.EVENT_READ, name)
            decoders[name] = codecs.getincrementaldecoder("utf-8")("replace")
            buffers[name] = ""
        updates.push(status=_("Starting process..."))
        try:
            duration, expected_streams = _expected_media(source_file, env, duration, destination)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
            # Metadata failure is not proof of input corruption. It does,
            # however, prohibit automatic deletion of the source.
            updates.push(text=f"Input probe unavailable: {error}")
            identity = None
        while selector.get_map() or process.poll() is None:
            if cancelled.is_set() or progress_item.was_cancelled():
                cancelled.set()
                raise InterruptedError("Conversion cancelled")
            now = time.monotonic()
            if now - started >= MAX_CONVERSION_SECONDS:
                raise TimeoutError("Conversion exceeded the time limit")
            events = selector.select(timeout=0.1)
            if not events and now - last_output > STUCK_WARNING_SECONDS and not warning_shown:
                updates.push(text=_("No progress detected. Process may be stuck."))
                warning_shown = True
            for key, _mask in events:
                name = key.data
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    text = buffers[name] + decoders[name].decode(b"", final=True)
                    if text:
                        consume(text, name)
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                last_output = time.monotonic()
                warning_shown = False
                text = buffers[name] + decoders[name].decode(chunk)
                lines = re.split(r"[\r\n]", text)
                buffers[name] = lines.pop()
                for line in lines:
                    if line:
                        consume(line, name)
                # A malformed stream without line separators cannot grow memory.
                if len(buffers[name]) > 65536:
                    consume(buffers[name][:65536], name)
                    buffers[name] = buffers[name][65536:]
        remaining = max(0.01, MAX_CONVERSION_SECONDS - (time.monotonic() - started))
        returncode = process.wait(timeout=remaining)
        if cancelled.is_set():
            raise InterruptedError("Conversion cancelled")
        if returncode:
            error = _friendly_ffmpeg_error(list(stderr_tail)) or _("Conversion failed with code {0}").format(returncode)
            result = ConversionResult(False, returncode, destination, error=error)
        else:
            extraction_only = env.get("only_extract_subtitles") == "1"
            duration_verified = duration is not None
            if not extraction_only:
                if not destination:
                    raise ValueError("No explicit output path was supplied")
                copy_mode = env.get("force_copy_video") == "1"
                try:
                    validate_output(destination, source=source_file,
                                    expected_duration=duration, expected_streams=expected_streams,
                                    duration_tolerance=3.0 if copy_mode else None,
                                    allow_longer=copy_mode,
                                    ffprobe=env.get("ffprobe_executable"))
                except OutputDurationMismatch as mismatch:
                    # An unexpected length is not proof of a bad file: a stream
                    # copy cuts on keyframes and a variable frame rate source
                    # gets re-timed. Report it and keep the original.
                    duration_verified = False
                    updates.push(text=f"Output duration not verified: {mismatch}")
            result = ConversionResult(True, 0, None if extraction_only else destination)
            if progress_item.delete_original and source_file and not extraction_only:
                try:
                    if identity is None or not duration_verified:
                        raise ValueError("The output could not be verified against the input")
                    updates.push(status=_("Checking output file..."))
                    remove_original(source_file, identity, [destination], cancelled,
                                    expected_frames=encoded_frames,
                                    ffmpeg=env.get("ffmpeg_executable"),
                                    ffprobe=env.get("ffprobe_executable"))
                    updates.push(text="Original deleted after output validation")
                except InterruptedError:
                    raise
                except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
                    updates.push(text=f"Original preserved: {error}")
    except InterruptedError as error:
        cancelled.set()
        result = ConversionResult(False, -1, destination, cancelled=True, error=str(error))
    except Exception as error:
        monitor_error = error
        logger.exception("Conversion monitoring/validation failed")
        result = ConversionResult(False, -1, destination, error=str(error))
    finally:
        try:
            if process.poll() is None or cancelled.is_set() or monitor_error:
                terminate_process_group(process)
        except (OSError, subprocess.SubprocessError):
            logger.exception("Could not reap conversion process group")
        if not result.success:
            discard_job_paths(workspace)
        selector.close()
        for pipe in (process.stdout, process.stderr):
            if pipe and not pipe.closed:
                pipe.close()
        if result_box is not None:
            result_box.append(result)

        def finish():
            updates.close()
            app.conversions_running = max(0, app.conversions_running - 1)
            progress_item.process = None
            if result.error:
                progress_item.add_output_text(result.error)
            if progress_item.is_segment_batch:
                return
            if result.cancelled:
                progress_item.mark_cancelled()
                app.progress_page.mark_conversion_complete(progress_item.conversion_id, False)
            elif result.success:
                progress_item.mark_success()
            else:
                progress_item.mark_failure()
                progress_item.update_status(result.error or _("Failed"))
            progress_item.cancel_button.set_sensitive(False)
            if not result.cancelled:
                notify_completion(app, result)
            if not hasattr(app, "completed_conversions"):
                app.completed_conversions = []
            app.completed_conversions.append({"input_file": source_file,
                "output_file": result.output_file, "success": result.success,
                "cancelled": result.cancelled, "job_id": job_id})
            app.conversion_completed(result.success, file_path=source_file, job_id=job_id)
        call_on_main(finish)


def notify_completion(app, result, *, body=None) -> None:
    """Announce the end of a job the way the application always has.

    A system notification is the point of an hour-long conversion the user
    minimized. The dialog is only for a job with nothing behind it in the
    queue, which keeps its own summary instead.
    """
    if result.success:
        app.send_system_notification(
            _("Conversion Complete"), body or _("Conversion completed successfully!"))
        return
    detail = result.error or _("Failed")
    app.send_system_notification(_("Error"), detail)
    if not getattr(app, "conversion_queue", None):
        app.show_error_dialog(
            _("The conversion failed with error code {0}.").format(result.returncode)
            + f"\n\n{detail}")
