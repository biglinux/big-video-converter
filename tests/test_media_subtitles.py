import errno
import os
import shutil
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from engine import media_validation as mv
from engine.subtitle_clipping import (
    SubtitleClipper,
    _clip,
    _format_timecode,
    _milliseconds,
    _read_cues,
    _serialize,
)


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (1000, 2000, [(0, 1000, "speech")]),
        (0, 1000, [(500, 1000, "speech")]),
        (2000, 3000, [(0, 500, "speech")]),
        (2500, 3000, []),
        (0, 500, []),
    ],
)
def test_subtitles_intersect_both_boundaries(start, end, expected):
    assert _clip([(500, 2500, "speech")], start, end, 0) == expected


def test_srt_rounding_carries_and_renumbers(tmp_path):
    assert _format_timecode(_milliseconds("59.9999")) == "00:01:00,000"
    path = tmp_path / "captions.srt"
    path.write_text("\ufeff1\r\n00:00:00,000 --> 00:00:01,000\r\nFala\r\n")
    cues = _read_cues(path)
    text = _serialize(cues + [(1000, 2000, "Outra")])
    assert "\n\n2\n" in text
    assert cues == [(0, 1000, "Fala")]


def test_same_language_tracks_get_distinct_sidecars(media, tmp_path, monkeypatch):
    clipper = SubtitleClipper(
        str(media["multi"]),
        str(tmp_path),
        "joined.mkv",
        [{"start": 0.5, "end": 1.5}, {"start": 2, "end": 2.5}],
        str(tmp_path),
        "extract",
    )
    calls = []
    import engine.subtitle_clipping as module

    run = module.subprocess.Popen

    def record(cmd, *args, **kwargs):
        if "-c:s" in cmd:
            calls.append(cmd)
        return run(cmd, *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", record)
    clipper.run()
    files = sorted(tmp_path.glob("joined.por*.srt"))
    assert [path.name for path in files] == ["joined.por.forced.srt", "joined.por.srt"]
    assert len(calls) == 2
    assert files[0].read_bytes() == files[1].read_bytes()
    cues = _read_cues(files[0])
    assert cues[0][0] == 0
    assert max(cue[1] for cue in cues) <= 1500


@pytest.mark.parametrize(
    "destination,expected",
    [
        ("out.mkv", 2),
        ("out.mp4", 1),
        ("out.mov", 1),
        ("out.webm", 1),
        (None, 2),
    ],
)
def test_container_bound_subtitle_expectation(destination, expected):
    """A bitmap track the script skips in MP4 must not be expected in the
    output, or a good conversion is reported as failed."""
    from engine.conversion import _expected_subtitles

    data = {
        "streams": [
            {"codec_type": "video"},
            {"codec_type": "subtitle", "codec_name": "subrip"},
            {"codec_type": "subtitle", "codec_name": "dvd_subtitle"},
        ]
    }
    assert _expected_subtitles(data, destination) == expected


def test_nonempty_garbage_output_is_rejected(tmp_path):
    partial = tmp_path / "partial.mkv"
    partial.write_bytes(b"not media but nonempty")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        mv.validate_output(str(partial))


def test_validation_checks_duration_identity_and_streams(media, tmp_path):
    out = tmp_path / "out.mp4"
    shutil.copyfile(media["video"], out)
    data = mv.validate_output(
        str(out),
        source=str(media["video"]),
        expected_duration=3,
        expected_streams={"video": 1, "audio": 1, "subtitle": 0},
    )
    assert mv.stream_count(data, "audio") == 1
    with pytest.raises(ValueError):
        mv.validate_output(str(out), expected_duration=30)
    with pytest.raises(ValueError):
        mv.validate_output(str(out), expected_streams={"audio": 2})
    with pytest.raises(ValueError):
        mv.validate_output(str(media["video"]), source=str(media["video"]))
    link = tmp_path / "link.mp4"
    link.symlink_to(out)
    with pytest.raises(ValueError):
        mv.validate_output(str(link))


def test_no_clobber_publication(tmp_path):
    staged, dest = tmp_path / "stage", tmp_path / "dest"
    staged.write_bytes(b"new")
    dest.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        mv.publish_output(str(staged), str(dest))
    assert dest.read_bytes() == b"old"
    dest.unlink()
    mv.publish_output(str(staged), str(dest))
    assert dest.read_bytes() == b"new"


def test_swapped_parent_keeps_other_files(tmp_path, monkeypatch):
    """A destination folder renamed and replaced by a symlink mid-publication."""
    folder, private = tmp_path / "out", tmp_path / "private"
    folder.mkdir()
    private.mkdir()
    (private / "dest").write_bytes(b"private")
    staged = tmp_path / "stage"
    staged.write_bytes(b"new")

    def swap_then_fail(*_args, **_kwargs):
        folder.rename(tmp_path / "moved")
        folder.symlink_to(private)
        raise OSError("replace failed")

    monkeypatch.setattr(mv.os, "replace", swap_then_fail)
    with pytest.raises(OSError):
        mv.publish_output(str(staged), str(folder / "dest"))
    assert (private / "dest").read_bytes() == b"private"
    assert not (tmp_path / "moved" / "dest").exists()


def test_permanent_removal_keeps_other_files(tmp_path, media, monkeypatch):
    """The source folder renamed and replaced by a symlink just before deletion."""
    folder, private = tmp_path / "in", tmp_path / "private"
    folder.mkdir()
    private.mkdir()
    source, output = folder / "clip.mp4", tmp_path / "clip-out.mp4"
    for path in (source, output, private / "clip.mp4"):
        shutil.copy(media["video"], path)
    identity = mv.FileIdentity.capture(str(source))
    real_unlink = mv.os.unlink

    def swap_then_unlink(*args, **kwargs):
        folder.rename(tmp_path / "moved")
        folder.symlink_to(private)
        real_unlink(*args, **kwargs)

    monkeypatch.setattr(mv.os, "unlink", swap_then_unlink)
    mv.remove_original(
        str(source), identity, [str(output)], threading.Event(), permanent=True
    )
    assert (private / "clip.mp4").exists()
    assert not (tmp_path / "moved" / "clip.mp4").exists()


class TestBoundPublication:
    def test_success_stays_in_opened_directory_after_parent_swap(self, tmp_path, monkeypatch):
        folder, private, moved = tmp_path / "out", tmp_path / "private", tmp_path / "moved"
        folder.mkdir()
        private.mkdir()
        (private / "dest").write_bytes(b"private")
        staged = tmp_path / "stage"
        staged.write_bytes(b"new")
        opened = []
        real_open_parent = mv._open_parent

        def swap_after_open(path):
            parent = real_open_parent(path)
            opened.append(parent)
            folder.rename(moved)
            folder.symlink_to(private, target_is_directory=True)
            return parent

        monkeypatch.setattr(mv, "_open_parent", swap_after_open)
        mv.publish_output(str(staged), str(folder / "dest"))
        assert (moved / "dest").read_bytes() == b"new"
        assert (private / "dest").read_bytes() == b"private"
        assert not staged.exists()
        with pytest.raises(OSError) as error:
            os.fstat(opened[0])
        assert error.value.errno == errno.EBADF

    @pytest.mark.parametrize("failure", ["different-device", "replace", "missing-stage", "claimed"])
    def test_failure_preserves_files_and_closes_parent(self, tmp_path, monkeypatch, failure):
        staged, dest = tmp_path / "stage", tmp_path / "dest"
        if failure != "missing-stage":
            staged.write_bytes(b"new")
        opened = []
        real_open_parent = mv._open_parent

        def record_open(path):
            parent = real_open_parent(path)
            opened.append(parent)
            return parent

        monkeypatch.setattr(mv, "_open_parent", record_open)
        expected_error = OSError
        if failure == "different-device":
            device = tmp_path.stat().st_dev
            monkeypatch.setattr(mv.os, "fstat", lambda fd: SimpleNamespace(st_dev=device + 1))
            expected_error = ValueError
        elif failure == "replace":
            monkeypatch.setattr(mv.os, "replace", Mock(side_effect=OSError("replace failed")))
        elif failure == "claimed":
            dest.write_bytes(b"existing")
            expected_error = FileExistsError

        with pytest.raises(expected_error):
            mv.publish_output(str(staged), str(dest))
        if failure == "claimed":
            assert dest.read_bytes() == b"existing"
        else:
            assert not dest.exists()
        if failure != "missing-stage":
            assert staged.read_bytes() == b"new"
        assert len(opened) == 1
        with pytest.raises(OSError) as error:
            os.stat(opened[0])
        assert error.value.errno == errno.EBADF

    def test_destination_symlink_is_not_followed_or_replaced(self, tmp_path):
        staged, destination, target = tmp_path / "stage", tmp_path / "dest", tmp_path / "target"
        staged.write_bytes(b"new")
        target.write_bytes(b"private")
        destination.symlink_to(target)
        with pytest.raises(FileExistsError):
            mv.publish_output(str(staged), str(destination))
        assert destination.is_symlink()
        assert target.read_bytes() == b"private"
        assert staged.read_bytes() == b"new"

    def test_relative_destination_uses_current_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        staged = tmp_path / "stage"
        staged.write_bytes(b"new")
        mv.publish_output("stage", "dest")
        assert (tmp_path / "dest").read_bytes() == b"new"
        assert not staged.exists()


class TestPermanentDirectoryRemoval:
    @pytest.fixture
    def paths(self, tmp_path, monkeypatch):
        folder = tmp_path / "input"
        folder.mkdir()
        source, output = folder / "source", tmp_path / "output"
        source.write_bytes(b"original")
        output.write_bytes(b"converted")
        # Isolate directory-bound deletion from the unchanged FFmpeg checks.
        monkeypatch.setattr(mv, "validate_output", Mock())
        monkeypatch.setattr(mv, "verify_integrity", Mock())
        return source, output, mv.FileIdentity.capture(str(source))

    @pytest.mark.parametrize("swap_parent", [False, True])
    def test_deletion_stays_in_opened_directory(self, paths, monkeypatch, swap_parent):
        source, output, identity = paths
        private = output.with_name("private")
        private.mkdir()
        (private / source.name).write_bytes(b"private")
        moved = output.with_name("moved")
        opened = []
        real_open_parent = mv._open_parent

        def open_then_swap(path):
            parent = real_open_parent(path)
            opened.append(parent)
            if swap_parent:
                source.parent.rename(moved)
                source.parent.symlink_to(private, target_is_directory=True)
            return parent

        monkeypatch.setattr(mv, "_open_parent", open_then_swap)
        mv.remove_original(str(source), identity, [str(output)], threading.Event(), permanent=True)
        assert not ((moved / source.name) if swap_parent else source).exists()
        assert (private / source.name).read_bytes() == b"private"
        assert output.read_bytes() == b"converted"
        with pytest.raises(OSError) as error:
            os.fstat(opened[0])
        assert error.value.errno == errno.EBADF

    @pytest.mark.parametrize("replacement", ["file", "symlink", "missing"])
    def test_source_changed_before_open_is_preserved(self, paths, monkeypatch, replacement):
        source, output, identity = paths
        saved = source.with_name("saved")
        opened = []
        real_open_parent = mv._open_parent

        def change_before_open(path):
            source.rename(saved)
            if replacement == "file":
                source.write_bytes(b"replacement")
            elif replacement == "symlink":
                source.symlink_to(output)
            parent = real_open_parent(path)
            opened.append(parent)
            return parent

        monkeypatch.setattr(mv, "_open_parent", change_before_open)
        expected = FileNotFoundError if replacement == "missing" else ValueError
        with pytest.raises(expected):
            mv.remove_original(str(source), identity, [str(output)], threading.Event(), permanent=True)
        assert saved.read_bytes() == b"original"
        assert output.read_bytes() == b"converted"
        if replacement == "file":
            assert source.read_bytes() == b"replacement"
        elif replacement == "symlink":
            assert source.is_symlink()
        with pytest.raises(OSError) as error:
            os.fstat(opened[0])
        assert error.value.errno == errno.EBADF

    def test_unlink_failure_keeps_original_and_closes_parent(self, paths, monkeypatch):
        source, output, identity = paths
        real_open_parent = mv._open_parent
        opened = []

        def record_open(path):
            parent = real_open_parent(path)
            opened.append(parent)
            return parent

        monkeypatch.setattr(mv, "_open_parent", record_open)
        monkeypatch.setattr(mv.os, "unlink", Mock(side_effect=PermissionError("denied")))
        with pytest.raises(PermissionError, match="denied"):
            mv.remove_original(str(source), identity, [str(output)], threading.Event(), permanent=True)
        assert source.read_bytes() == b"original"
        assert output.read_bytes() == b"converted"
        with pytest.raises(OSError) as error:
            os.fstat(opened[0])
        assert error.value.errno == errno.EBADF


def test_only_job_workspaces_are_discarded(tmp_path):
    workspace = tmp_path / ".bvc-12345"
    workspace.mkdir()
    (workspace / "video.mkv").write_bytes(b"partial")
    other = tmp_path / "not-ours"
    other.mkdir()
    mv.discard_job_paths(str(workspace))
    assert not workspace.exists()
    mv.discard_job_paths(str(other))
    assert other.exists()


def test_changed_or_cancelled_original_is_kept(media, tmp_path, monkeypatch):
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    shutil.copyfile(media["video"], src)
    shutil.copyfile(src, out)
    identity = mv.FileIdentity.capture(str(src))
    monkeypatch.setattr(mv, "verify_integrity", lambda *a, **kw: None)
    with src.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="original changed"):
        mv.remove_original(str(src), identity, [str(out)], threading.Event())
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        mv.remove_original(
            str(src), mv.FileIdentity.capture(str(src)), [str(out)], event
        )
    assert src.exists()


def test_removal_requires_a_validated_output(media, tmp_path):
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    shutil.copyfile(media["video"], src)
    shutil.copyfile(src, out)
    identity = mv.FileIdentity.capture(str(src))
    with pytest.raises(ValueError, match="No validated output"):
        mv.remove_original(str(src), identity, [], threading.Event())
    assert src.exists()
    mv.remove_original(str(src), identity, [str(out)], threading.Event())
    assert not src.exists() and out.exists()


def test_integrity_check_accepts_a_complete_file(media):
    mv.verify_integrity(str(media["video"]), threading.Event())


@pytest.mark.parametrize("container,fraction", [("mp4", 0.75), ("mkv", 0.60)])
def test_integrity_check_rejects_a_truncated_output(
    media, tmp_path, container, fraction
):
    """A short Matroska file still probes successfully and exits 0."""
    whole = tmp_path / f"whole.{container}"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(media["video"]),
            "-c",
            "copy",
            "-y",
            str(whole),
        ],
        check=True,
        timeout=30,
    )
    frames = int(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-show_entries",
                "stream=nb_read_packets",
                "-of",
                "csv=p=0",
                str(whole),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    )
    cut = tmp_path / f"cut.{container}"
    cut.write_bytes(whole.read_bytes()[: int(whole.stat().st_size * fraction)])
    with pytest.raises(ValueError):
        mv.verify_integrity(str(cut), threading.Event(), expected_frames=frames)


def test_short_or_unmeasured_media_decodes_fully():
    for duration in (None, 0.0, float("inf"), 3.0, mv.FULL_DECODE_SECONDS):
        assert mv.sample_windows(duration) == ((0.0, float("inf")),)


def test_long_media_samples_bounded_windows():
    for duration in (60.0, 3600.0, 36000.0):
        windows = mv.sample_windows(duration)
        assert len(windows) == 5 and sum(length for _, length in windows) == 7.0
        assert windows[0][0] == 0.0 and windows[1][0] == duration - 2.0
        assert all(0.0 <= start <= duration - length for start, length in windows)


@pytest.mark.parametrize(
    "options,limit",
    [
        (["-ss", "00:00:00.500", "-t", "1"], "1.500"),
        (["-ss", "500ms", "-to", "1.5s"], "1.5"),
        (["-ss", "1"], ""),
    ],
)
def test_packet_clip_uses_only_parsed_timestamps(options, limit):
    from engine.subtitle_timing import clipping_options

    end, filters = clipping_options(options)
    assert end == limit and "noise=amount=0:" in filters and "setts=pts=" in filters


@pytest.mark.parametrize(
    "value", ["nan", "inf", "-1", "00:99:00", "1+2", "0');movie=evil(", ""]
)
def test_timestamp_expressions_are_rejected(value):
    from engine.subtitle_timing import seconds

    with pytest.raises(ValueError):
        seconds(value)


@pytest.mark.parametrize("extension", ["mkv", "mp4"])
def test_trim_clips_crossing_embedded_subtitle(media, tmp_path, run_cli, extension):
    out = tmp_path / f"clipped.{extension}"
    result = run_cli(media["multi"], out, options="-ss 0.5 -t 1 -threads 1")
    assert result.returncode == 0, result.stderr[-3000:]
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(out),
            "-map",
            "0:s:0",
            "-c:s",
            "srt",
            "-f",
            "srt",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "00:00:00,000 --> 00:00:01,000" in result.stdout
    assert "Primeira fala" in result.stdout and "Segunda fala" not in result.stdout


def test_extracted_subtitle_uses_same_trim_interval(media, tmp_path, run_cli):
    result = run_cli(
        media["multi"],
        tmp_path / "extract.mp4",
        only_extract_subtitles="1",
        options="-ss 0.5 -t 1",
    )
    assert result.returncode == 0, result.stderr[-2000:]
    outputs = sorted(tmp_path.glob("extract.por*.srt"))
    assert [p.name for p in outputs] == ["extract.por.forced.srt", "extract.por.srt"]
    assert not (tmp_path / "extract.mp4").exists()
    for path in outputs:
        text = path.read_text()
        assert "00:00:00,000 --> 00:00:01,000" in text and "Segunda fala" not in text


def test_trash_failure_preserves_original(media, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from gi.repository import GLib

    def refuse(_cancellable):
        raise GLib.Error("no Trash on this device")

    source, output = tmp_path / "source.mp4", tmp_path / "output.mp4"
    shutil.copyfile(media["video"], source)
    shutil.copyfile(source, output)
    fake = SimpleNamespace(new_for_path=lambda _path: SimpleNamespace(trash=refuse))
    monkeypatch.setattr(mv.Gio, "File", fake)
    with pytest.raises(OSError, match="could not be moved to the Trash"):
        mv.remove_original(
            str(source),
            mv.FileIdentity.capture(str(source)),
            [str(output)],
            threading.Event(),
        )
    assert source.read_bytes() == media["video"].read_bytes()


def test_cancel_during_packet_count_reaps_probe(tmp_path, monkeypatch):
    import sys
    import time

    cancel = threading.Event()
    real_popen = subprocess.Popen
    children = []

    def slow_probe(*args, **kwargs):
        process = real_popen(
            [sys.executable, "-c", "import time; time.sleep(30)"], **kwargs
        )
        children.append(process)
        cancel.set()
        return process

    monkeypatch.setattr(mv.subprocess, "Popen", slow_probe)
    started = time.monotonic()
    try:
        with pytest.raises(InterruptedError):
            mv.verify_integrity(str(tmp_path / "output.mp4"), cancel, timeout=2)
        assert time.monotonic() - started < 5
        assert children and all(child.poll() is not None for child in children)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()


def test_taken_segment_sidecar_gets_next_number(media, tmp_path):
    existing = tmp_path / "joined.por.srt"
    existing.write_text("Manually corrected subtitles", encoding="utf-8")
    clipper = SubtitleClipper(
        str(media["multi"]),
        str(tmp_path),
        "joined.mkv",
        [{"start": 0, "end": 2}],
        str(tmp_path),
        "extract",
    )
    clipper.run()
    assert existing.read_text() == "Manually corrected subtitles"
    assert sorted(path.rpartition("/")[2] for path in clipper.created_files) == [
        "joined.por.forced.srt",
        "joined.por2.srt",
    ]
    assert not list(tmp_path.glob(".bvc-subtitle-*"))


def test_cancel_ends_a_helper_at_once():
    import sys
    import time

    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        mv.run_cancellable(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cancel,
            timeout=60,
            task="testing",
        )
    assert time.monotonic() - started < 3


def test_dropped_picture_subtitles_block_deletion(tmp_path):
    from engine.segment_batch import _left_out_subtitles
    from test_cli import pgs_source

    source = str(pgs_source(tmp_path))
    embedded, extract, none = (
        {"subtitle_extract": mode} for mode in ("embedded", "extract", "none")
    )
    assert _left_out_subtitles(source, embedded, embedded, ".mp4") == 1
    assert _left_out_subtitles(source, embedded, embedded, ".mkv") == 0
    assert _left_out_subtitles(source, extract, extract, ".mkv") == 1
    assert _left_out_subtitles(source, embedded, none, ".mkv") == 1
    assert _left_out_subtitles(source, none, none, ".mp4") == 0


def test_failed_track_extraction_keeps_the_rest(media, tmp_path, monkeypatch):
    clipper = SubtitleClipper(
        str(media["multi"]),
        str(tmp_path),
        "joined.mkv",
        [{"start": 0, "end": 2}],
        str(tmp_path),
        "extract",
    )
    extract = clipper._extract_segment_subtitle
    first = clipper._get_subtitle_streams()[0]["index"]
    monkeypatch.setattr(
        clipper,
        "_extract_segment_subtitle",
        lambda path, index: index != first and extract(path, index),
    )
    clipper.run()
    assert clipper.failed == [first]
    assert len(clipper.created_files) == 1
