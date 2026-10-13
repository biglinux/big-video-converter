"""What the user is told when a job fails, and what the queue does before launching one."""

import subprocess
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Adw", "1")

from engine import conversion, file_info
from engine.media_validation import terminate_process_group
from queue_runner import QueueRunnerMixin
from test_supervisor import App, pump_until

ISSUE_27_STDERR = [
    "ffmpeg version n8.0 Copyright (c) 2000-2025 the FFmpeg developers",
    "  configuration: --prefix=/usr --disable-debug --enable-gpl",
    "Missing argument for option 'ac:a:0'.",
    "Error splitting the argument list: Invalid argument",
]


class TestFailureMessage:
    def test_failure_shows_the_real_error(self):
        message = conversion._failure_message(127, ISSUE_27_STDERR)
        assert "Missing argument for option 'ac:a:0'." in message
        assert "Error splitting the argument list" in message
        assert "configuration:" not in message

    def test_bare_exit_code_is_still_reported(self):
        message = conversion._failure_message(
            3, ["frame=   10 fps=0.0 q=28.0 size=       0KiB"]
        )
        assert "3" in message and "frame=" not in message

    def test_earlier_attempt_explains_last_failure(self):
        hints = [
            "\x1b[33mCould not find tag for codec none in stream #3, codec not currently supported in container\x1b[0m"
        ]
        message = conversion._failure_message(1, ["Conversion failed!"], hints)
        assert "Try MKV" in message
        assert "Could not find tag for codec none" in message
        assert "\x1b" not in message

    def test_supervised_job_reports_the_failing_stage(self, tmp_path, cli_env):
        """Sixty lines of noise, a retry marker, then the error: the error must reach the user."""
        script = (
            'for i in $(seq 60); do echo "frame=$i fps=25 size=1KiB" >&2; done; '
            'echo "Could not find tag for codec none in stream #3" >&2; '
            'echo "Running command: ffmpeg -i x"; '
            'echo "Missing argument for option '
            "'"
            "ac:a:0"
            "'"
            '." >&2; exit 127'
        )
        app = App()
        conversion.run_with_progress_dialog(
            app,
            ["bash", "-c", script],
            "test",
            env_vars={**cli_env, "output_file": str(tmp_path / "out.mp4")},
            job_id="issue27",
        )
        pump_until(lambda: bool(app.notifications), timeout=10)
        error = app.progress_page.rows[0].status_text
        assert "Missing argument for option 'ac:a:0'." in error
        assert "Could not find tag for codec none" in error
        assert app.notifications[0][0] is False


class TestProbeCache:
    def test_warm_cache_answers_without_running_ffprobe(self, media, monkeypatch):
        monkeypatch.setattr(file_info, "_PROBE_CACHE", {})
        path = str(media["video"])
        assert file_info.launch_probes(path)["audio"] is True

        def forbidden(*args, **kwargs):
            raise AssertionError(
                "ffprobe must not run on the main thread after warming"
            )

        monkeypatch.setattr(file_info.subprocess, "run", forbidden)
        assert file_info.has_audio_streams(path) is True
        assert file_info.get_video_dimensions(path) == (128, 72)
        compatible, offending = file_info.check_mp4_compatibility(path)
        assert compatible is True and offending == []

    def test_a_changed_file_is_probed_again(self, media, tmp_path, monkeypatch):
        monkeypatch.setattr(file_info, "_PROBE_CACHE", {})
        copy = tmp_path / "copy.mp4"
        copy.write_bytes(media["video"].read_bytes())
        assert file_info.get_video_dimensions(str(copy)) == (128, 72)
        with copy.open("ab") as handle:
            handle.write(b"\0")
        calls = []
        real_run = file_info.subprocess.run

        def counting(*args, **kwargs):
            calls.append(args)
            return real_run(*args, **kwargs)

        monkeypatch.setattr(file_info.subprocess, "run", counting)
        file_info.get_video_dimensions(str(copy))
        assert calls, "a modified file must not be answered from the cache"

    def test_text_probe_reads_generic_channel_counts(self, monkeypatch):
        report = (
            'Input #0, mov, from "x.mov":\n'
            "  Duration: 00:00:01.00, start: 0.000000, bitrate: 1 kb/s\n"
            "  Stream #0:0[0x1]: Video: mpeg4 (Simple Profile), yuv420p, 128x72, 25 fps\n"
            "  Stream #0:1[0x2](eng): Audio: pcm_s16le, 48000 Hz, 3 channels, s16, 2304 kb/s\n"
            "  Stream #0:2[0x3]: Audio: pcm_s16le, 48000 Hz, 5.1(side), s16, 4608 kb/s\n"
            'Output #0, null, to "pipe:":\n'
        )
        monkeypatch.setattr(
            file_info.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(stderr=report, stdout="", returncode=0),
        )
        info = file_info._probe_with_ffmpeg("/nonexistent/x.mov")
        audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
        assert [s["channels"] for s in audio] == [3, 6]
        assert audio[0]["tags"] == {"language": "eng"}


class TestProcessGroupGuard:
    def test_child_in_own_group_is_refused(self):
        process = subprocess.Popen(["sleep", "30"])
        try:
            with pytest.raises(ValueError, match="process group"):
                terminate_process_group(process)
            assert process.poll() is None
        finally:
            process.kill()
            process.wait()

    def test_a_reaped_child_is_tolerated(self):
        process = subprocess.Popen(["true"], start_new_session=True)
        process.wait()
        terminate_process_group(process, grace=1)


class TestDiskSpaceDialog:
    def _queue(self):
        queue = SimpleNamespace(calls=[], started=[])
        queue.header_bar = SimpleNamespace(
            set_buttons_sensitive=lambda value: queue.calls.append(value)
        )
        queue._do_start_queue_processing = lambda: queue.started.append(True)
        queue._restore_queue_after_single_file = lambda success: None
        return queue

    def test_cancel_hands_the_convert_button_back(self):
        queue = self._queue()
        QueueRunnerMixin._on_disk_space_response(queue, "cancel")
        assert queue.calls == [True] and not queue.started

    def test_continue_starts_the_queue(self):
        queue = self._queue()
        QueueRunnerMixin._on_disk_space_response(queue, "continue")
        assert queue.started == [True] and not queue.calls


def test_info_dialog_skips_unusable_probe_values():
    """ffprobe writes "N/A" or leaves fields out; the dialog renders on the main loop."""
    info = file_info.normalize_probe(
        {
            "format": {"size": "N/A", "duration": "nan", "bit_rate": "128000"},
            "streams": [
                {
                    "codec_type": "audio",
                    "sample_rate": "N/A",
                    "channels": 2,
                    "tags": {"language": "por"},
                },
                {
                    "codec_type": "video",
                    "width": "640",
                    "height": "x",
                    "r_frame_rate": "30/0",
                },
                "garbage",
            ],
        }
    )
    assert info["format"] == {"bit_rate": 128000}
    audio, video = info["streams"]
    assert audio == {"codec_type": "audio", "channels": 2, "tags": {"language": "por"}}
    assert video == {"codec_type": "video", "width": 640}
    assert file_info.normalize_probe({"streams": []})["format"] == {}
