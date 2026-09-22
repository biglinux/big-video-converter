"""Bounded asynchronous video-thumbnail service.

The service reuses the freedesktop thumbnail cache first and falls back to a
private cache.  Worker threads never touch GTK; callers marshal callbacks to
GLib's main context before changing widgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import os
from pathlib import Path
import queue
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable

logger = logging.getLogger(__name__)
_PNG = b"\x89PNG\r\n\x1a\n"
_SHARED_SIZES = ("xx-large", "x-large", "large", "normal")


@dataclass(frozen=True, slots=True)
class FileIdentity:
    path: str
    uri: str
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_path(cls, value: str | os.PathLike[str]) -> "FileIdentity":
        path = Path(value).expanduser().resolve(strict=True)
        stat = path.stat()
        if not path.is_file():
            raise OSError(f"Not a regular file: {path}")
        return cls(
            os.fspath(path),
            path.as_uri(),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            int(stat.st_ctime_ns),
        )

    @property
    def key(self) -> str:
        raw = f"{self.uri}\0{self.size}\0{self.mtime_ns}\0{self.ctime_ns}"
        return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()


class ThumbnailRequest:
    __slots__ = ("_manager", "_key", "_token", "_cancelled")

    def __init__(self, manager=None, key="", token=""):
        self._manager = manager
        self._key = key
        self._token = token
        self._cancelled = False

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        manager, self._manager = self._manager, None
        if manager is not None:
            manager._cancel(self._key, self._token)


@dataclass(slots=True)
class _Job:
    identity: FileIdentity
    callbacks: dict[str, Callable[[str | None], None]] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None


class ThumbnailManager:
    """Deduplicated service with a small fixed worker pool."""

    def __init__(self, *, cache_home=None, ffmpeg=None, max_workers=2, timeout=20.0):
        if not 1 <= int(max_workers) <= 4:
            raise ValueError("max_workers must be between 1 and 4")
        base = Path(
            cache_home
            or os.environ.get("XDG_CACHE_HOME")
            or Path.home() / ".cache"
        ).expanduser()
        self._shared_roots = (base / "thumbnails", Path.home() / ".thumbnails")
        self._private_dir = base / "big-video-converter" / "thumbnails"
        self._private_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._private_dir.chmod(0o700)
        except OSError:
            pass
        self._ffmpeg = ffmpeg
        self._timeout = float(timeout)
        self._lock = threading.RLock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._jobs: dict[str, _Job] = {}
        self._closed = False
        self._pruned = False
        self._workers = []
        for index in range(int(max_workers)):
            worker = threading.Thread(
                target=self._worker,
                name=f"bvc-thumbnail-{index + 1}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def request(self, file_path, callback) -> ThumbnailRequest:
        if not callable(callback):
            raise TypeError("callback must be callable")
        try:
            identity = FileIdentity.from_path(file_path)
        except OSError:
            callback(None)
            return ThumbnailRequest()
        key = identity.key
        token = uuid.uuid4().hex
        with self._lock:
            if self._closed:
                callback(None)
                return ThumbnailRequest()
            job = self._jobs.get(key)
            new_job = job is None
            if job is None:
                job = _Job(identity)
                self._jobs[key] = job
            job.callbacks[token] = callback
        if new_job:
            self._queue.put(key)
        return ThumbnailRequest(self, key, token)

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            job.callbacks.clear()
            job.cancel_event.set()
            self._terminate(job.process)
        for _ in self._workers:
            self._queue.put(None)

    def _cancel(self, key: str, token: str) -> None:
        with self._lock:
            job = self._jobs.get(key)
            if job is None:
                return
            job.callbacks.pop(token, None)
            if job.callbacks:
                return
            job.cancel_event.set()
            self._terminate(job.process)

    def _worker(self) -> None:
        while True:
            key = self._queue.get()
            try:
                if key is None:
                    return
                self._run(key)
            finally:
                self._queue.task_done()

    def _run(self, key: str) -> None:
        with self._lock:
            job = self._jobs.get(key)
        if job is None or job.cancel_event.is_set():
            self._finish(key, None)
            return
        result = None
        try:
            self._prune_once()
            shared = self._find_shared(job.identity)
            if shared is not None:
                result = os.fspath(shared)
            else:
                private = self._private_dir / f"{job.identity.key}.png"
                if self._valid_png(private):
                    result = os.fspath(private)
                else:
                    generated = self._generate(job, private)
                    result = os.fspath(generated) if generated else None
        except Exception:
            logger.exception("Thumbnail worker failed")
        self._finish(key, result)

    def _finish(self, key: str, result: str | None) -> None:
        with self._lock:
            job = self._jobs.pop(key, None)
        if job is None or job.cancel_event.is_set():
            return
        for callback in list(job.callbacks.values()):
            try:
                callback(result)
            except Exception:
                logger.exception("Thumbnail callback failed")

    def _find_shared(self, identity: FileIdentity) -> Path | None:
        digest = hashlib.md5(
            identity.uri.encode("utf-8", "surrogatepass"),
            usedforsecurity=False,
        ).hexdigest()
        expected_mtime = str(identity.mtime_ns // 1_000_000_000)
        for root in self._shared_roots:
            for size in _SHARED_SIZES:
                candidate = root / size / f"{digest}.png"
                if not self._valid_png(candidate):
                    continue
                metadata = _png_text(candidate)
                if metadata.get("Thumb::URI") != identity.uri:
                    continue
                if metadata.get("Thumb::MTime") != expected_mtime:
                    continue
                return candidate
        return None

    def _generate(self, job: _Job, target: Path) -> Path | None:
        ffmpeg = self._ffmpeg or shutil.which("ffmpeg")
        if not ffmpeg or job.cancel_event.is_set():
            return None
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}-", suffix=".png", dir=self._private_dir
        )
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            vf = (
                "scale=288:162:force_original_aspect_ratio=decrease,"
                "pad=288:162:(ow-iw)/2:(oh-ih)/2:color=black"
            )
            for seek in ("1", "0"):
                command = [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-ss", seek, "-i", job.identity.path, "-map", "0:V:0",
                    "-frames:v", "1", "-an", "-sn", "-dn", "-vf", vf,
                    "-y", os.fspath(temporary),
                ]
                if self._supervise(job, command) and self._valid_png(temporary):
                    temporary.chmod(0o600)
                    os.replace(temporary, target)
                    return target
                temporary.unlink(missing_ok=True)
                temporary.touch(mode=0o600)
            return None
        finally:
            temporary.unlink(missing_ok=True)

    def _supervise(self, job: _Job, command: list[str]) -> bool:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        with self._lock:
            job.process = process
        deadline = time.monotonic() + self._timeout
        try:
            while process.poll() is None:
                if job.cancel_event.wait(0.05) or time.monotonic() >= deadline:
                    self._terminate(process)
                    return False
            return process.returncode == 0
        finally:
            with self._lock:
                if job.process is process:
                    job.process = None
            if process.poll() is None:
                self._terminate(process)

    @staticmethod
    def _terminate(process) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                process.terminate()
            except ProcessLookupError:
                return
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            process.wait(timeout=1)

    def _prune_once(self) -> None:
        with self._lock:
            if self._pruned:
                return
            self._pruned = True
        now = time.time()
        entries = []
        for path in self._private_dir.glob("*.png"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime > 90 * 86400:
                path.unlink(missing_ok=True)
            else:
                entries.append((stat.st_mtime_ns, path))
        entries.sort(reverse=True)
        for _, path in entries[512:]:
            path.unlink(missing_ok=True)

    @staticmethod
    def _valid_png(path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return handle.read(8) == _PNG
        except OSError:
            return False


def _png_text(path: Path) -> dict[str, str]:
    result = {}
    try:
        with path.open("rb") as handle:
            if handle.read(8) != _PNG:
                return result
            for _ in range(4096):
                raw_length = handle.read(4)
                if len(raw_length) != 4:
                    break
                length = struct.unpack(">I", raw_length)[0]
                kind = handle.read(4)
                if length > 16 * 1024 * 1024:
                    return {}
                data = handle.read(length)
                if len(data) != length or len(handle.read(4)) != 4:
                    return {}
                if kind == b"tEXt" and b"\0" in data:
                    key, value = data.split(b"\0", 1)
                    result[key.decode("latin-1")] = value.decode("latin-1")
                if kind == b"IEND":
                    break
    except OSError:
        return {}
    return result
