"""Job-owned paths and media checks, independent of GTK widgets.

A successful exit and a non-empty file are necessary, not sufficient. In
particular, deletion must never discover an output by guessing its filename.
"""

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time

from utils.ffmpeg_path import get_ffmpeg_executable, get_ffprobe_executable


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def capture(cls, path: str):
        value = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode):
            raise ValueError("Expected a regular file, not a symlink or device")
        return cls(value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


@dataclass(frozen=True)
class ConversionResult:
    success: bool
    returncode: int
    output_file: str | None = None
    cancelled: bool = False
    error: str = ""


class OutputDurationMismatch(ValueError):
    """The output is readable, but not for the interval that was requested."""


def probe_media(path: str, *, executable: str | None = None, timeout=15) -> dict:
    result = subprocess.run(
        [executable or get_ffprobe_executable(), "-v", "error", "-show_streams",
         "-show_format", "-of", "json", os.path.abspath(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=True,
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("streams"), list):
        raise ValueError("FFprobe did not return a media stream inventory")
    return data


def media_duration(data: dict, kind: str = "video") -> float | None:
    """Duration of the reference stream, falling back to the container's own.

    The longest stream is the wrong reference: a stream copy routinely leaves
    audio seconds longer than the video that was asked for, so comparing the
    maximum both accepts a video cut short and rejects a correct conversion.
    """
    candidates = [s.get("duration") for s in data.get("streams", [])
                  if s.get("codec_type") == kind]
    candidates.append(data.get("format", {}).get("duration"))
    for value in candidates:
        try:
            value = float(value)
        except (ValueError, TypeError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    return None


def stream_count(data: dict, kind: str) -> int:
    return sum(s.get("codec_type") == kind for s in data.get("streams", []))


def validate_output(path: str, *, source: str | None = None,
                    expected_duration: float | None = None,
                    expected_streams: dict | None = None,
                    duration_tolerance: float | None = None,
                    allow_longer: bool = False,
                    ffprobe: str | None = None) -> dict:
    identity = FileIdentity.capture(path)
    if identity.size == 0:
        raise ValueError("The conversion produced an empty output")
    if source and os.path.samefile(path, source):
        raise ValueError("The output is the original input file")
    media = probe_media(path, executable=ffprobe)
    if not stream_count(media, "video"):
        raise ValueError("The output contains no video stream")
    for kind, expected in (expected_streams or {}).items():
        if stream_count(media, kind) != expected:
            raise ValueError(f"Unexpected number of {kind} streams in the output")
    if expected_duration is not None:
        if not math.isfinite(expected_duration) or expected_duration <= 0:
            raise ValueError("Invalid expected output duration")
        actual = media_duration(media)
        tolerance = (max(1.0, 0.02 * expected_duration) if duration_tolerance is None
                     else duration_tolerance)
        if actual is None:
            raise OutputDurationMismatch("The output reports no usable duration")
        # A keyframe cut can only keep more than was asked for, never less, so
        # a stream copy is judged on the shortfall alone.
        if (expected_duration - actual > tolerance
                or (not allow_longer and actual - expected_duration > tolerance)):
            raise OutputDurationMismatch(
                f"expected about {expected_duration:.2f}s, output has {actual:.2f}s")
    if FileIdentity.capture(path) != identity:
        raise ValueError("The output changed during validation")
    return media


def terminate_process_group(process: subprocess.Popen, grace: float = 20.0) -> None:
    """Reap our child and terminate its entire private session, never the GUI.

    The grace period has to cover what the child does on SIGTERM: FFmpeg
    finalizes the container it was writing and the script then deletes a
    partial file that can be gigabytes. Two seconds cut that short and left
    the debris behind; the caller still verifies and removes what survived.
    """
    if process.pid == os.getpgrp():
        raise ValueError("Refusing to terminate the application's process group")
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    # The leader can exit while descendants still hold stdout/stderr open.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


FULL_DECODE_SECONDS = 30.0


def sample_windows(duration: float | None) -> tuple[tuple[float, float], ...]:
    """Intervals to decode: both edges first, then a few interior probes.

    A broken first GOP and a truncated tail are the failures that actually
    happen, and they sit at fixed positions, so seven seconds of sampled media
    costs the same for a clip as for a film. Short media is decoded whole
    instead: at that length one pass is already cheap and it covers every
    frame rather than seven seconds of them. An unknown duration cannot be
    sampled at all, so it is decoded whole for the same price.
    """
    if not duration or not math.isfinite(duration) or duration <= FULL_DECODE_SECONDS:
        return ((0.0, math.inf),)
    step = duration / 4
    return ((0.0, 2.0), (duration - 2.0, 2.0),
            *((round(step * n, 3), 1.0) for n in (1, 2, 3)))


def _decode_window(path: str, start: float, length: float,
                   cancelled: threading.Event, *, ffmpeg: str | None = None,
                   timeout=120) -> None:
    # Accurate seeking would decode and discard everything from the preceding
    # keyframe, which on a long GOP costs as much as the whole file. Landing on
    # the keyframe itself is what keeps a window's cost proportional to it.
    interval = ([] if math.isinf(length) else
                ["-noaccurate_seek", "-ss", f"{start:.3f}", "-t", f"{length:.3f}"])
    with tempfile.TemporaryFile() as error_log:
        with subprocess.Popen(
            [ffmpeg or get_ffmpeg_executable(), "-nostdin", "-v", "error", "-xerror",
             "-err_detect", "explode", *interval,
             "-i", path, "-map", "0:v", "-map", "0:a?", "-f", "null", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=error_log, start_new_session=True,
        ) as process:
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if cancelled.wait(0.1):
                        raise InterruptedError("Cancelled before deleting the original")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Output decoding validation timed out")
            finally:
                if process.poll() is None:
                    terminate_process_group(process)
        if process.returncode:
            error_log.seek(0)
            detail = error_log.read().decode("utf-8", "replace").strip().splitlines()
            raise ValueError(f"The output failed to decode at {start:.1f}s: "
                             + (detail[-1] if detail else "unknown decoder error"))


def verify_integrity(path: str, cancelled: threading.Event, *,
                     expected_frames: int | None = None, ffprobe: str | None = None,
                     ffmpeg: str | None = None, timeout=900) -> None:
    """Prove the published file is complete, without decoding all of it.

    Decoding every frame costs a full pass over the media and still misses the
    two failures that matter before an irreversible delete: a stream muxed with
    no packets at all, and a container whose header promises a length the data
    never reaches. Counting packets reads the whole file without decoding
    pixels, which is what exposes both (FFprobe reports a premature end of a
    Matroska file on stderr while still exiting successfully), and comparing
    the count with the frames FFmpeg said it wrote closes the loop between
    producer and file. Decoding is then only needed as a spot check.
    """
    path = os.path.abspath(path)
    probe = subprocess.run(
        [ffprobe or get_ffprobe_executable(), "-v", "error", "-err_detect", "explode",
         "-count_packets", "-show_entries", "stream=codec_type,nb_read_packets,duration",
         "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    complaints = probe.stderr.strip().splitlines()
    if probe.returncode or complaints:
        raise ValueError("The output is damaged: "
                         + (complaints[-1] if complaints else "could not be read"))
    data = json.loads(probe.stdout)
    packets = {}
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        try:
            count = int(stream.get("nb_read_packets"))
        except (TypeError, ValueError):
            count = 0
        if kind in ("video", "audio") and count <= 0:
            raise ValueError(f"The output has an empty {kind} stream")
        packets[kind] = packets.get(kind, 0) + count
    # One frame of slack: the last progress line can be written before the
    # muxer flushes its final packet.
    if expected_frames and packets.get("video", 0) + 1 < expected_frames:
        raise ValueError(f"The output holds {packets.get('video', 0)} video packets, "
                         f"but FFmpeg reported writing {expected_frames} frames")
    for start, length in sample_windows(media_duration(data)):
        if cancelled.is_set():
            raise InterruptedError("Cancelled before deleting the original")
        _decode_window(path, start, length, cancelled, ffmpeg=ffmpeg,
                       timeout=timeout if math.isinf(length) else 120)


def remove_original(source: str, identity: FileIdentity, outputs: list[str],
                    cancelled: threading.Event, *, expected_frames: int | None = None,
                    ffmpeg: str | None = None, ffprobe: str | None = None) -> None:
    """Delete the unchanged input, only after validating outputs this job owns.

    The deletion is permanent, so every precondition is re-checked after the
    checks that take time: the source must still be the file that was
    converted, and each output must still be the file that was validated. A
    source symlink is refused; its target must not be deleted accidentally.
    """
    if not outputs:
        raise ValueError("No validated output authorizes removing the original")
    output_identities = []
    for path in outputs:
        validate_output(path, source=source, ffprobe=ffprobe)
        output_identities.append(FileIdentity.capture(path))
        verify_integrity(path, cancelled, ffprobe=ffprobe, ffmpeg=ffmpeg,
                         expected_frames=expected_frames if len(outputs) == 1 else None)
    if cancelled.is_set():
        raise InterruptedError("Cancelled before deleting the original")
    if FileIdentity.capture(source) != identity:
        raise ValueError("The original changed while the conversion was running")
    if any(FileIdentity.capture(p) != expected for p, expected in zip(outputs, output_identities)):
        raise ValueError("An output changed after validation")
    os.unlink(source)


def publish_output(staged: str, destination: str) -> None:
    """Atomic, no-clobber publication on the same filesystem.

    The name is claimed with an exclusive create and then renamed over, rather
    than hard-linked: FAT, exFAT and most FUSE mounts reject link(2), and a
    destination on a memory stick would otherwise discard a finished encode.
    """
    if Path(staged).parent.stat().st_dev != Path(destination).parent.stat().st_dev:
        raise ValueError("Staging and destination must be on the same filesystem")
    os.close(os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    try:
        os.replace(staged, destination)
    except OSError:
        os.unlink(destination)
        raise


def discard_job_paths(workspace: str | None = None) -> None:
    """Drop the private workspace a killed job announced as its own.

    Waiting longer for a child to tidy up is a guess; removing the directory
    it told us about is not. The name is checked so a malformed announcement
    cannot point this at anything else.
    """
    if workspace and os.path.basename(workspace).startswith(".bvc."):
        shutil.rmtree(workspace, ignore_errors=True)
