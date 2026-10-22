"""Bulk queue admission and the file scanner's handoff, without GTK widgets."""

from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import gi
import pytest

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

import file_intake
import queue_runner
from engine.job_options import DEFAULT_METADATA
from file_intake import FileIntakeMixin
from queue_runner import QueueRunnerMixin


class Queue(QueueRunnerMixin, FileIntakeMixin):
    def __init__(self):
        self.currently_converting = False
        self.active_conversions = {}
        self.conversion_queue = deque()
        self.conversion_page = SimpleNamespace(
            file_metadata={}, update_queue_display=Mock()
        )
        self.settings_store = MagicMock()
        self.last_accessed_directory = "/previous"
        self.header_bar = Mock()
        self.show_error_dialog = Mock()
        self.show_info_dialog = Mock()


@pytest.fixture
def queue(monkeypatch):
    monkeypatch.setattr(queue_runner.GLib, "idle_add", Mock(return_value=37))
    return Queue()


def test_bulk_add_skips_empty_and_duplicate_paths_in_order(queue):
    existing = "/old/existing.mp4"
    queue.conversion_queue.append(existing)
    edits = {"trim_segments": [(1, 2)], "brightness": 0.4}
    queue.conversion_page.file_metadata[existing] = edits

    count = queue.add_files_to_queue(
        ["", existing, "/first/a.mp4", "/first/a.mp4", "/last/b.mp4", ""]
    )

    assert count == 2
    assert list(queue.conversion_queue) == [existing, "/first/a.mp4", "/last/b.mp4"]
    assert queue.conversion_page.file_metadata[existing] is edits
    assert queue.last_accessed_directory == "/last"
    assert queue.settings_store.save_setting.call_args_list == [
        call("last-accessed-directory", "/first"),
        call("last-accessed-directory", "/last"),
    ]
    queue_runner.GLib.idle_add.assert_called_once_with(queue._refresh_queue_display)
    assert queue._queue_refresh_id == 37
    queue.conversion_page.update_queue_display.assert_not_called()


def test_new_files_get_independent_metadata_without_overwriting_edits(queue):
    defaults = deepcopy(DEFAULT_METADATA)
    edits = {"trim_segments": [(2, 4)]}
    queue.conversion_page.file_metadata["/edited.mp4"] = edits
    assert queue.add_files_to_queue(["/a.mp4", "/b.mp4", "/edited.mp4"]) == 3
    first = queue.conversion_page.file_metadata["/a.mp4"]
    second = queue.conversion_page.file_metadata["/b.mp4"]
    assert first == second == defaults
    first["trim_segments"].append((1, 3))
    assert second == defaults and DEFAULT_METADATA == defaults
    assert queue.conversion_page.file_metadata["/edited.mp4"] is edits


@pytest.mark.parametrize("state", ["currently_converting", "_quitting"])
def test_bulk_add_refuses_running_or_closing_queue(queue, state):
    setattr(queue, state, True)
    assert queue.add_files_to_queue(["/new.mp4"]) == 0
    assert not queue.conversion_queue and not queue.conversion_page.file_metadata
    assert queue.last_accessed_directory == "/previous"
    queue.settings_store.save_setting.assert_not_called()
    queue_runner.GLib.idle_add.assert_not_called()


@pytest.mark.parametrize("paths", [[], ["", ""], ["/existing.mp4", ""]])
def test_empty_or_duplicate_batch_has_no_side_effects(queue, paths):
    queue.conversion_queue.append("/existing.mp4")
    assert queue.add_files_to_queue(paths) == 0
    assert list(queue.conversion_queue) == ["/existing.mp4"]
    assert queue.last_accessed_directory == "/previous"
    queue.settings_store.save_setting.assert_not_called()
    queue_runner.GLib.idle_add.assert_not_called()


def test_multiple_batches_share_one_pending_refresh(queue):
    assert queue.add_files_to_queue(["/a.mp4"]) == 1
    assert queue.add_files_to_queue(["/b.mp4", "/a.mp4"]) == 1
    queue_runner.GLib.idle_add.assert_called_once()
    assert queue._refresh_queue_display() == queue_runner.GLib.SOURCE_REMOVE
    queue.conversion_page.update_queue_display.assert_called_once()
    assert queue._queue_refresh_id is None
    assert queue.add_files_to_queue(["/c.mp4"]) == 1
    assert queue_runner.GLib.idle_add.call_count == 2


def test_bulk_add_before_conversion_page_exists(queue):
    del queue.conversion_page
    assert queue.add_files_to_queue(["/a.mp4", "/a.mp4"]) == 1
    assert list(queue.conversion_queue) == ["/a.mp4"]
    assert queue.last_accessed_directory == "/"
    queue_runner.GLib.idle_add.assert_not_called()


def test_large_batch_does_not_search_the_queue_for_each_path(queue):
    class NoMembershipScan(deque):
        def __contains__(self, value):
            pytest.fail("Bulk admission must not linearly scan the queue per path")

    paths = [f"/clips/{i}.mp4" for i in range(2000)]
    queue.conversion_queue = NoMembershipScan(paths[:1000])
    assert queue.add_files_to_queue(paths + paths) == 1000
    assert list(queue.conversion_queue) == paths
    queue_runner.GLib.idle_add.assert_called_once()


@pytest.fixture
def scanner(queue, monkeypatch):
    # Run enumeration synchronously, retaining the idle handoff so the test
    # can inspect what happens before and after the GTK callback.
    monkeypatch.setattr(
        file_intake.threading,
        "Thread",
        lambda *, target, **kwargs: SimpleNamespace(start=target),
    )
    queue.add_files_to_queue = Mock(wraps=queue.add_files_to_queue)
    return queue


@pytest.mark.parametrize("selected_files", [False, True])
def test_scanner_publishes_one_batch_and_reports_unique_count(
    scanner, tmp_path, selected_files
):
    video, other = tmp_path / "video.mp4", tmp_path / "custom.data"
    video.touch()
    other.touch()
    completed = Mock()
    batch_events = []

    @contextmanager
    def batch():
        batch_events.append("begin")
        yield
        batch_events.append("end")

    def save(*args):
        assert batch_events == ["begin"]

    scanner.settings_store.batch_update.side_effect = batch
    scanner.settings_store.save_setting.side_effect = save
    scanner.add_paths_to_queue(
        [str(video), str(video), str(other), str(tmp_path / "missing.mp4")],
        completed,
        selected_files=selected_files,
    )
    scanner.add_files_to_queue.assert_not_called()
    assert scanner._pending_imports == 1
    scanner.header_bar.set_buttons_sensitive.assert_called_once_with(False)
    finish, files, error, skipped = file_intake.GLib.idle_add.call_args.args
    assert files == [str(video), str(video)] + ([str(other)] if selected_files else [])
    assert error is None and skipped == 0

    assert finish(files, error, skipped) is False

    scanner.add_files_to_queue.assert_called_once_with(files)
    scanner.settings_store.batch_update.assert_called_once()
    assert batch_events == ["begin", "end"]
    completed.assert_called_once_with(2 if selected_files else 1)
    assert scanner._pending_imports == 0
    scanner.header_bar.set_buttons_sensitive.assert_called_with(True)
    scanner.show_error_dialog.assert_not_called()


def test_failed_batch_save_reports_error_and_restores_controls(scanner, tmp_path):
    video = tmp_path / "video.mp4"
    video.touch()
    scanner.settings_store.save_setting.side_effect = OSError("disk full")
    completed = Mock()
    scanner.add_paths_to_queue([str(video)], completed)
    finish, *args = file_intake.GLib.idle_add.call_args.args
    assert finish(*args) is False
    scanner.add_files_to_queue.assert_called_once_with([str(video)])
    scanner.show_error_dialog.assert_called_once_with("disk full")
    completed.assert_not_called()
    assert scanner._pending_imports == 0
    scanner.header_bar.set_buttons_sensitive.assert_called_with(True)


def test_scanner_handoff_after_quit_does_not_add_files(scanner, tmp_path):
    video = tmp_path / "video.mp4"
    video.touch()
    completed = Mock()
    scanner.add_paths_to_queue([str(video)], completed)
    finish, *args = file_intake.GLib.idle_add.call_args.args
    scanner._quitting = True
    assert finish(*args) is False
    assert scanner._pending_imports == 0
    scanner.add_files_to_queue.assert_not_called()
    scanner.settings_store.batch_update.assert_not_called()
    completed.assert_not_called()
