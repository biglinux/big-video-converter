"""Queue manager mixin — queue operations and conversion orchestration."""

import gettext
import logging
import os
import shutil
from collections import deque

from gi.repository import Adw, GLib

_ = gettext.gettext
logger = logging.getLogger(__name__)


class QueueManagerMixin:
    """Mixin providing conversion queue management and orchestration."""

    def add_file_to_queue(self, file_path: str) -> bool:
        """Add a file to the conversion queue"""
        if file_path and os.path.exists(file_path):
            # Update last accessed directory
            input_dir = os.path.dirname(file_path)
            self.last_accessed_directory = input_dir
            self.settings_manager.save_setting("last-accessed-directory", input_dir)

            # Add to queue if not already present
            if file_path not in self.conversion_queue:
                self.conversion_queue.append(file_path)
                self.logger.debug(
                    f"Added file to queue: {os.path.basename(file_path)}, Queue size: {len(self.conversion_queue)}"
                )

            # Always initialize/reset per-file metadata with default values when adding to queue
            if hasattr(self, "conversion_page"):
                self.conversion_page.file_metadata[file_path] = {
                    "trim_segments": [],
                    "crop_left": 0,
                    "crop_right": 0,
                    "crop_top": 0,
                    "crop_bottom": 0,
                    "brightness": 0.0,
                    "saturation": 1.0,
                    "hue": 0.0,
                }
                self.logger.debug(f"Initialized clean metadata for: {os.path.basename(file_path)}")

            # Update UI
            if hasattr(self, "conversion_page"):
                GLib.idle_add(self.conversion_page.update_queue_display)
            return True
        return False

    def add_to_conversion_queue(self, file_path: str):
        """Add a file to the conversion queue without starting conversion"""
        return self.add_file_to_queue(file_path)

    def clear_queue(self) -> None:
        """Clear the conversion queue"""
        self.conversion_queue.clear()

        # Clear all file metadata
        if hasattr(self, "conversion_page"):
            self.conversion_page.file_metadata.clear()

        if hasattr(self, "conversion_page"):
            self.conversion_page.update_queue_display()
        self.logger.debug("Conversion queue cleared")

    def remove_from_queue(self, file_path: str) -> bool:
        """Remove a specific file from the queue"""
        if file_path in self.conversion_queue:
            self.conversion_queue.remove(file_path)

            # Also clear metadata when removing from queue
            if (
                hasattr(self, "conversion_page")
                and file_path in self.conversion_page.file_metadata
            ):
                del self.conversion_page.file_metadata[file_path]
                self.logger.debug(
                    f"Cleared metadata for removed file: {os.path.basename(file_path)}"
                )

            # If this file is currently loaded in the editor, clear it to force fresh reload
            if (
                hasattr(self, "video_edit_page")
                and self.video_edit_page.current_video_path == file_path
            ):
                self.video_edit_page.current_video_path = None
                self.logger.debug(
                    f"Cleared editor state for removed file: {os.path.basename(file_path)}"
                )

            if hasattr(self, "conversion_page"):
                self.conversion_page.update_queue_display()
            if os.environ.get("BVC_DEBUG"):
                self.logger.debug(f"Removed {os.path.basename(file_path)} from queue")
            return True
        return False

    # Conversion processing
    def _check_disk_space(self, files: list[str]) -> bool:
        """Check if there's enough disk space for conversion.

        Estimates required space as 1.5x total input size to account for
        transcoding overhead. Returns True if enough space, False otherwise.
        """
        output_folder = self.settings_manager.load_setting("output-folder", "")
        if output_folder and output_folder.strip():
            target_dir = os.path.normpath(os.path.abspath(output_folder.strip()))
        else:
            # Default: same folder as first input file
            target_dir = os.path.dirname(files[0]) if files else "/"

        total_input_size = 0
        for f in files:
            try:
                total_input_size += os.path.getsize(f)
            except OSError:
                continue

        if total_input_size == 0:
            return True

        # Estimate 1.5x input size as needed space
        required = int(total_input_size * 1.5)

        try:
            usage = shutil.disk_usage(target_dir)
        except OSError:
            return True  # Can't check — proceed anyway

        if usage.free >= required:
            return True

        # Not enough space — show warning dialog
        free_gb = usage.free / (1024 ** 3)
        needed_gb = required / (1024 ** 3)
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

        # Store response for the async callback
        self._disk_space_ok = False

        def on_response(_dialog: Adw.AlertDialog, result: object) -> None:
            try:
                response = _dialog.choose_finish(result)
            except GLib.Error:
                response = "cancel"
            if response == "continue":
                self._disk_space_ok = True
                self._do_start_queue_processing()

        window = self.get_active_window()
        dialog.choose(window, None, on_response)
        return False  # Caller should NOT proceed — async dialog handles it

    def start_queue_processing(self) -> None:
        """Start processing the conversion queue"""
        if not self.conversion_queue:
            self.logger.debug("Queue is empty, nothing to process")
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        # Check disk space before starting
        if not self._check_disk_space(list(self.conversion_queue)):
            return  # Dialog will call _do_start_queue_processing if user confirms

        self._do_start_queue_processing()

    def _do_start_queue_processing(self):
        """Internal: actually start queue processing after checks pass."""
        self.logger.debug("Starting queue processing")
        self.is_cancellation_requested = False
        self._was_queue_processing = True
        self.completed_conversions = []
        self.header_bar.set_buttons_sensitive(False)

        # Initialize the progress page with the queue items
        if hasattr(self, "progress_page"):
            self.progress_page.reset()
            self.progress_page.initialize_queue(list(self.conversion_queue))

        self.currently_converting = False
        self.main_stack.set_visible_child_name("progress_view")
        GLib.timeout_add(300, self.process_next_in_queue)

    def convert_current_file(self) -> None:
        """Convert the currently opened file in the editor"""
        if not hasattr(self, "video_edit_page") or not self.video_edit_page:
            self.logger.debug("No video edit page available")
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        if self.video_edit_page.is_playing:
            self.logger.debug("Stopping video playback before conversion")
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
            self.logger.debug("No valid file currently loaded in editor")
            GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
            return

        self.logger.debug(f"Converting current file: {os.path.basename(current_file)}")

        self._original_queue_before_single_conversion = list(self.conversion_queue)
        self._single_file_conversion = True
        self._single_file_to_convert = current_file
        self.conversion_queue = deque([current_file])
        self.start_queue_processing()

    def process_next_in_queue(self) -> bool:
        """Process the next file in queue if we have capacity"""

        # If cancellation was requested, don't start new conversions
        if self.is_cancellation_requested:
            self.logger.debug("Cancellation requested, stopping queue processing")
            self.header_bar.set_buttons_sensitive(True)
            self._was_queue_processing = False
            return False

        # Determine max concurrent conversions based on settings/hardware
        max_concurrent = 1

        # Check if we can do parallel processing
        gpu_mode = self.settings_manager.load_setting("gpu", "auto")
        force_copy = self.settings_manager.get_boolean("force-copy-video", False)

        if (
            gpu_mode == "auto"
            and hasattr(self, "detected_gpus")
            and len(self.detected_gpus) > 1
            and not force_copy
        ):
            max_concurrent = min(
                len(self.detected_gpus), 2
            )  # Limit to 2 for now as per requirement

            # Initialize GPU slots if empty and we haven't started yet
            if not self.gpu_slots and not self.active_conversions:
                self.logger.info(
                    f"Initializing {max_concurrent} GPU slots for parallel processing"
                )
                # Build slot list from detected GPUs
                slots: list[dict] = []
                for i in range(max_concurrent):
                    gpu_info = self.detected_gpus[i]
                    slot = {
                        "id": i,
                        "type": "auto",
                        "device": gpu_info["device"],
                        "name": gpu_info.get("name", "Unknown GPU"),
                    }

                    # Refine type based on name
                    name_lower = slot["name"].lower()
                    if "intel" in name_lower:
                        slot["type"] = "intel"
                    elif "nvidia" in name_lower:
                        slot["type"] = "nvidia"
                    elif "amd" in name_lower:
                        slot["type"] = "amd"

                    slots.append(slot)

                # Reorder slots so the best GPU for the target codec comes first
                try:
                    from utils.gpu_selector import select_best_gpu

                    codec = self.settings_manager.load_setting("video-codec", "h264")
                    best = select_best_gpu(self.detected_gpus, codec)
                    if best:
                        best_type = best["type"]
                        best_device = best.get("device", "")
                        # Move the best GPU to the front of the deque
                        slots.sort(
                            key=lambda s: (
                                0 if s["type"] == best_type and s["device"] == best_device
                                else 1
                            )
                        )
                        self.logger.info(
                            f"GPU slot order optimized: best={best_type} "
                            f"for codec={codec}"
                        )
                except (ImportError, OSError, ValueError) as e:
                    self.logger.debug(f"GPU selection fallback: {e}")

                for slot in slots:
                    self.gpu_slots.append(slot)

        # Check if we reached capacity
        if len(self.active_conversions) >= max_concurrent:
            # We're full, wait for a completion
            return False

        # Check if queue is empty
        if not self.conversion_queue:
            # Only finish if no active conversions remain
            if not self.active_conversions:
                self.logger.debug("Queue processing complete")
                self.header_bar.set_buttons_sensitive(True)
                self.is_cancellation_requested = False
                self.currently_converting = False

                # Show completion summary on progress page
                if hasattr(self, "progress_page"):
                    self.progress_page.show_completion_summary()

                if hasattr(self, "is_minimized") and self.is_minimized:
                    self.send_system_notification(
                        _("Batch Conversion Complete"),
                        _("All queued files have been processed."),
                    )
            return False

        # Get next file
        next_file = self.conversion_queue.popleft()

        # Verify file exists
        if not os.path.exists(next_file):
            self.logger.debug(f"Skipping missing file: {next_file}")
            # Try next one immediately
            GLib.idle_add(self.process_next_in_queue)
            return False

        # Prepare for conversion
        self.conversion_page.current_file_path = next_file

        # Allocate a GPU slot if using parallel processing
        gpu_slot = None
        if max_concurrent > 1 and self.gpu_slots:
            gpu_slot = self.gpu_slots.popleft()
            self.logger.info(
                f"Allocated GPU slot for {os.path.basename(next_file)}: {gpu_slot['name']}"
            )

        # Add to active conversions tracking with lock
        conversion_info = {
            "file_path": next_file,
            "gpu_slot": gpu_slot,
            "start_time": GLib.get_real_time(),
        }

        with self.conversions_lock:
            self.active_conversions.append(conversion_info)

        self.currently_converting = True

        # Update UI queue display
        GLib.idle_add(self.conversion_page.update_queue_display)

        # Start conversion with override if slot allocated
        result = self.conversion_page.force_start_conversion(gpu_override=gpu_slot)

        if result is False:
            # Failed to start
            self.logger.error(f"Failed to start conversion for {next_file}")
            with self.conversions_lock:
                if conversion_info in self.active_conversions:
                    self.active_conversions.remove(conversion_info)

            if gpu_slot:
                self.gpu_slots.append(gpu_slot)  # Return slot

            # Try next one
            GLib.idle_add(self.process_next_in_queue)

        # Try to start another if we have capacity (active < max)
        with self.conversions_lock:
            active_count = len(self.active_conversions)

        if active_count < max_concurrent and self.conversion_queue:
            GLib.timeout_add(500, self.process_next_in_queue)

        return False

    def _force_start_conversion(self):
        """Helper to force start conversion with proper error handling"""
        import traceback

        self.currently_converting = True
        result = False
        try:
            self.logger.debug("Forcing conversion to start automatically...")
            if hasattr(self, "conversion_page"):
                result = self.conversion_page.force_start_conversion()
        except Exception as e:
            self.logger.error(f"Error starting automatic conversion: {e}")
            traceback.print_exc()
            result = False

        if result is False:
            self.logger.error("Conversion failed to start or was deferred, resetting state.")
            self.currently_converting = False
            GLib.idle_add(lambda: self.conversion_completed(False))

        return False

    def conversion_completed(self, success, skip_tracking: bool=False) -> None:
        """Called when a conversion is completed"""
        with self.completion_lock:
            if self._processing_completion:
                self.logger.warning(
                    "WARNING: conversion_completed is already being processed, ignoring duplicate call"
                )
                return
            self._processing_completion = True

        try:
            self.logger.info(f"conversion_completed called with success={success}")

            # Handle cancellation
            if self.is_cancellation_requested:
                self.logger.info("Cancellation requested. Stopping.")
                self.is_cancellation_requested = False
                with self.conversions_lock:
                    self.active_conversions = []
                self.currently_converting = False
                GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
                self.return_to_main_view()
                return

            # Release GPU slot & Tracking
            with self.conversions_lock:
                if self.active_conversions:
                    finished_conversion = self.active_conversions.pop(0)
                    if finished_conversion.get("gpu_slot"):
                        self.logger.info(
                            f"Releasing GPU slot: {finished_conversion['gpu_slot']['name']}"
                        )
                        self.gpu_slots.append(finished_conversion["gpu_slot"])

                if not self.active_conversions:
                    self.currently_converting = False

            # Check single file conversion mode
            if (
                hasattr(self, "_single_file_conversion")
                and self._single_file_conversion
            ):
                self._single_file_conversion = False
                # Restore original queue, removing the converted file on success
                if hasattr(self, "_original_queue_before_single_conversion"):
                    restored = list(self._original_queue_before_single_conversion)
                    if success and hasattr(self, "_single_file_to_convert"):
                        converted = self._single_file_to_convert
                        restored = [f for f in restored if f != converted]
                    self.conversion_queue = deque(restored)
                    # Update UI to show restored queue
                    if hasattr(self, "conversion_page"):
                        GLib.idle_add(self.conversion_page.update_queue_display)

                GLib.idle_add(self.header_bar.set_buttons_sensitive, True)
                return

            # Continue queue processing
            GLib.idle_add(self.process_next_in_queue)

        finally:
            with self.completion_lock:
                self._processing_completion = False
