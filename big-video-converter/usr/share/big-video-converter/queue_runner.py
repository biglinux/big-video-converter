"""Queue manager mixin: queue operations and running the conversions."""

import gettext
import logging
import os
import shutil
import threading
import uuid
from collections import deque
from copy import deepcopy

from engine.job_options import DEFAULT_METADATA
from gi.repository import Adw, GLib

_ = gettext.gettext
logger = logging.getLogger(__name__)


def disk_shortage(files: list[str], output_folder: str):
    """(free, required) bytes of the first destination without room for
    about 1.5 times its inputs, or None when every one has it."""
    volumes = {}
    for file_path in files:
        target = os.path.abspath(output_folder or os.path.dirname(file_path))
        try:
            device = os.stat(target).st_dev
            size = os.path.getsize(file_path)
        except OSError:
            continue
        volumes.setdefault(device, [target, 0])[1] += size
    for target_dir, size in volumes.values():
        required = int(size * 1.5)
        try:
            free = shutil.disk_usage(target_dir).free
        except OSError:
            continue
        if free < required:
            return free, required
    return None


class QueueRunnerMixin:
    """Mixin providing conversion queue management and orchestration."""

    def add_files_to_queue(self, file_paths: list[str]) -> int:
        """Queue paths the caller found to be files, off the main loop.

        Returns how many were not queued yet.
        """
        # A running queue already has one progress row per file; a file added
        # while it runs would have none.
        if getattr(self, "_quitting", False) or self.currently_converting:
            return 0
        queued = set(self.conversion_queue)
        added = 0
        for file_path in file_paths:
            if not file_path or file_path in queued:
                continue
            queued.add(file_path)
            input_dir = os.path.dirname(file_path)
            self.last_accessed_directory = input_dir
            self.settings_store.save_setting("last-accessed-directory", input_dir)

            self.conversion_queue.append(file_path)
            added += 1

            # Initialize new files without discarding edits when a file is added again
            if hasattr(self, "conversion_page"):
                self.conversion_page.file_metadata.setdefault(
                    file_path, deepcopy(DEFAULT_METADATA)
                )

        if (
            added
            and hasattr(self, "conversion_page")
            and getattr(self, "_queue_refresh_id", None) is None
        ):
            self._queue_refresh_id = GLib.idle_add(self._refresh_queue_display)
        return added

    def _refresh_queue_display(self):
        self._queue_refresh_id = None
        if not getattr(self, "_quitting", False):
            self.conversion_page.update_queue_display()
        return GLib.SOURCE_REMOVE

    def clear_queue(self) -> None:
        # Each file of a running queue has a progress row that clearing would
        # leave pending.
        if self.currently_converting or self.active_conversions:
            return
        self.conversion_queue.clear()

        if hasattr(self, "conversion_page"):
            self.conversion_page.file_metadata.clear()
            self.conversion_page.update_queue_display()

    def remove_from_queue(self, file_path: str) -> bool:
        if file_path in self.conversion_queue:
            self.conversion_queue.remove(file_path)

            # Also clear metadata when removing from queue
            if (
                hasattr(self, "conversion_page")
                and file_path in self.conversion_page.file_metadata
            ):
                del self.conversion_page.file_metadata[file_path]

            # If this file is currently loaded in the editor, clear it to force fresh reload
            if (
                hasattr(self, "video_edit_page")
                and self.video_edit_page.current_video_path == file_path
            ):
                self.video_edit_page.current_video_path = None

            if hasattr(self, "conversion_page"):
                self.conversion_page.update_queue_display()
            return True
        return False

    # Conversion processing
    def _ask_about_disk_space(self, shortage) -> bool:
        """Start the queue, or ask first when a destination looks too full."""
        if getattr(self, "_quitting", False):
            return GLib.SOURCE_REMOVE
        if shortage is None:
            self.checking_space = False
            self._do_start_queue_processing()
            return GLib.SOURCE_REMOVE
        free, required = shortage
        free_gb = free / (1024**3)
        needed_gb = required / (1024**3)
        dialog = Adw.AlertDialog(
            heading=_("Low disk space"),
            body=_(
                "The destination has {free:.1f} GB free but "
                "approximately {needed:.1f} GB may be needed.\n\n"
                "Do you want to continue anyway?"
            ).format(free=free_gb, needed=needed_gb),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("continue", _("Continue"))
        dialog.set_response_appearance("continue", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog: Adw.AlertDialog, result: object) -> None:
            try:
                response = _dialog.choose_finish(result)
            except GLib.Error:
                response = "cancel"
            self._on_disk_space_response(response)

        window = self.get_active_window()
        dialog.choose(window, None, on_response)
        return GLib.SOURCE_REMOVE

    def _on_disk_space_response(self, response: str) -> None:
        """Continue the queue, or hand the buttons back after a cancel.

        The header disabled Convert before asking, so a cancel enables it
        again.
        """
        self.checking_space = False
        if response == "continue":
            self._do_start_queue_processing()
            return
        self._restore_queue_after_single_file(False)
        self.header_bar.set_buttons_sensitive(True)

    def start_queue_processing(self) -> None:
        if (
            getattr(self, "_quitting", False)
            or self.active_conversions
            or self.currently_converting
            or self.checking_space
            or getattr(self, "_pending_imports", 0)
        ):
            self._restore_queue_after_single_file(False)
            return
        if not self.conversion_queue:
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        # One message for the whole queue, not one dialog for every job.
        options_error = self.conversion_page.additional_options_error(
            self.conversion_queue
        )
        if options_error:
            self._restore_queue_after_single_file(False)
            self.show_error_dialog(
                _("The additional FFmpeg options cannot be used"), options_error
            )
            self.header_bar.set_buttons_sensitive(True)
            return

        custom = self.settings_store.get_boolean("use-custom-output-folder", False)
        folder = self.settings_store.load_setting("output-folder", "") if custom else ""
        files = list(self.conversion_queue)
        self.checking_space = True
        self.header_bar.set_buttons_sensitive(False)

        # stat and disk_usage block for as long as a dead network mount does.
        def measure() -> None:
            GLib.idle_add(self._ask_about_disk_space, disk_shortage(files, folder))

        threading.Thread(target=measure, name="bvc-disk-space", daemon=True).start()

    def _do_start_queue_processing(self):
        """Internal: actually start queue processing after checks pass."""
        self.is_cancellation_requested = False
        self.completed_conversions = []
        # True for the whole generation, between jobs too: the header, the
        # queue actions and file additions stay off until it settles.
        self.currently_converting = True
        self.header_bar.set_buttons_sensitive(False)

        self._queue_completion_presented = False
        if hasattr(self, "progress_page"):
            self.progress_page.initialize_queue(list(self.conversion_queue))

        self.main_stack.set_visible_child_name("progress_view")
        self.parallel_jobs = 1
        if self._gpu_concurrency() > 1:
            # Ranking runs ffmpeg, vainfo and an NVENC test frame on the first
            # call, so it happens on a worker before the first job starts.
            gpus = list(self.detected_gpus)
            codec = self.settings_store.load_setting("video-codec", "h264")

            def rank() -> None:
                from engine.gpu_selector import select_best_gpu, supports

                try:
                    best = select_best_gpu(gpus, codec)
                    # A card that cannot encode the codec gets no job of its own.
                    capable = [g for g in gpus if supports(g, gpus, codec) is True]
                except Exception:
                    self.logger.exception("Could not rank the GPUs")
                    best, capable = None, []
                GLib.idle_add(self._start_on_gpu_slots, capable, best)

            threading.Thread(target=rank, name="bvc-gpu-rank", daemon=True).start()
        else:
            GLib.timeout_add(300, self.process_next_in_queue)

    def _gpu_concurrency(self) -> int:
        """How many jobs run at once: one per GPU, at most two, in auto mode."""
        if (
            self.settings_store.load_setting("gpu", "auto") == "auto"
            and len(getattr(self, "detected_gpus", ())) > 1
            and not self.settings_store.get_boolean("force-copy-video", False)
        ):
            # Two encoders at once already saturate disk and CPU on these machines.
            return min(len(self.detected_gpus), 2)
        return 1

    def _start_on_gpu_slots(self, gpus, best) -> bool:
        """Give each concurrent job a GPU, the chosen one first, then start."""
        if best:
            gpus = sorted(
                gpus,
                key=lambda g: (
                    (g["type"], g["device"]) != (best["type"], best["device"])
                ),
            )
        self.gpu_slots.clear()
        self.gpu_slots.extend(
            {
                "id": i,
                "type": gpu["type"] if gpu["type"] != "unknown" else "auto",
                "device": gpu["device"],
                "name": gpu["name"],
            }
            for i, gpu in enumerate(gpus[: self._gpu_concurrency()])
        )
        self.parallel_jobs = max(1, len(self.gpu_slots))
        self.process_next_in_queue()
        return GLib.SOURCE_REMOVE

    def _present_queue_completion(self) -> bool:
        """Present and notify one settled queue generation exactly once."""
        if getattr(self, "_queue_completion_presented", False):
            return False

        notification = None
        progress_page = getattr(self, "progress_page", None)
        if progress_page is not None:
            shown = progress_page.show_completion_summary()
            if shown is False:
                return False
            notification_getter = getattr(
                progress_page, "completion_notification", None
            )
            if notification_getter is not None:
                notification = notification_getter()

        self._queue_completion_presented = True
        self.send_system_notification(
            *(
                notification
                or (
                    _("Batch Conversion Complete"),
                    _("All queued files have been processed."),
                )
            )
        )
        return True

    def convert_current_file(self) -> None:
        """Convert the currently opened file in the editor"""
        if not hasattr(self, "video_edit_page") or not self.video_edit_page:
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        if self.video_edit_page.is_playing:
            if self.video_edit_page.mpv_player:
                self.video_edit_page.mpv_player.pause()
            self.video_edit_page.is_playing = False
            if hasattr(self.video_edit_page, "ui") and hasattr(
                self.video_edit_page.ui, "play_pause_button"
            ):
                self.video_edit_page.ui.play_pause_button.set_icon_name(
                    "media-playback-start"
                )
            if self.video_edit_page.position_update_id:
                GLib.source_remove(self.video_edit_page.position_update_id)
                self.video_edit_page.position_update_id = None

        current_file = self.video_edit_page.current_video_path
        if not current_file or not os.path.exists(current_file):
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        self._original_queue_before_single_conversion = list(self.conversion_queue)
        self._single_file_conversion = True
        self._single_file_to_convert = current_file
        self.conversion_queue = deque([current_file])
        self.start_queue_processing()

    def _restore_queue_after_single_file(self, success: bool) -> None:
        """Bring back the queue a "Convert this file" run replaced.

        Every way that run ends comes here: success drops the converted file,
        anything else (cancel, failure, a refused start) keeps it queued.
        """
        if not getattr(self, "_single_file_conversion", False):
            return
        self._single_file_conversion = False
        restored = list(getattr(self, "_original_queue_before_single_conversion", ()))
        if success:
            restored = [f for f in restored if f != self._single_file_to_convert]
        self.conversion_queue = deque(restored)
        if hasattr(self, "conversion_page"):
            GLib.idle_add(self.conversion_page.update_queue_display)

    def _finish_queue_generation(self, success: bool) -> None:
        self._restore_queue_after_single_file(success)
        self.is_cancellation_requested = False
        self.currently_converting = False
        self.header_bar.set_buttons_sensitive(True)
        self._present_queue_completion()

    def process_next_in_queue(self) -> bool:
        """Process the next file in queue if we have capacity"""

        # If cancellation was requested, don't start new conversions
        if self.is_cancellation_requested or getattr(self, "_quitting", False):
            if not self.active_conversions:
                self._finish_queue_generation(False)
            return False

        max_concurrent = self.parallel_jobs

        if len(self.active_conversions) >= max_concurrent:
            # We're full, wait for a completion
            return False

        if not self.conversion_queue:
            # Only finish if no active conversions remain
            if not self.active_conversions:
                # A single file only reaches here when it never ran.
                self._finish_queue_generation(False)
            return False

        next_file = self.conversion_queue.popleft()

        # Allocate a GPU slot if using parallel processing
        gpu_slot = None
        if max_concurrent > 1 and self.gpu_slots:
            gpu_slot = self.gpu_slots.popleft()
            self.logger.info(
                "Allocated GPU slot for %s: %s",
                os.path.basename(next_file),
                gpu_slot["name"],
            )

        conversion_info = {
            "file_path": next_file,
            "gpu_slot": gpu_slot,
            "start_time": GLib.get_real_time(),
            "job_id": uuid.uuid4().hex,
            "cancel_event": threading.Event(),
        }

        with self.conversions_lock:
            self.active_conversions.append(conversion_info)

        GLib.idle_add(self.conversion_page.update_queue_display)

        # Preparing a job asks ffprobe about the file (audio tracks, size,
        # MP4 compatibility), each call up to ten seconds on slow storage.
        # Those answers, and whether the file is still there, are collected on
        # a worker thread first, so the main loop never blocks; the launch
        # itself stays on the main loop.
        probes = {}
        found = []

        def launch() -> bool:
            # A row skipped during the pre-flight probe set its job's event.
            if (
                self.is_cancellation_requested
                or getattr(self, "_quitting", False)
                or conversion_info["cancel_event"].is_set()
            ):
                conversion_info["cancel_event"].set()
                self.conversion_completed(
                    False, file_path=next_file, job_id=conversion_info["job_id"]
                )
                return False
            if not found:
                self.progress_page.finish_pending(
                    next_file, success=False, reason=_("File not found")
                )
                self.conversion_completed(
                    False, file_path=next_file, job_id=conversion_info["job_id"]
                )
                return False
            # Set right before use: another slot's launch may have run in between.
            self.conversion_page.current_file_path = next_file
            try:
                result = self.conversion_page.force_start_conversion(
                    gpu_override=gpu_slot, probes=probes
                )
            except Exception:
                self.logger.exception("Failed to prepare conversion")
                result = False
            if result is False:
                self.conversion_completed(
                    False, file_path=next_file, job_id=conversion_info["job_id"]
                )
            return False

        gpus = getattr(self, "detected_gpus", [])
        codec = self.settings_store.load_setting("video-codec", "h264")

        def warm_then_launch() -> None:
            if not os.path.exists(next_file):
                GLib.idle_add(launch)
                return
            found.append(True)
            try:
                from engine.file_info import launch_probes
                from engine.gpu_selector import select_best_gpu

                probes.update(launch_probes(next_file))
                # Fills the encoder and vainfo caches the launch reads.
                select_best_gpu(gpus, codec)
            except Exception:
                self.logger.exception("Pre-flight probe failed; launching anyway")
            GLib.idle_add(launch)

        threading.Thread(
            target=warm_then_launch, name="bvc-preflight", daemon=True
        ).start()

        # Try to start another if we have capacity (active < max)
        with self.conversions_lock:
            active_count = len(self.active_conversions)

        if active_count < max_concurrent and self.conversion_queue:
            GLib.timeout_add(500, self.process_next_in_queue)

        return False

    def conversion_completed(
        self,
        success,
        file_path: str | None = None,
        job_id: str | None = None,
    ) -> None:
        """Release the finished job's slot and move the queue on.

        Runs on the main loop. file_path or job_id identify the job, so the
        right GPU slot is released when several conversions run in parallel.
        """
        # A completion must match an active job. A late/duplicate event
        # never releases the oldest (or any unrelated) GPU slot.
        with self.conversions_lock:
            matches = [
                i
                for i, info in enumerate(self.active_conversions)
                if (
                    info.get("job_id") == job_id
                    if job_id
                    else file_path is not None and info.get("file_path") == file_path
                )
            ]
            if len(matches) != 1:
                self.logger.debug(
                    "Ignoring stale or unidentified completion: %s", job_id or file_path
                )
                return
            finished = self.active_conversions.pop(matches[0])
            if finished.get("gpu_slot"):
                self.gpu_slots.append(finished["gpu_slot"])
            self.claimed_outputs.discard(finished.get("output_path"))

        self.progress_page.finish_pending(
            finished["file_path"],
            success=success,
            cancelled=bool(
                finished.get("cancel_event") and finished["cancel_event"].is_set()
            ),
        )
        if self.is_cancellation_requested:
            if not self.active_conversions:
                self._finish_queue_generation(success)
            return

        # A "Convert this file" run ends with its one job.
        if getattr(self, "_single_file_conversion", False):
            self._finish_queue_generation(success)
            return

        GLib.idle_add(self.process_next_in_queue)
