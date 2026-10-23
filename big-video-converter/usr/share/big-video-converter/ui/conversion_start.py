"""Starting a conversion: the job settings, the decision dialogs and the launch."""

import os
import subprocess
import threading
from copy import deepcopy

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import gettext
import logging
import re

from constants import CONVERT_SCRIPT_PATH
from engine.conversion import run_with_progress_dialog
from engine.ffmpeg_options import validate_additional_options
from engine.job_options import freeze, segments_to_convert, snapshot_preset
from engine.presets import PresetError, output_container
from engine.segment_batch import start_segment_batch
from engine.size_target import prepare_size_job, target_bytes
from engine.video_settings import CropError, SettingsOverride, get_ffmpeg_filter_string
from gi.repository import Adw, GLib, Gtk

logger = logging.getLogger(__name__)

_ = gettext.gettext

# Settings the conversion script reads from its environment are all named so.
_SCRIPT_SETTING = re.compile(r"[a-z_]+")
_PROXY_VARIABLES = frozenset({"http_proxy", "https_proxy", "no_proxy", "all_proxy"})


class ConversionStartMixin:
    """Mixin turning the queued file and its settings into a running job."""

    def _encoding_environment(self, gpu_override=None, settings=None):
        """Hardware and encoder settings for a job that re-encodes video.

        Copy mode needs none of this, but the dialog that offers to re-encode
        instead of copying does, and it must land on the accelerator the user
        actually chose rather than falling back to the processor.
        """
        settings = settings or self.app.settings_store
        env = {}

        if gpu_override:
            if "type" in gpu_override:
                env["gpu"] = gpu_override["type"]
            if "device" in gpu_override:
                env["gpu_device"] = gpu_override["device"]
        else:
            gpu_setting = settings.load_setting("gpu", "auto")
            env["gpu"] = gpu_setting

            # GPU device selection (render device path)
            gpu_device_index = settings.load_setting("gpu-device-index", 0)

            # A chosen device in Auto mode also names the vendor's encoders.
            if gpu_device_index > 0 and hasattr(self.app, "detected_gpus"):
                idx = gpu_device_index - 1  # 0 = Auto
                if idx < len(self.app.detected_gpus):
                    device_info = self.app.detected_gpus[idx]
                    env["gpu_device"] = device_info["device"]
                    if gpu_setting == "auto" and device_info["type"] != "unknown":
                        env["gpu"] = device_info["type"]

            # Smart GPU selection: when auto mode + auto device + multiple GPUs,
            # pick the best GPU for the selected codec
            if (
                gpu_setting == "auto"
                and gpu_device_index == 0
                and hasattr(self.app, "detected_gpus")
                and len(self.app.detected_gpus) > 1
            ):
                from engine.gpu_selector import select_best_gpu

                codec = settings.load_setting("video-codec", "h264")
                best = select_best_gpu(self.app.detected_gpus, codec)
                if best:
                    env["gpu"] = best["type"]
                    if best.get("device"):
                        env["gpu_device"] = best["device"]

        env["video_quality"] = settings.load_setting("video-quality", "default")
        env["video_encoder"] = settings.load_setting("video-codec", "h264")
        env["preset"] = settings.load_setting("preset", "default")
        return env

    def force_start_conversion(self, gpu_override=None, probes=None):
        """Start converting current_file_path.

        ``probes`` holds the answers of ``file_info.launch_probes``, taken off
        the main loop; a missing answer counts as a failed probe.
        """
        probes = probes or {}
        if not hasattr(self, "current_file_path") or not os.path.exists(
            self.current_file_path
        ):
            logger.error("Cannot start conversion: No valid file selected")
            return False

        input_file = self.current_file_path

        try:
            file_metadata = deepcopy(self.file_metadata.get(input_file, {}))
            active_preset = self.app.active_preset()
            preset_snapshot = file_metadata.get("preset_snapshot")
            if (
                preset_snapshot
                and preset_snapshot.get("settings", {}).get("gpu", "auto") != "auto"
            ):
                gpu_override = None
            if preset_snapshot is None and active_preset is not None:
                preset_snapshot = snapshot_preset(active_preset)
            resolved = freeze(self.app.settings_store.settings, file_metadata)
            settings = SettingsOverride(self.app.settings_store, resolved["settings"])
        except (OSError, ValueError, PresetError) as error:
            self.app.show_error_dialog(str(error))
            return False

        try:
            # The script reads its settings from lowercase variables. An
            # inherited one (an exported "options" or "size_limit") would
            # silently change the job, so only the proxy variables pass.
            env_vars = {
                name: value
                for name, value in os.environ.items()
                if not _SCRIPT_SETTING.fullmatch(name) or name in _PROXY_VARIABLES
            }

            force_copy_video_enabled = settings.get_boolean("force-copy-video", False)

            # GPU - Use direct string value, but disable if copying without reencoding
            if force_copy_video_enabled:
                # When copying without reencoding, hardware acceleration is not needed
                env_vars["gpu"] = "software"
            else:
                env_vars.update(self._encoding_environment(gpu_override, settings))

            # Subtitle handling (works regardless of copy mode)
            env_vars["subtitle_extract"] = settings.load_setting(
                "subtitle-extract", "embedded"
            )

            # The widgets already carry a preset's structured choices; the
            # file adds what has no widget: per-encoder arguments and the
            # preset's own FFmpeg options.
            env_vars.pop("preset_file", None)
            for key in (
                "force_copy_video",
                "video_resolution",
                "video_fps",
                "video_stabilize",
                "source_hdr",
                "audio_bitrate",
                "audio_channels",
                "normalize_enabled",
            ):
                env_vars[key] = ""
            if force_copy_video_enabled:
                env_vars["force_copy_video"] = "1"

            # Audio handling - Check if video has audio streams
            audio_handling = settings.load_setting("audio-handling", "copy")

            # A probe that failed answers True, as has_audio_streams does.
            if not probes.get("audio", True):
                audio_handling = "none"

            env_vars["audio_handling"] = audio_handling

            if not force_copy_video_enabled:
                video_resolution = settings.load_setting("video-resolution", "")
                if video_resolution:
                    env_vars["video_resolution"] = video_resolution
                env_vars["video_fps"] = settings.load_setting("video-fps", "")
                if file_metadata.get("stabilize"):
                    env_vars["video_stabilize"] = "1"
                if file_metadata.get("source_hdr", "auto") != "auto":
                    env_vars["source_hdr"] = file_metadata["source_hdr"]

            if settings.get_boolean("gpu-partial", False):
                env_vars["gpu_partial"] = "1"
            if settings.get_boolean("uhd-h265", False):
                env_vars["uhd_h265"] = "1"
            if force_copy_video_enabled:
                env_vars["force_copy_video"] = "1"
            if settings.get_boolean("only-extract-subtitles", False):
                env_vars["only_extract_subtitles"] = "1"

            # Noise reduction
            sm = settings
            if sm.get_boolean("noise-reduction", False):
                env_vars["noise_reduction"] = "1"

                # Core NR parameters
                env_vars["noise_strength"] = str(
                    sm.load_setting("noise-reduction-strength", 1.0)
                )
                env_vars["noise_model"] = str(sm.load_setting("noise-model", 0))
                if sm.get_boolean("voice-only", False):
                    env_vars["voice_only"] = "1"
                    env_vars["voice_only_sensitivity"] = str(
                        sm.load_setting("voice-only-sensitivity", 3)
                    )

            # Audio filters (work independently of NR)
            # Noise gate
            if sm.get_boolean("noise-gate-enabled", False):
                env_vars["noise_gate"] = "1"
                env_vars["gate_intensity"] = str(
                    sm.load_setting("noise-gate-intensity", 0.5)
                )

            # High-pass filter
            if sm.get_boolean("hpf-enabled", False):
                env_vars["hpf_enabled"] = "1"
                env_vars["hpf_frequency"] = str(sm.load_setting("hpf-frequency", 80))

            # Compressor
            if sm.get_boolean("compressor-enabled", False):
                env_vars["compressor_enabled"] = "1"
                env_vars["compressor_intensity"] = str(
                    sm.load_setting("compressor-intensity", 1.0)
                )

            # Equalizer
            if sm.get_boolean("eq-enabled", False):
                env_vars["eq_enabled"] = "1"
                env_vars["eq_bands"] = str(
                    sm.load_setting("eq-bands", "0,0,0,0,0,0,0,0,0,0")
                )

            # Loudness normalization
            if sm.get_boolean("normalize-enabled", False):
                env_vars["normalize_enabled"] = "1"

            audio_bitrate = settings.load_setting("audio-bitrate", "")
            if audio_bitrate:
                env_vars["audio_bitrate"] = audio_bitrate
            audio_channels = settings.load_setting("audio-channels", "")
            if audio_channels:
                env_vars["audio_channels"] = audio_channels
            audio_codec = settings.load_setting("audio-codec", "aac")
            if audio_codec:
                env_vars["audio_codec"] = audio_codec

            # Per-file cuts and how to save them; removing segments converts
            # what is left of the video, joined.
            try:
                trim_segments, output_mode = segments_to_convert(file_metadata)
            except ValueError as error:
                self.app.show_error_dialog(
                    _("The segments cannot be removed"),
                    _("“{name}” was not converted: {reason}").format(
                        name=os.path.basename(input_file), reason=error
                    ),
                )
                return False

            # Get crop values from per-file metadata (not global settings)
            crop_left = file_metadata.get("crop_left", 0)
            crop_right = file_metadata.get("crop_right", 0)
            crop_top = file_metadata.get("crop_top", 0)
            crop_bottom = file_metadata.get("crop_bottom", 0)

            # Per-file editing values are applied through a local override
            # instead of being written to the global settings: two parallel
            # conversions would otherwise overwrite each other's filters.
            filter_settings = SettingsOverride(
                settings,
                {
                    "preview-crop-left": crop_left,
                    "preview-crop-right": crop_right,
                    "preview-crop-top": crop_top,
                    "preview-crop-bottom": crop_bottom,
                    "preview-brightness": file_metadata.get("brightness", 0.0),
                    "preview-contrast": file_metadata.get("contrast", 0.0),
                    "preview-saturation": file_metadata.get("saturation", 1.0),
                    "preview-hue": file_metadata.get("hue", 0.0),
                    "preview-rotation": file_metadata.get("rotation", 0),
                    "preview-flip-h": file_metadata.get("flip_h", False),
                    "preview-flip-v": file_metadata.get("flip_v", False),
                    "preview-denoise": file_metadata.get("denoise", "off"),
                    "preview-sharpen": file_metadata.get("sharpen", "off"),
                    "preview-effect-file": file_metadata.get("effect_file", ""),
                },
            )

            # Cropping needs the frame size.
            video_width = video_height = None
            if crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0:
                video_width, video_height = probes.get("size") or (None, None)

            # The unified video filter string from the per-file values, also
            # used when a copy job is re-encoded after all.
            try:
                video_filter = get_ffmpeg_filter_string(
                    filter_settings,
                    video_width=video_width,
                    video_height=video_height,
                )
            except CropError as error:
                # Converting without the crop the user asked for is a wrong
                # result, not a fallback.
                logger.error("Crop not applicable: %s", error)
                self.app.show_error_dialog(
                    _("The crop cannot be applied"),
                    _(
                        "“{name}” was not converted: its picture size could not be read, "
                        "or the crop removes the whole picture. Check the crop in the "
                        "editor and try again."
                    ).format(name=os.path.basename(input_file)),
                )
                return False

            # Filters need re-encoding; a copy job has none.
            env_vars["video_filter"] = "" if force_copy_video_enabled else video_filter

            # Validate the option grammar shared with the backend. The
            # transport text is parsed into argv, never executed as shell.
            raw_additional_options = settings.load_setting("additional-options", "")
            options_ok, additional_options = validate_additional_options(
                raw_additional_options
            )
            if not options_ok:
                error_message = additional_options
                logger.error("Rejected additional options: %s", error_message)
                GLib.idle_add(lambda msg=error_message: self.app.show_error_dialog(msg))
                return False

            env_vars["options"] = additional_options

            if video_width is not None and video_height is not None:
                env_vars["video_width"] = str(video_width)
                env_vars["video_height"] = str(video_height)

        except (OSError, ValueError) as error:
            logger.exception("Could not prepare the conversion")
            self.app.show_error_dialog(str(error))
            return False

        output_format = output_container(
            settings.load_setting("output-format-index", 0)
        )
        output_ext = "." + output_format

        input_ext = os.path.splitext(input_file)[1].lower()
        input_basename = os.path.splitext(os.path.basename(input_file))[0]
        input_dir = os.path.dirname(os.path.abspath(input_file))

        if input_ext == output_ext:
            # If input format is the same as output format, add "-converted" to the name
            output_basename = f"{input_basename}-converted{output_ext}"
        else:
            # If format is different, just change the extension
            output_basename = f"{input_basename}{output_ext}"

        use_same_folder = (
            self.folder_combo.get_selected() == 0
        )  # 0 = "Same folder as original file"

        if use_same_folder:
            output_folder = input_dir
        else:
            # Custom folder selected
            output_folder = self.output_folder_entry.get_text()
            if not output_folder:
                # If custom folder is empty, use input directory
                output_folder = input_dir

        if not os.path.isabs(output_folder):
            output_folder = os.path.abspath(output_folder)

        job_info = next(
            (
                info
                for info in getattr(self.app, "active_conversions", [])
                if info.get("file_path") == input_file
            ),
            {},
        )
        claimed = self.app.claimed_outputs

        # Find a name neither on disk nor reserved by a parallel job
        full_output_path = os.path.join(output_folder, output_basename)
        if os.path.exists(full_output_path) or full_output_path in claimed:
            # Find an available filename by adding a counter
            base_name = os.path.splitext(output_basename)[0]
            extension = os.path.splitext(output_basename)[1]
            counter = 1
            while True:
                output_basename = f"{base_name}_{counter}{extension}"
                full_output_path = os.path.join(output_folder, output_basename)
                if (
                    not os.path.exists(full_output_path)
                    and full_output_path not in claimed
                ):
                    break
                counter += 1

        # Reserved until conversion_completed releases it with the job.
        if job_info:
            job_info["output_path"] = full_output_path
            claimed.add(full_output_path)
        env_vars["output_file"] = full_output_path

        env_vars["output_format"] = output_format

        cmd = [CONVERT_SCRIPT_PATH, input_file]

        # Delete original setting
        delete_original = self.delete_original_check.get_active()

        job_id = job_info.get("job_id")
        cancel_event = job_info.get("cancel_event") or threading.Event()

        force_copy_video = env_vars.get("force_copy_video") == "1"
        # Re-encoding uses the settings copy mode had skipped, the accelerator
        # included: choosing "re-encode" is not a request to fall back to the
        # processor. Offered when MP4 refuses the copied tracks, and taken by
        # a trim whose cut is not on a keyframe (engine/segment_batch.py).
        reencode_env = None
        if force_copy_video:
            reencode_env = dict(env_vars)
            reencode_env.pop("force_copy_video", None)
            reencode_env.pop("force_software", None)
            reencode_env.update(self._encoding_environment(gpu_override, settings))
            video_resolution = settings.load_setting("video-resolution", "")
            if video_resolution:
                reencode_env["video_resolution"] = video_resolution
            reencode_env["video_fps"] = settings.load_setting("video-fps", "")
            if file_metadata.get("stabilize"):
                reencode_env["video_stabilize"] = "1"
            if file_metadata.get("source_hdr", "auto") != "auto":
                reencode_env["source_hdr"] = file_metadata["source_hdr"]
            reencode_env["video_filter"] = video_filter
        conversion_context = {
            "preset_source": preset_snapshot["source"] if preset_snapshot else None,
            "cmd": cmd,
            "env_vars": env_vars,
            "job_id": job_id,
            "cancel_event": cancel_event,
            "delete_original": delete_original,
            "skip_trash": bool(
                self.app.settings_store.load_setting("skip-trash", False)
            ),
            "full_output_path": full_output_path,
            "input_file": input_file,
            "input_basename": input_basename,
            "input_ext": input_ext,
            "output_ext": output_ext,
            "output_folder": output_folder,
            "trim_segments": trim_segments,
            # Size jobs replace trim_segments with parts of the whole file.
            "cut": bool(trim_segments),
            "output_mode": output_mode,
            "reencode_env": reencode_env,
        }
        # A size target decides copy or encode itself, so it comes before the
        # copy-mode MP4 check.
        size_target = self._size_target(settings)
        if size_target is not None:
            conversion_context.update(
                size_target=size_target, encode_env=reencode_env or env_vars
            )
            return self._start_size_job(conversion_context)
        if force_copy_video and output_format == "mp4":
            is_compatible, incompatible_streams = probes.get("mp4") or (True, [])
            if not is_compatible:
                # Package all needed variables
                def show_compatibility_warning() -> None:
                    if cancel_event.is_set():
                        self.app.conversion_completed(
                            False, file_path=input_file, job_id=job_id
                        )
                        return
                    dialog = Adw.AlertDialog()
                    dialog.set_heading(_("These tracks can't be copied into MP4"))
                    dialog.set_body(
                        _(
                            "“Copy video without reencoding” keeps the original "
                            "streams as they are, and the MP4 container does not "
                            "accept the ones listed below."
                        )
                    )
                    dialog.set_extra_child(
                        self._build_incompatible_streams_child(incompatible_streams)
                    )

                    if hasattr(dialog, "set_prefer_wide_layout"):  # libadwaita 1.6
                        dialog.set_prefer_wide_layout(True)

                    dialog.add_response("cancel", _("Cancel"))
                    dialog.add_response("reencode", _("Reencode (recommended)"))
                    dialog.add_response("proceed", _("Copy anyway"))
                    dialog.set_response_appearance(
                        "reencode", Adw.ResponseAppearance.SUGGESTED
                    )
                    dialog.set_response_appearance(
                        "proceed", Adw.ResponseAppearance.DESTRUCTIVE
                    )
                    dialog.set_default_response("reencode")
                    dialog.set_close_response("cancel")

                    def on_response(dialog, response) -> None:
                        if cancel_event.is_set() or response == "cancel":
                            cancel_event.set()
                            self.app.conversion_completed(
                                False, file_path=input_file, job_id=job_id
                            )
                            return
                        if response == "reencode":
                            conversion_context["env_vars"] = reencode_env
                        # Direct invocation: this callback is already on GTK's
                        # main loop. Do not schedule a True-returning idle task.
                        try:
                            self._continue_conversion(conversion_context)
                        except Exception as error:
                            logger.exception("Could not resume conversion")
                            self.app.show_error_dialog(str(error))
                            self.app.conversion_completed(
                                False, file_path=input_file, job_id=job_id
                            )

                    dialog.connect("response", on_response)
                    # Quitting closes it: the job it holds must not keep the
                    # application waiting for an answer nobody can give.
                    self._decision_dialogs.add(dialog)
                    dialog.connect("closed", self._decision_dialogs.discard)
                    dialog.present(self.app.window)

                GLib.idle_add(show_compatibility_warning)
                return (
                    True  # The active job remains reserved while awaiting a decision.
                )

        return self._continue_conversion(conversion_context)

    def close_decision_dialogs(self) -> None:
        """Answer every open question about a job with its close response."""
        for dialog in list(self._decision_dialogs):
            dialog.force_close()

    def additional_options_error(self, files) -> str | None:
        """Why the FFmpeg options of a queued file are rejected, if they are."""
        for path in files:
            try:
                resolved = freeze(
                    self.app.settings_store.settings,
                    deepcopy(self.file_metadata.get(path, {})),
                )
            except (OSError, ValueError, PresetError):
                continue  # The job reports its own recipe error.
            settings = SettingsOverride(self.app.settings_store, resolved["settings"])
            accepted, message = validate_additional_options(
                settings.load_setting("additional-options", "")
            )
            if not accepted:
                return message
        return None

    def _build_incompatible_streams_child(self, streams):
        """Build the list of MP4-incompatible tracks shown in the warning dialog."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        # Give the dialog room to breathe: AlertDialog sizes itself around the
        # extra child, and the default width squeezes these labels into 3 lines.
        box.set_size_request(480, -1)

        group = Adw.PreferencesGroup()
        group.set_title(_("Incompatible tracks"))

        for stream in streams:
            codec = (stream.get("codec_name") or "").upper()
            row = Adw.ActionRow(title=codec or _("Unknown codec"))

            if stream.get("codec_type") == "video":
                kind = _("Video track {0}").format(stream.get("index", 1))
                icon_name = "video-x-generic-symbolic"
            else:
                kind = _("Audio track {0}").format(stream.get("index", 1))
                icon_name = "audio-volume-high-symbolic"

            details = [kind]
            language = stream.get("language")
            if language:
                details.append(language.upper())
            title = stream.get("title")
            if title:
                details.append(title)
            row.set_subtitle(" · ".join(details))

            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.add_css_class("dim-label")
            row.add_prefix(icon)

            badge = Gtk.Label(label=_("not supported by MP4"))
            badge.add_css_class("caption")
            badge.add_css_class("warning")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)

            group.add(row)

        box.append(group)

        hint = Gtk.Label()
        hint.set_wrap(True)
        hint.set_xalign(0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_label(
            _(
                "Reencode converts these tracks so the MP4 plays anywhere. "
                "Copying anyway is faster, but the result may have no sound or "
                "may not play at all — choose MKV as the output format to keep "
                "the original tracks untouched."
            )
        )
        box.append(hint)

        return box

    def _size_target(self, settings):
        """{"bytes", "strategy"} when converted videos must fit a size, else None."""
        target_id = settings.load_setting("size-target", "")
        if not target_id:
            return None
        return {
            "bytes": target_bytes(
                target_id, float(settings.load_setting("size-target-mb", 50))
            ),
            "strategy": settings.load_setting("size-strategy", "auto"),
        }

    def _start_size_job(self, context):
        """Plan off the GTK thread (it probes the file), then start the job."""
        input_file, job_id = context["input_file"], context.get("job_id")

        def dispatch(job):
            try:
                if job["route"] == "batch":
                    start_segment_batch(self, job)
                else:
                    self._continue_conversion(job)
            except (OSError, ValueError) as error:
                logger.exception("Size job could not start")
                fail(str(error))
            return GLib.SOURCE_REMOVE

        def fail(message):
            self.app.show_error_dialog(
                _("This video cannot be made to fit the size"), message
            )
            self.app.conversion_completed(False, file_path=input_file, job_id=job_id)
            return GLib.SOURCE_REMOVE

        def plan():
            try:
                job = prepare_size_job(context)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                logger.warning("Size target not applied: %s", error)
                GLib.idle_add(fail, str(error))
                return
            GLib.idle_add(dispatch, job)

        threading.Thread(target=plan, daemon=True).start()
        return True

    def _continue_conversion(self, context):
        # The naming and segment fields are read by start_segment_batch, which
        # takes the whole context; only these are used here.
        cmd = context["cmd"]
        env_vars = context["env_vars"]
        delete_original = context["delete_original"]
        input_file = context["input_file"]
        trim_segments = context["trim_segments"]

        logger.debug("Conversion command: %s", " ".join(cmd))

        if context.get("cancel_event") is not None and context["cancel_event"].is_set():
            self.app.conversion_completed(
                False, file_path=input_file, job_id=context.get("job_id")
            )
            return False
        # Several cuts go to a segment batch, and so does one cut copied
        # without re-encoding: the batch checks that it starts on a keyframe
        # and re-encodes it otherwise.
        if len(trim_segments) > 1 or (
            trim_segments and env_vars.get("force_copy_video") == "1"
        ):
            return start_segment_batch(self, context)

        # Single segment or no segments - use standard conversion
        # Calculate segment duration for single-segment trimming for accurate progress
        segment_duration = None
        if len(trim_segments) == 1:
            trim_start = trim_segments[0]["start"]
            segment_duration = trim_segments[0]["end"] - trim_start
            trim = f"-t {self._format_time_ffmpeg(segment_duration)}"
            if trim_start > 0:
                trim = f"-ss {self._format_time_ffmpeg(trim_start)} {trim}"
            env_vars = {
                **env_vars,
                "options": " ".join(
                    filter(None, [env_vars.get("options", "").strip(), trim])
                ),
            }

        # input_file lets the queue match the job when it completes.
        run_with_progress_dialog(
            self.app,
            cmd,
            f"{os.path.basename(input_file)}",
            input_file,  # Always pass full path for queue tracking
            delete_original,
            env_vars,
            segment_duration=segment_duration,
            preset_source=context.get("preset_source"),
            job_id=context.get("job_id"),
            cancel_event=context.get("cancel_event"),
            partial=context.get("cut", False),
            skip_trash=context.get("skip_trash", False),
        )

        return True

    def _format_time_ffmpeg(self, seconds):
        """HH:MM:SS.ffffff for ffmpeg, rounded to the microsecond FFmpeg keeps.

        Players hand over exact cut positions; truncating 3.3 (stored as
        3.2999…) to milliseconds cut a frame early.
        """
        whole, micro = divmod(round(seconds * 1_000_000), 1_000_000)
        return (
            f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}.{micro:06d}"
        )
