"""Job-owned paths and media checks, independent of GTK widgets.

A successful exit and a non-empty file are necessary, not sufficient. In
particular, deletion must never discover an output by guessing its filename.
"""

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
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


def media_duration(data: dict) -> float | None:
    values = [data.get("format", {}).get("duration")]
    values.extend(s.get("duration") for s in data.get("streams", []))
    valid = []
    for value in values:
        try:
            value = float(value)
        except (ValueError, TypeError):
            continue
        if math.isfinite(value) and value > 0:
            valid.append(value)
    return max(valid) if valid else None


def stream_count(data: dict, kind: str) -> int:
    return sum(s.get("codec_type") == kind for s in data.get("streams", []))


def validate_output(path: str, *, source: str | None = None,
                    expected_duration: float | None = None,
                    expected_streams: dict | None = None,
                    duration_tolerance: float = 0.5,
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
        if actual is None or abs(actual - expected_duration) > duration_tolerance:
            raise ValueError("The output duration does not match the requested interval")
    if FileIdentity.capture(path) != identity:
        raise ValueError("The output changed during validation")
    return media


def terminate_process_group(process: subprocess.Popen, grace: float = 2.0) -> None:
    """Reap our child and terminate its entire private session, never the GUI."""
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


def verify_decoding(path: str, cancelled: threading.Event, *, timeout=3600,
                    ffmpeg: str | None = None) -> None:
    """An additional full decode check before a destructive operation."""
    with subprocess.Popen(
        [ffmpeg or get_ffmpeg_executable(), "-nostdin", "-v", "error", "-xerror",
         "-i", os.path.abspath(path), "-map", "0:v:0", "-map", "0:a?",
         "-f", "null", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    ) as process:
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancelled.wait(0.1):
                    raise InterruptedError("Cancelled before deleting the original")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Output decoding validation timed out")
            if process.returncode:
                raise ValueError("Output decoding validation failed")
        finally:
            if process.poll() is None:
                terminate_process_group(process)


def trash_original(source: str, identity: FileIdentity, outputs: list[str],
                   cancelled: threading.Event, *, ffmpeg: str | None = None) -> None:
    """Move the unchanged input to Trash, only after validating owned outputs.

    There is intentionally no permanent-unlink fallback if Trash is unavailable.
    A source symlink is refused; its target must not be deleted accidentally.
    """
    if not outputs:
        raise ValueError("No validated output authorizes removing the original")
    output_identities = []
    for path in outputs:
        validate_output(path, source=source)
        output_identities.append(FileIdentity.capture(path))
        verify_decoding(path, cancelled, ffmpeg=ffmpeg)
    if cancelled.is_set():
        raise InterruptedError("Cancelled before deleting the original")
    if FileIdentity.capture(source) != identity:
        raise ValueError("The original changed while the conversion was running")
    if any(FileIdentity.capture(p) != expected for p, expected in zip(outputs, output_identities)):
        raise ValueError("An output changed after validation")
    from gi.repository import Gio
    if not Gio.File.new_for_path(os.path.abspath(source)).trash(None):
        raise OSError("Could not move the original to Trash")


def publish_output(staged: str, destination: str) -> None:
    """Atomic, no-clobber publication on the same filesystem."""
    if Path(staged).parent.stat().st_dev != Path(destination).parent.stat().st_dev:
        raise ValueError("Staging and destination must be on the same filesystem")
    os.link(staged, destination, follow_symlinks=False)
