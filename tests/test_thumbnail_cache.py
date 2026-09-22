from pathlib import Path
import hashlib
import os
import struct
import sys
import threading
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
SHARE = ROOT / "big-video-converter/usr/share/big-video-converter"
sys.path.insert(0, os.fspath(SHARE))

from utils.thumbnail_cache import FileIdentity, ThumbnailManager

PNG = b"\x89PNG\r\n\x1a\n"


def chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_png(path, metadata=None):
    raw = bytearray(PNG)
    raw += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    for key, value in (metadata or {}).items():
        raw += chunk(b"tEXt", key.encode("latin-1") + b"\0" + value.encode("latin-1"))
    raw += chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    raw += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def wait(event):
    assert event.wait(3), "thumbnail callback timed out"


def test_shared_desktop_thumbnail_is_reused(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    identity = FileIdentity.from_path(video)
    digest = hashlib.md5(identity.uri.encode(), usedforsecurity=False).hexdigest()
    thumbnail = tmp_path / "thumbnails/large" / f"{digest}.png"
    write_png(
        thumbnail,
        {
            "Thumb::URI": identity.uri,
            "Thumb::MTime": str(identity.mtime_ns // 1_000_000_000),
        },
    )
    event = threading.Event()
    values = []
    manager = ThumbnailManager(cache_home=tmp_path, ffmpeg="/missing")
    manager.request(video, lambda value: (values.append(value), event.set()))
    wait(event)
    manager.shutdown()
    assert values == [os.fspath(thumbnail)]


def test_identical_requests_are_deduplicated(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    manager = ThumbnailManager(cache_home=tmp_path, ffmpeg="/missing", max_workers=1)
    calls = []

    def generate(job, target):
        calls.append(job.identity.key)
        time.sleep(0.05)
        write_png(target)
        return target

    monkeypatch.setattr(manager, "_generate", generate)
    event = threading.Event()
    values = []

    def callback(value):
        values.append(value)
        if len(values) == 2:
            event.set()

    manager.request(video, callback)
    manager.request(video, callback)
    wait(event)
    manager.shutdown()
    assert len(calls) == 1
    assert values[0] == values[1]


def test_last_cancelled_subscriber_suppresses_callback(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    manager = ThumbnailManager(cache_home=tmp_path, ffmpeg="/missing", max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def generate(job, target):
        started.set()
        release.wait(2)
        if job.cancel_event.is_set():
            return None
        write_png(target)
        return target

    monkeypatch.setattr(manager, "_generate", generate)
    values = []
    request = manager.request(video, values.append)
    wait(started)
    request.cancel()
    release.set()
    time.sleep(0.1)
    manager.shutdown()
    assert values == []
