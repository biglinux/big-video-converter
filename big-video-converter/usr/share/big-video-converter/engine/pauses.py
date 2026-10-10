"""Pauses in speech, found on audio cleaned by DPDFNet.

Background noise fills the pauses of a raw recording. DPDFNet at full strength
leaves it near -70 dBFS, so a threshold set from the speech level separates the
words from the pauses between them. The analysis is mono: a pause on one
channel only, such as an interview recorded on separate channels, reads as
sound.
"""

import logging
import math
import os
import signal
import subprocess
import threading
from array import array
from collections import OrderedDict, deque

from engine.media_validation import terminate_process_group

logger = logging.getLogger(__name__)

STEP = 0.01  # seconds per level: 480 samples at 48 kHz
KEEP = 0.15  # seconds of each pause kept beside speech, so words do not collide
PAUSE_LENGTHS = (0.5, 1.0, 2.0)

LEVEL = b"lavfi.astats.Overall.Peak_level="
# One peak level per 10 ms of mono audio, cleaned at the model's full strength.
PIPELINE = (
    '"$@" | "$DENOISER" --channels 1 --attenuation 48 | '
    '"$FFMPEG" -hide_banner -nostdin -v error -f f32le -ar 48000 -ac 1 -i pipe:0 '
    "-af asetnsamples=n=480:p=0,astats=metadata=1:reset=1:measure_perchannel=none:"
    "measure_overall=Peak_level,ametadata=mode=print:"
    "key=lavfi.astats.Overall.Peak_level:file=- -f null -"
)


class LevelReader:
    """Collect the levels from FFmpeg's metadata lines, split across chunks."""

    def __init__(self, duration=0, progress=None):
        self.levels = array("f")
        self.duration = duration
        self.progress = progress
        self._rest = b""

    def consume(self, data):
        lines = (self._rest + data).split(b"\n")
        self._rest = lines.pop()
        for line in lines:
            if line.startswith(LEVEL):
                level = float(line[len(LEVEL) :])
                if math.isnan(level):
                    raise ValueError("FFmpeg reported a level that is not a number")
                self.levels.append(level)
        if self.progress and self.duration:
            self.progress(min(1.0, len(self.levels) * STEP / self.duration))


def find_pauses(levels, min_pause):
    """The pauses of at least ``min_pause`` seconds, less KEEP beside speech.

    Silence is anything 35 dB under the loud end of the speech, bounded so a
    quiet recording still has a floor and a loud one cannot lose soft words.
    """
    if not levels:
        return []
    ordered = sorted(levels)
    threshold = min(-40.0, max(-65.0, ordered[int(0.95 * (len(ordered) - 1))] - 35))
    end = len(levels)
    pauses = []

    def add(first, last):
        if (last - first) * STEP < min_pause:
            return
        start = first * STEP + (KEEP if first else 0)
        stop = last * STEP - (KEEP if last < end else 0)
        pauses.append({"start": round(start, 3), "stop": round(stop, 3)})

    first = None
    for index, level in enumerate(levels):
        if level < threshold:
            if first is None:
                first = index
        elif first is not None:
            add(first, index)
            first = None
    if first is not None:
        add(first, end)
    return pauses


class PauseFinder:
    """Measure one audio track at a time off the GTK thread; keep the last four.

    Callbacks run on the GTK thread and never for a superseded analysis.
    """

    CACHE_ENTRIES = 4

    def __init__(self):
        self._lock = threading.Lock()
        self._generation = 0
        self._process = None
        self._thread = None
        self._cache = OrderedDict()

    @staticmethod
    def identity(path, audio_index):
        stat = os.stat(path)
        return (
            path,
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            audio_index,
        )

    def cached(self, key):
        with self._lock:
            levels = self._cache.get(key)
            if levels is not None:
                self._cache.move_to_end(key)
            return levels

    @property
    def busy(self):
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._process is not None
        )

    def start(self, key, ffmpeg, denoiser, duration, progress, done, failed):
        """Measure audio track ``key[-1]`` (0-based) of the file ``key[0]``."""
        self.cancel()
        with self._lock:
            generation = self._generation
        shown = [-1]

        def report(fraction):
            percent = int(fraction * 100)
            if percent != shown[0]:
                shown[0] = percent
                self._deliver(generation, progress, fraction)

        decode = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-i",
            key[0],
            "-map",
            f"0:a:{key[-1]}",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_f32le",
            "-f",
            "f32le",
            "pipe:1",
        ]

        def work():
            reader = LevelReader(duration, report)
            errors = deque(maxlen=16)  # the last 64 KiB of diagnostics
            try:
                with self._lock:
                    if generation != self._generation:
                        return
                    process = self._process = subprocess.Popen(
                        ["bash", "-o", "pipefail", "-c", PIPELINE, "bash", *decode],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                        env=os.environ | {"DENOISER": denoiser, "FFMPEG": ffmpeg},
                    )
                # The diagnostics are drained apart, so a chatty error cannot
                # fill its pipe and stall the levels.
                drain = threading.Thread(
                    target=lambda: errors.extend(
                        iter(lambda: process.stderr.read1(4096), b"")
                    )
                )
                drain.start()
                try:
                    for chunk in iter(lambda: process.stdout.read1(65536), b""):
                        reader.consume(chunk)
                    returncode = process.wait()
                finally:
                    if process.poll() is None:
                        terminate_process_group(process, grace=1)
                    drain.join()
                    process.stdout.close()
                    process.stderr.close()
                with self._lock:
                    current = generation == self._generation
                    self._process = None
                if not current:
                    return
                if returncode:
                    message = (
                        b"".join(errors)[-4096:].decode("utf-8", "replace").strip()
                    )
                    raise OSError(message or f"exit status {returncode}")
            except Exception as exc:  # any failure must end the busy state it reports
                if not isinstance(exc, (OSError, ValueError)):
                    logger.exception("Pause analysis failed")
                with self._lock:
                    if self._process is not None and generation == self._generation:
                        self._process = None
                self._deliver(generation, failed, str(exc))
                return
            with self._lock:
                self._cache[key] = reader.levels
                while len(self._cache) > self.CACHE_ENTRIES:
                    self._cache.popitem(last=False)
            self._deliver(generation, done, key, reader.levels)

        self._thread = threading.Thread(target=work, name="video-pauses", daemon=True)
        self._thread.start()

    def _deliver(self, generation, callback, *args):
        from gi.repository import GLib

        def run():
            if generation == self._generation:
                callback(*args)
            return GLib.SOURCE_REMOVE

        # An explicit source: GLib.idle_add raised TypeError here intermittently.
        source = GLib.idle_source_new()
        source.set_callback(run)
        source.attach(GLib.MainContext.default())

    def cancel(self):
        """Stop at once; the worker reaps the process and drops its result."""
        with self._lock:
            self._generation += 1
            process, self._process = self._process, None
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
