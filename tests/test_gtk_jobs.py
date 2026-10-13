"""Conversion jobs started from the real application: reservation, launch and failure."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
    reason="Requires an X11 or Wayland display",
)
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from conftest import pump, pump_until, widgets
from gi.repository import Adw, GLib, Gtk


def _reserved_jobs(app, monkeypatch, *paths):
    """Jobs as process_next_in_queue reserves them, without launching anything."""
    import threading

    jobs = [
        {
            "file_path": path,
            "job_id": f"job-{index}",
            "cancel_event": threading.Event(),
            "gpu_slot": None,
        }
        for index, path in enumerate(paths)
    ]
    monkeypatch.setattr(app, "active_conversions", list(jobs))
    return jobs


def _job_settings(app, monkeypatch, **overrides):
    monkeypatch.setattr(
        app.settings_store,
        "settings",
        {
            **app.settings_store.settings,
            "output-format-index": 0,
            "force-copy-video": False,
            "size-target": "",
            "additional-options": "",
            "gpu": "software",
            **overrides,
        },
    )
    monkeypatch.setattr(app, "active_preset", lambda: None)
    monkeypatch.setattr(app, "claimed_outputs", set())


def test_parallel_jobs_reserve_distinct_output_names(
    app, media, tmp_path, monkeypatch, request
):
    import shutil

    page = app.conversion_page
    sources = []
    for folder, name in (("a", "clip.mkv"), ("b", "clip.avi")):
        (tmp_path / folder).mkdir()
        shutil.copyfile(media["video"], tmp_path / folder / name)
        sources.append(str(tmp_path / folder / name))
    out = tmp_path / "out"
    out.mkdir()
    _reserved_jobs(app, monkeypatch, *sources)
    _job_settings(app, monkeypatch)
    monkeypatch.setattr(page, "file_metadata", {})
    old = (page.folder_combo.get_selected(), page.output_folder_entry.get_text())
    request.addfinalizer(
        lambda: (
            page.folder_combo.set_selected(old[0]),
            page.output_folder_entry.set_text(old[1]),
        )
    )
    page.folder_combo.set_selected(1)
    page.output_folder_entry.set_text(str(out))
    contexts = []
    monkeypatch.setattr(
        page, "_continue_conversion", lambda context: contexts.append(context) or True
    )
    for source in sources:
        monkeypatch.setattr(page, "current_file_path", source, raising=False)
        assert page.force_start_conversion()
    outputs = [context["full_output_path"] for context in contexts]
    assert outputs == [str(out / "clip.mp4"), str(out / "clip_1.mp4")]
    assert app.claimed_outputs == set(outputs)
    monkeypatch.setattr(app, "process_next_in_queue", lambda: False)
    app.conversion_completed(True, job_id="job-0")
    assert app.claimed_outputs == {outputs[1]}


def test_cancelled_single_run_restores_queue(app, monkeypatch):
    from collections import deque

    queue = ["/videos/A.mp4", "/videos/B.mp4", "/videos/C.mp4"]
    monkeypatch.setattr(app, "conversion_queue", deque())
    monkeypatch.setattr(
        app, "_original_queue_before_single_conversion", list(queue), raising=False
    )
    monkeypatch.setattr(app, "_single_file_conversion", True, raising=False)
    monkeypatch.setattr(app, "_single_file_to_convert", queue[1], raising=False)
    monkeypatch.setattr(app, "currently_converting", True)
    monkeypatch.setattr(app, "is_cancellation_requested", True)
    _reserved_jobs(app, monkeypatch, queue[1])
    app.conversion_completed(False, job_id="job-0")
    assert list(app.conversion_queue) == queue and not app._single_file_conversion
    assert not app.currently_converting and not app.is_cancellation_requested


def test_vanished_file_restores_queue_with_reason(app, monkeypatch, tmp_path):
    from collections import deque

    missing, other = str(tmp_path / "gone.mp4"), str(tmp_path / "other.mp4")
    monkeypatch.setattr(app, "conversion_queue", deque([missing]))
    monkeypatch.setattr(
        app, "_original_queue_before_single_conversion", [other, missing], raising=False
    )
    monkeypatch.setattr(app, "_single_file_conversion", True, raising=False)
    monkeypatch.setattr(app, "_single_file_to_convert", missing, raising=False)
    monkeypatch.setattr(app, "active_conversions", [])
    monkeypatch.setattr(app, "currently_converting", True)
    app.progress_page.initialize_queue([missing])
    try:
        app.process_next_in_queue()
        pump_until(lambda: not app._single_file_conversion)
        assert (
            list(app.conversion_queue) == [other, missing]
            and not app.currently_converting
        )
        row = app.progress_page.queue_items[missing]
        assert (
            row.status == "failed" and row.status_label.get_text() == "File not found"
        )
    finally:
        app.progress_page.reset()


def test_opening_files_queues_existing_ones(app, media, monkeypatch, tmp_path):
    from collections import deque

    from gi.repository import Gio

    monkeypatch.setattr(app, "conversion_queue", deque())
    video = str(media["video"])
    gone = Gio.File.new_for_path(str(tmp_path / "gone.mp4"))
    app.do_open([Gio.File.new_for_path(video), gone], 2, "")
    pump_until(lambda: list(app.conversion_queue) == [video])
    pump(0.05)
    assert list(app.conversion_queue) == [video]


def test_vanished_file_stays_listed_as_missing(app, monkeypatch, tmp_path):
    from collections import deque

    missing = str(tmp_path / "moved.mp4")
    monkeypatch.setattr(app, "conversion_queue", deque([missing]))
    page = app.conversion_page
    page.update_queue_display()
    pump_until(
        lambda: (
            page.queue_rows
            and any(
                isinstance(w, Gtk.Label) and w.get_label() == "File not found"
                for w in widgets(page.queue_rows[0])
            )
        )
    )
    assert [row.file_path for row in page.queue_rows] == [missing]


def test_launch_reuses_worker_probe_answers(app, media, monkeypatch):
    """The queue probes on a worker; the launch on the main loop runs no ffprobe."""
    from engine import file_info

    source = str(media["video"])
    page = app.conversion_page
    monkeypatch.setattr(
        app.settings_store,
        "settings",
        {
            "video-codec": "h264",
            "gpu": "software",
            "force-copy-video": True,
            "output-format-index": 0,
            "audio-handling": "copy",
        },
    )
    monkeypatch.setattr(app, "active_preset", lambda: None)
    monkeypatch.setattr(page, "current_file_path", source, raising=False)
    monkeypatch.setattr(
        page, "file_metadata", {source: {"crop_left": 8, "crop_right": 8}}
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("probed on the main loop")

    for name in (
        "has_audio_streams",
        "get_video_dimensions",
        "check_mp4_compatibility",
    ):
        monkeypatch.setattr(file_info, name, forbidden)
    contexts = []
    monkeypatch.setattr(
        page, "_continue_conversion", lambda context: contexts.append(context) or True
    )
    assert page.force_start_conversion(
        probes={"audio": False, "size": (128, 72), "mp4": (True, [])}
    )
    assert contexts[-1]["env_vars"]["audio_handling"] == "none"


def test_skipped_reserved_job_never_launches(app, media, monkeypatch, tmp_path):
    import shutil
    import threading
    from collections import deque

    from engine import file_info

    source = str(tmp_path / "reserved.mp4")
    shutil.copyfile(media["video"], source)
    probing = threading.Event()
    monkeypatch.setattr(file_info, "launch_probes", lambda path: probing.wait(5) and {})
    launched = []
    monkeypatch.setattr(
        app.conversion_page,
        "force_start_conversion",
        lambda **kw: launched.append(kw) or True,
    )
    monkeypatch.setattr(app, "conversion_queue", deque([source]))
    monkeypatch.setattr(app, "active_conversions", [])
    monkeypatch.setattr(app, "is_cancellation_requested", False)
    monkeypatch.setattr(app, "currently_converting", True)
    app.progress_page.initialize_queue([source])
    try:
        app.process_next_in_queue()
        assert app.active_conversions
        app.progress_page.queue_items[source].cancel()
        probing.set()
        pump_until(lambda: not app.currently_converting)
        assert not launched and not app.active_conversions
        assert app.progress_page.queue_items[source].status == "cancelled"
    finally:
        probing.set()
        app.progress_page.reset()


def test_queue_actions_disabled_while_running(app, monkeypatch, tmp_path):
    from collections import deque

    names = (
        "add_files",
        "add_folder",
        "add_network_file",
        "clear_queue",
        "start_conversion",
        "restore_settings",
    )
    kept = tmp_path / "kept.mp4"
    dropped = tmp_path / "dropped.mp4"
    kept.write_bytes(b"")
    dropped.write_bytes(b"")
    monkeypatch.setattr(app, "conversion_queue", deque([str(kept)]))
    with monkeypatch.context() as patch:
        patch.setattr(app, "currently_converting", True)
        app.header_bar.set_buttons_sensitive(True)
        assert not any(app.lookup_action(name).get_enabled() for name in names)
        app.clear_queue()
        assert not app.add_file_to_queue(str(dropped))
        assert list(app.conversion_queue) == [str(kept)]
    app.header_bar.set_buttons_sensitive(True)
    assert all(app.lookup_action(name).get_enabled() for name in names)


def test_invalid_options_report_once_and_stop(app, monkeypatch):
    from collections import deque

    queue = ["/videos/a.mp4", "/videos/b.mp4"]
    _job_settings(app, monkeypatch, **{"additional-options": "-vf 'unclosed"})
    monkeypatch.setattr(app, "conversion_queue", deque(queue))
    monkeypatch.setattr(app.conversion_page, "file_metadata", {})
    errors = []
    monkeypatch.setattr(app, "show_error_dialog", lambda *args: errors.append(args))
    monkeypatch.setattr(
        "queue_runner.disk_shortage", lambda *a: pytest.fail("the queue started")
    )
    app.start_queue_processing()
    assert len(errors) == 1 and "No closing quotation" in errors[0][1]
    assert list(app.conversion_queue) == queue and not app.currently_converting


def test_second_start_waits_for_space_check(app, monkeypatch):
    import threading
    from collections import deque

    import queue_runner

    gate, started = threading.Event(), []
    monkeypatch.setattr(queue_runner, "disk_shortage", lambda *a: gate.wait(5) and None)
    monkeypatch.setattr(app, "_do_start_queue_processing", lambda: started.append(1))
    monkeypatch.setattr(app, "conversion_queue", deque(["/videos/a.mp4"]))
    monkeypatch.setattr(app.conversion_page, "file_metadata", {})
    app.start_queue_processing()
    app.start_queue_processing()
    gate.set()
    pump_until(lambda: started)
    pump(0.2)
    assert started == [1] and not app.checking_space
    app.header_bar.set_buttons_sensitive(True)


def test_queue_drop_moves_own_rows_last(app, monkeypatch):
    from collections import deque

    paths = ["/videos/a.mp4", "/videos/b.mp4", "/videos/c.mp4"]
    monkeypatch.setattr(app, "conversion_queue", deque(paths))
    page = app.conversion_page
    monkeypatch.setattr(page, "dragged_row", None)
    # Text dragged from another application is not a row index.
    assert not page.on_drop_listbox(None, "0", 0, 10**6)
    page.dragged_row = object()
    assert not page.on_drop_listbox(None, "5", 0, 10**6)
    assert not page.on_drop_listbox(None, "-1", 0, 10**6)
    assert list(app.conversion_queue) == paths
    assert page.on_drop_listbox(None, "0", 0, 10**6)
    assert list(app.conversion_queue) == paths[1:] + paths[:1]


def test_closing_mp4_question_completes_its_job(app, media, monkeypatch):

    page = app.conversion_page
    source = str(media["video"])
    jobs = _reserved_jobs(app, monkeypatch, source)
    _job_settings(app, monkeypatch, **{"force-copy-video": True})
    monkeypatch.setattr(page, "file_metadata", {})
    monkeypatch.setattr(page, "current_file_path", source, raising=False)
    completed = []
    monkeypatch.setattr(
        app, "conversion_completed", lambda ok, **kwargs: completed.append((ok, kwargs))
    )
    pcm = [{"codec_type": "audio", "codec_name": "pcm_s16le", "index": 1}]
    assert page.force_start_conversion(probes={"mp4": (False, pcm)})
    pump_until(lambda: bool(page._decision_dialogs))
    # What quit() does: the job it waits for must settle.
    page.close_decision_dialogs()
    pump(0.3)
    assert completed == [(False, {"file_path": source, "job_id": "job-0"})]
    assert jobs[0]["cancel_event"].is_set() and not page._decision_dialogs


def test_job_environment_ignores_inherited_script_settings(app, media, monkeypatch):
    for name, value in (
        ("options", "-an"),
        ("size_limit", "1"),
        ("video_filter", "hflip"),
        ("http_proxy", "http://proxy.invalid:3128"),
    ):
        monkeypatch.setenv(name, value)
    page = app.conversion_page
    source = str(media["video"])
    _reserved_jobs(app, monkeypatch, source)
    _job_settings(app, monkeypatch)
    monkeypatch.setattr(page, "file_metadata", {})
    monkeypatch.setattr(page, "current_file_path", source, raising=False)
    contexts = []
    monkeypatch.setattr(
        page, "_continue_conversion", lambda context: contexts.append(context) or True
    )
    assert page.force_start_conversion()
    env = contexts[-1]["env_vars"]
    assert (
        env["options"] == "" and env["video_filter"] == "" and "size_limit" not in env
    )
    assert (
        env["http_proxy"] == "http://proxy.invalid:3128"
        and env["PATH"] == os.environ["PATH"]
    )


@pytest.mark.parametrize("size", [(None, None), (56, 72)])
def test_impossible_crop_fails_the_job(app, media, monkeypatch, size):

    page = app.conversion_page
    source = str(media["video"])
    _reserved_jobs(app, monkeypatch, source)
    _job_settings(app, monkeypatch)
    monkeypatch.setattr(
        page, "file_metadata", {source: {"crop_left": 28, "crop_right": 28}}
    )
    monkeypatch.setattr(page, "current_file_path", source, raising=False)
    errors, contexts = [], []
    monkeypatch.setattr(app, "show_error_dialog", lambda *args: errors.append(args))
    monkeypatch.setattr(
        page, "_continue_conversion", lambda context: contexts.append(context) or True
    )
    assert page.force_start_conversion(probes={"size": size}) is False
    assert not contexts and len(errors) == 1 and not app.claimed_outputs


def test_debounced_size_estimate_stops_on_close(app, media, monkeypatch):
    import ui.conversion_page as module

    calls = []

    def plan(context):
        calls.append(context)
        raise ValueError("planned")

    monkeypatch.setattr(module, "prepare_size_job", plan)
    page = app.conversion_page
    source = str(media["video"])
    monkeypatch.setattr(page, "file_metadata", {source: {}})
    page.on_file_options_by_path(source)
    dialog = app.window.get_visible_dialog()
    size_row = next(
        w
        for w in widgets(dialog)
        if isinstance(w, Adw.ComboRow) and w.get_title() == "Maximum size"
    )
    size_row.set_selected(size_row.get_model().get_n_items() - 1)
    spin = next(
        w
        for w in widgets(dialog)
        if isinstance(w, Adw.ActionRow) and w.get_title() == "Size in megabytes"
    ).get_activatable_widget()
    for value in range(60, 66):
        spin.set_value(value)
    pump(0.6)
    assert len(calls) == 1
    spin.set_value(80)
    dialog.force_close()
    pump(0.6)
    assert len(calls) == 1


def test_second_instance_uses_its_working_directory(app, media, monkeypatch, tmp_path):
    import shutil

    from gi.repository import Gio

    shutil.copyfile(media["video"], tmp_path / "relative.mp4")
    line = Gio.ApplicationCommandLine(
        arguments=GLib.Variant(
            "aay",
            [
                b"big-video-converter\0",
                b"relative.mp4\0",
                b"bvc-test://host/share/remote.mp4\0",
            ],
        ),
        platform_data=GLib.Variant(
            "a{sv}", {"cwd": GLib.Variant("ay", str(tmp_path).encode() + b"\0")}
        ),
    )
    opened = []
    real_open = app.do_open
    monkeypatch.setattr(
        app, "do_open", lambda files, count, hint, edits: opened.extend(files)
    )
    monkeypatch.setattr(app, "activate", lambda: None)
    assert app.do_command_line(line) == 0
    assert [f.get_path() for f in opened] == [str(tmp_path / "relative.mp4"), None]
    errors = []
    monkeypatch.setattr(app, "show_error_dialog", lambda *args: errors.append(args))
    real_open(opened[1:] + [Gio.File.new_for_uri("bvc-test://host/other.mp4")], 2, "")
    assert len(errors) == 1 and "bvc-test://host/share/remote.mp4" in errors[0][1]


def test_player_cuts_reach_a_new_file(app, media, monkeypatch, tmp_path):
    import shutil

    from gi.repository import Gio

    video = tmp_path / "cut.mp4"
    shutil.copyfile(media["video"], video)
    line = Gio.ApplicationCommandLine(
        arguments=GLib.Variant(
            "aay", [b"big-video-converter\0", str(video).encode() + b"\0"]
        ),
        options=GLib.Variant(
            "a{sv}",
            {
                "segments": GLib.Variant("s", "1-2,3-4.5"),
                "source-hdr": GLib.Variant("s", "pq"),
            },
        ),
    )
    monkeypatch.setattr(app, "activate", lambda: None)
    app.clear_queue()
    assert app.do_command_line(line) == 0
    pump_until(lambda: str(video) in app.conversion_queue)
    metadata = app.conversion_page.file_metadata[str(video)]
    assert metadata["trim_segments"] == [
        {"start": 1.0, "end": 2.0},
        {"start": 3.0, "end": 4.5},
    ]
    assert metadata["source_hdr"] == "pq"
    app.clear_queue()
