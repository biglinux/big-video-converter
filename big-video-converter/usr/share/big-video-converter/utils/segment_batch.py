"""Sequential segment jobs with shared cancellation and owned temporary files."""

import gettext
import logging
import os
from pathlib import Path
import tempfile
import threading

from utils.conversion import call_on_main, run_with_progress_dialog
from utils.ffmpeg_path import get_ffmpeg_executable
from utils.media_validation import (
    ConversionResult, FileIdentity, publish_output, trash_original,
)

_ = gettext.gettext
logger = logging.getLogger(__name__)


def _publish_available(staged: str, desired: str) -> str:
    """Reserve the final name with link(), not an exists()/overwrite sequence."""
    base, extension = os.path.splitext(desired)
    for counter in range(10000):
        candidate = desired if counter == 0 else f"{base}_{counter}{extension}"
        try:
            publish_output(staged, candidate)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError("Could not find an unused output filename")


def start_segment_batch(page, context):
    """Start one parent job; report exactly one aggregate result to the queue."""
    app = page.app
    source = context["input_file"]
    segments = tuple(dict(segment) for segment in context["trim_segments"])
    mode = context["output_mode"]
    if mode not in ("split", "join"):
        raise ValueError("Invalid segment output mode")
    if not segments or any(s["start"] < 0 or s["end"] <= s["start"] for s in segments):
        raise ValueError("Invalid segment interval")
    cancel_event = context.get("cancel_event") or threading.Event()
    job_id = context.get("job_id")
    identity = None
    try:
        identity = FileIdentity.capture(source)
    except ValueError:
        if not os.path.isfile(source):
            raise
    row = app.progress_page.add_conversion(os.path.basename(source), source, None)
    row.cancel_event = cancel_event
    row.job_id = job_id
    row.is_segment_batch = True
    # Keep shutdown inhibited between child stages as well as during them.
    app.conversions_running += 1

    def work():
        outputs = []
        result = ConversionResult(False, -1)
        try:
            with tempfile.TemporaryDirectory(prefix=".bvc-segments-", dir=context["output_folder"]) as work_dir:
                if context["env_vars"].get("only_extract_subtitles") == "1":
                    from utils.subtitle_processor import SubtitleProcessor
                    groups = [segments] if mode == "join" else [(segment,) for segment in segments]
                    for index, group in enumerate(groups):
                        if cancel_event.is_set():
                            raise InterruptedError("Segment batch cancelled")
                        name = (os.path.basename(context["full_output_path"]) if mode == "join" else
                                f"{context['input_basename']}-part{index + 1}{context['output_ext']}")
                        processor = SubtitleProcessor(source, context["output_folder"],
                            name, group, work_dir, "extract", cancel_event=cancel_event)
                        processor.process()
                        outputs.extend(processor.created_files)
                    if not outputs:
                        raise ValueError("No subtitle cues intersect the selected segments")
                    result = ConversionResult(True, 0, outputs[0] if len(outputs) == 1 else None)
                    # Extraction alone never authorizes removing the video.
                    return
                paths = []
                for index, segment in enumerate(segments):
                    if cancel_event.is_set():
                        raise InterruptedError("Segment batch cancelled")
                    path = str(Path(work_dir) / f"segment-{index:04d}{context['output_ext']}")
                    env = dict(context["env_vars"])
                    env["output_file"] = path
                    duration = segment["end"] - segment["start"]
                    trim = f"-ss {page._format_time_ffmpeg(segment['start'])} -t {page._format_time_ffmpeg(duration)}"
                    env["options"] = " ".join(filter(None, [env.get("options", "").strip(), trim]))
                    stage = run_with_progress_dialog(
                        app, context["cmd"][:2], os.path.basename(source),
                        None, False, env, wait_for_completion=True,
                        is_segment_batch=True, segment_duration=duration,
                        job_id=job_id, cancel_event=cancel_event,
                        progress_item=row, source_file=source,
                    )
                    if not stage.success:
                        result = stage
                        if stage.cancelled:
                            raise InterruptedError(stage.error)
                        raise RuntimeError(stage.error or f"Segment {index + 1} failed")
                    paths.append(path)
                if cancel_event.is_set():
                    raise InterruptedError("Segment batch cancelled")
                if mode == "join":
                    concat_list = Path(work_dir) / "segments.ffconcat"
                    # All filenames are generated ASCII basenames, not user
                    # paths. No unescaped apostrophes can enter ffconcat syntax.
                    concat_list.write_text("ffconcat version 1.0\n" + "".join(
                        f"file '{Path(path).name}'\n" for path in paths), encoding="utf-8")
                    joined = str(Path(work_dir) / f"joined{context['output_ext']}")
                    cmd = [get_ffmpeg_executable(), "-nostdin", "-n", "-f", "concat",
                           "-safe", "1", "-i", str(concat_list), "-map", "0:v:0",
                           "-map", "0:a?", "-map", "0:s?", "-c", "copy", joined]
                    env = dict(context["env_vars"])
                    env["output_file"] = joined
                    env["options"] = ""
                    duration = sum(s["end"] - s["start"] for s in segments)
                    result = run_with_progress_dialog(
                        app, cmd, os.path.basename(source), None, False, env,
                        wait_for_completion=True, is_segment_batch=True,
                        segment_duration=duration, job_id=job_id,
                        cancel_event=cancel_event, progress_item=row,
                        source_file=paths[0], output_file=joined,
                    )
                    if not result.success:
                        if result.cancelled:
                            raise InterruptedError(result.error)
                        raise RuntimeError(result.error or "Concatenation failed")
                    if cancel_event.is_set():
                        raise InterruptedError("Segment batch cancelled")
                    outputs.append(_publish_available(joined, context["full_output_path"]))
                    if context["env_vars"].get("subtitle_extract") == "extract":
                        from utils.subtitle_processor import SubtitleProcessor
                        processor = SubtitleProcessor(source, context["output_folder"],
                            os.path.basename(outputs[0]), list(segments), work_dir, "extract", cancel_event=cancel_event)
                        processor.process()
                else:
                    for index, path in enumerate(paths):
                        if cancel_event.is_set():
                            raise InterruptedError("Segment batch cancelled")
                        desired = os.path.join(context["output_folder"],
                            f"{context['input_basename']}-part{index + 1}{context['output_ext']}")
                        final = _publish_available(path, desired)
                        outputs.append(final)
                        if context["env_vars"].get("subtitle_extract") == "extract":
                            from utils.subtitle_processor import SubtitleProcessor
                            processor = SubtitleProcessor(source, context["output_folder"],
                                os.path.basename(final), [segments[index]], work_dir, "extract", cancel_event=cancel_event)
                            processor.process()
                if context["delete_original"]:
                    try:
                        if identity is None:
                            raise ValueError("Source identity cannot authorize removal")
                        trash_original(source, identity, outputs, cancel_event)
                    except InterruptedError:
                        raise
                    except Exception as error:
                        logger.warning("Original preserved: %s", error)
                        call_on_main(row.add_output_text, f"Original preserved: {error}")
                result = ConversionResult(True, 0, outputs[0] if len(outputs) == 1 else None)
        except InterruptedError as error:
            cancel_event.set()
            result = ConversionResult(False, -1, cancelled=True, error=str(error))
        except Exception as error:
            logger.exception("Segment batch failed")
            result = ConversionResult(False, result.returncode or -1, error=str(error))

        finally:
            call_on_main(finish_result, result, outputs)

    def finish_result(result, outputs):
        app.conversions_running = max(0, app.conversions_running - 1)
        row.process = None
        row.is_segment_batch = False
        if result.cancelled:
            row.mark_cancelled()
            app.progress_page.mark_conversion_complete(row.conversion_id, False)
        elif result.success:
            row.mark_success()
        else:
            row.mark_failure()
            row.add_output_text(result.error)
            row.update_status(_("Failed"))
        if not hasattr(app, "completed_conversions"):
            app.completed_conversions = []
        app.completed_conversions.append({"input_file": source,
            "output_file": result.output_file, "output_files": outputs,
            "success": result.success, "cancelled": result.cancelled,
            "job_id": job_id})
        app.conversion_completed(result.success, file_path=source, job_id=job_id)

    threading.Thread(target=work, daemon=True).start()
    return True
