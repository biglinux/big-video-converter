"""Pauses found on DPDFNet-cleaned speech become cuts that keep a little air."""

import re
import shutil
import subprocess
import time

import pytest
from engine.pauses import KEEP, STEP, PauseFinder, find_pauses
from gi.repository import GLib


def levels(*runs):
    """An envelope from (level dB, seconds) runs."""
    return [level for level, seconds in runs for _ in range(round(seconds / STEP))]


def test_pauses_keep_air_beside_speech_but_not_at_the_ends():
    envelope = levels((-80, 1), (-6, 2), (-70, 1.5), (-6, 2), (-80, 1))
    assert find_pauses(envelope, 0.5) == [
        {"start": 0.0, "stop": 1 - KEEP},
        {"start": 3 + KEEP, "stop": 4.5 - KEEP},
        {"start": 6.5 + KEEP, "stop": 7.5},
    ]


def test_short_gaps_and_silence_free_audio_are_not_pauses():
    assert find_pauses(levels((-6, 1), (-70, 0.4), (-6, 1)), 0.5) == []
    assert find_pauses(levels((-6, 3)), 0.5) == []
    assert find_pauses([], 0.5) == []


def test_the_threshold_follows_the_speech_level_within_bounds():
    # Quiet speech at -40 dBFS keeps a -65 dBFS floor: DPDFNet's residue
    # (about -70) is a pause, a -60 dBFS murmur is not.
    assert find_pauses(levels((-40, 2), (-60, 1), (-40, 2)), 0.5) == []
    quiet = levels((-40, 2), (-70, 1), (-40, 2))
    assert find_pauses(quiet, 0.5) == [{"start": 2 + KEEP, "stop": 3 - KEEP}]
    # Loud speech cannot lift the threshold above -40 dBFS and eat soft words.
    assert find_pauses(levels((0, 2), (-38, 1), (0, 2)), 0.5) == []
    assert find_pauses(levels((float("-inf"), 2)), 0.5) == [{"start": 0.0, "stop": 2.0}]


def ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", *map(str, args)],
        capture_output=True,
        text=True,
        check=True,
    )


def wait_for(predicate, timeout=60):
    context = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "the analysis did not finish"
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


@pytest.mark.skipif(
    not (shutil.which("dpdfnet-enhance") and shutil.which("espeak-ng")),
    reason="needs dpdfnet-enhance and espeak-ng",
)
def test_a_pause_in_a_noisy_video_is_found_at_its_edges(tmp_path):
    for name, text in (
        ("one", "The quick brown fox jumps over the lazy dog."),
        ("two", "Then it runs back into the forest."),
    ):
        subprocess.run(
            ["espeak-ng", "-w", str(tmp_path / f"{name}.wav"), text], check=True
        )
    clean = tmp_path / "clean.wav"
    ffmpeg(
        "-v",
        "error",
        "-i",
        tmp_path / "one.wav",
        "-i",
        tmp_path / "two.wav",
        "-filter_complex",
        "[0]apad=pad_dur=1.5[a];[a][1]concat=v=0:a=1,aresample=48000",
        clean,
    )
    report = ffmpeg(
        "-i", clean, "-af", "silencedetect=noise=-50dB:d=0.5", "-f", "null", "-"
    ).stderr
    start = float(re.search(r"silence_start: (\S+)", report)[1])
    stop = float(re.search(r"silence_end: (\S+)", report)[1])
    # The speech is the second audio track, behind noise as loud as its quiet parts.
    video = tmp_path / "talk.mkv"
    ffmpeg(
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=64x36:rate=10",
        "-i",
        clean,
        "-filter_complex",
        "[1]asplit[keep][talk];anoisesrc=r=48000:a=0.03:seed=7[n];"
        "[talk][n]amix=normalize=0:duration=first[noisy]",
        "-map",
        "0:v",
        "-map",
        "[keep]",
        "-map",
        "[noisy]",
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "flac",
        video,
    )

    found = []
    finder = PauseFinder()
    finder.start(
        PauseFinder.identity(str(video), 1),
        "ffmpeg",
        shutil.which("dpdfnet-enhance"),
        0,
        lambda _f: None,
        lambda _key, levels: found.append(find_pauses(levels, 0.5)),
        lambda message: found.append(message),
    )
    wait_for(lambda: found)

    pauses = found[0]
    assert isinstance(pauses, list), pauses
    # Never into the speech, and close to KEEP from it.
    assert len(pauses) == 1
    assert start < pauses[0]["start"] <= start + KEEP + 0.05
    assert stop - KEEP - 0.05 <= pauses[0]["stop"] < stop


def test_cancel_stops_the_analysis_and_drops_its_result(media, tmp_path):
    # A denoiser that never answers keeps the analysis running until cancelled.
    stalled = tmp_path / "stalled-denoiser"
    stalled.write_text("#!/bin/sh\nexec sleep 30\n")
    stalled.chmod(0o755)
    reached = []
    finder = PauseFinder()
    finder.start(
        PauseFinder.identity(str(media["video"]), 0),
        "ffmpeg",
        str(stalled),
        3,
        lambda _f: None,
        lambda *args: reached.append(args),
        lambda message: reached.append(message),
    )
    wait_for(lambda: finder.busy)
    started = time.monotonic()
    finder.cancel()
    finder._thread.join(10)
    assert not finder._thread.is_alive()
    assert time.monotonic() - started < 3
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)
    assert reached == []
