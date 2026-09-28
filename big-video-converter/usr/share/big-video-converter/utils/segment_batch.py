"""Sequential segment jobs with shared cancellation and owned temporary files."""

import gettext
import json
import logging
import math
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from utils.conversion import (
    TEXT_ONLY_CONTAINERS,
    TEXT_SUBTITLE_CODECS,
    _failure_reason,
    call_on_main,
    notify_completion,
    run_with_progress_dialog,
)
from utils.ffmpeg_path import get_ffmpeg_executable, get_ffprobe_executable
from utils.media_validation import (
    ConversionResult,
    FileIdentity,
    probe_media,
    publish_output,
    remove_original,
    run_cancellable,
)
from utils.size_target import describe_plan, keyframe_cuts, probe_packets

_ = gettext.gettext


class SegmentBatchError(RuntimeError):
    """A batch failure whose message is already a sentence for the user."""
ngettext = gettext.ngettext
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


def _left_out_subtitles(source, asked, used, extension):
    """How many subtitle tracks of the source the published parts lack.

    A picture subtitle (PGS, DVD) has no text form, so it becomes no sidecar
    and has no place in MP4, MOV or WebM; joined cuts carry no subtitles at
    all when the job had to switch them off.
    """
    if (asked.get("subtitle_extract") or "extract") == "none":
        return 0
    streams = [s for s in probe_media(source)["streams"] if s.get("codec_type") == "subtitle"]
    if (used.get("subtitle_extract") or "extract") == "none":
        return len(streams)
    if used.get("subtitle_extract") == "embedded" and extension.lower() not in TEXT_ONLY_CONTAINERS:
        return 0
    return sum(s.get("codec_name") not in TEXT_SUBTITLE_CODECS for s in streams)


def _keyframe_starts(source, segments, cancel_event):
    """Move each cut's start back to the video keyframe a stream copy starts on.

    Each cut gains "keyframe", where its output really begins; "start" is the
    -ss value that makes FFmpeg seek to that keyframe. Also returns the length
    of one video frame in seconds.
    """
    ffprobe = get_ffprobe_executable()
    data = probe_media(source, executable=ffprobe)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), {})
    rate = next((r for r in (video.get("avg_frame_rate"), video.get("r_frame_rate"))
                 if r and not r.startswith("0")), "25/1")
    numerator, _slash, denominator = rate.partition("/")
    frame = float(denominator or 1) / float(numerator)
    # -read_intervals takes stream timestamps; -ss counts from the file start.
    offset = float(data["format"].get("start_time") or 0)
    # Outside MP4/MOV, MXF and NUT, FFmpeg seeks 3/23 s before -ss when the
    # video has B-frames (fftools/ffmpeg_demux.c), so -ss must lead the
    # keyframe by as much or the seek lands on the keyframe before it.
    by_pts = data["format"].get("format_name", "").split(",")[0] in ("mov", "mxf", "nut")
    lead = 0.0 if by_pts or not any(s.get("has_b_frames") for s in data["streams"]) else 3 / 23
    cuts = []
    for segment in segments:
        start = offset + segment["start"]
        keyframes = []
        # A window before the cut, and the whole opening only for a longer GOP.
        for interval in (f"{max(0.0, start - 30):.6f}%{start + 0.001:.6f}", f"%{start + 0.001:.6f}"):
            probe = run_cancellable(
                [ffprobe, "-v", "error", "-select_streams", "v:0", "-read_intervals", interval,
                 "-show_entries", "packet=pts_time,flags", "-of", "json", source],
                cancel_event, timeout=120, task="finding the keyframes")
            probe.check_returncode()
            packets = json.loads(probe.stdout).get("packets", [])
            keyframes = [float(p["pts_time"]) for p in packets
                         if "K" in p.get("flags", "") and p.get("pts_time", "N/A") != "N/A"
                         and float(p["pts_time"]) + lead <= start]
            if keyframes:
                break
        if not keyframes:
            cuts.append({**segment, "keyframe": segment["start"]})
            continue
        keyframe = max(keyframes) - offset
        # Rounded up to the millisecond, never past the cut, so the seek lands
        # on this keyframe rather than the one before it.
        seek = min(math.ceil((keyframe + lead) * 1000) / 1000, segment["start"])
        cuts.append({"start": max(0.0, seek), "end": segment["end"], "keyframe": max(0.0, keyframe)})
    return tuple(cuts), frame


def _copy_parts(full, cuts, work_dir, extension, cancel_event):
    """Copy the cuts of ``full`` out as separate files, in one pass.

    The segment muxer starts each file at the keyframe itself. Seeking with
    -ss/-t instead cuts by decode time, so the next keyframe and its B-frames
    (decoded before they are shown) ended the previous part as well.
    """
    if cancel_event.is_set():
        raise InterruptedError("Segment batch cancelled")
    if len(cuts) == 1:
        return [full]
    folder = Path(work_dir) / "parts"
    folder.mkdir(exist_ok=True)
    for old in folder.iterdir():
        old.unlink()
    options = ["-segment_format_options", "movflags=+faststart"] if extension in (
        ".mp4", ".mov", ".m4v") else []
    # The segment muxer reads the path as a template: a '%' of the folder's
    # own would be taken for the part number.
    template = str(folder).replace("%", "%%") + f"/segment-%04d{extension}"
    run_cancellable([get_ffmpeg_executable(), "-nostdin", "-v", "error", "-y", "-i", full,
                     "-map", "0", "-c", "copy", "-f", "segment", "-reset_timestamps", "1",
                     "-segment_times", ",".join(f"{start:.6f}" for start, _end in cuts[1:]),
                     *options, template],
                    cancel_event, timeout=3600, task="splitting into parts").check_returncode()
    paths = sorted(str(path) for path in folder.iterdir())
    if len(paths) != len(cuts):
        raise RuntimeError(f"Expected {len(cuts)} parts, the split made {len(paths)}")
    return paths


def _split_to_size(full, limit, work_dir, extension, cancel_event):
    """Split an encoded file into even parts under ``limit`` bytes.

    The index estimate is close but not exact, so parts are measured and the
    cuts made again with the overshoot taken off the limit when one is over.
    """
    packets = probe_packets(full, cancel_event)
    budget = limit
    for _attempt in range(4):
        cuts = keyframe_cuts(packets, budget)
        if cuts is None:
            raise SegmentBatchError(_(
                "The keyframes of the video are too far apart to split it into "
                "parts under the size limit."))
        paths = _copy_parts(full, cuts, work_dir, extension, cancel_event)
        over = max(os.path.getsize(path) for path in paths) - limit
        if over <= 0:
            return paths, cuts
        budget -= over + 16 * 1024
    raise SegmentBatchError(_("The video could not be split into parts under the size limit."))


def _bitmap_timeline(cuts):
    """Bitstream filters that keep a bitmap subtitle packet only when it
    overlaps a cut, and move it to that cut's place in the joined file.

    A PGS or DVD subtitle is a picture: it cannot be rewritten cue by cue like
    text, so each packet is kept whole with its timestamps clamped to the cut
    (see utils/subtitle_timing.py for a single cut). A zero duration, common
    in PGS, still counts as touching its own instant.
    """
    keep, pts, dts, length = [], "PTS", "DTS", "DURATION"
    offset = 0.0
    windows = []
    for cut in cuts:
        windows.append((f"{cut['start']:.6f}", f"{cut['end']:.6f}", f"{offset:.6f}"))
        offset += cut["end"] - cut["start"]
    for start, end, shift in reversed(windows):
        keep.append(f"lt(pts*tb,{end})*gt((pts+max(duration,1))*tb,{start})")
        inside = f"lt(PTS*TB,{end})*gt((PTS+max(DURATION,1))*TB,{start})"
        pts = f"if({inside},max(PTS-{start}/TB,0)+{shift}/TB,{pts})"
        dts = f"if({inside},max(DTS-{start}/TB,0)+{shift}/TB,{dts})"
        length = (f"if({inside},max(0,min(PTS+DURATION,{end}/TB)-max(PTS,{start}/TB)),"
                  f"{length})")
    return (f"noise=amount=0:drop='not({'+'.join(keep)})',"
            f"setts=pts='{pts}':dts='{dts}':duration='{length}'")


def _embed_subtitles(app, row, context, cuts, joined, work_dir, duration, cancel_event):
    """Mux the source's subtitles, clipped to the cuts, into the joined file.

    Text tracks are clipped cue by cue. Bitmap tracks (PGS, DVD) are copied
    packet by packet, and only Matroska can hold them.
    """
    from utils.subtitle_processor import SubtitleProcessor
    source = context["input_file"]
    tracks = SubtitleProcessor(source, work_dir, "merged", cuts, work_dir,
                               "embedded", cancel_event=cancel_event).process()
    bitmaps = [s["index"] for s in probe_media(source)["streams"]
               if s.get("codec_type") == "subtitle"
               and s.get("codec_name") not in TEXT_SUBTITLE_CODECS]
    if bitmaps and context["output_ext"].lower() in TEXT_ONLY_CONTAINERS:
        call_on_main(row.add_output_text, ngettext(
            "{0} cannot hold picture subtitles (PGS, DVD): {1} track left out. "
            "Choose MKV to keep it.",
            "{0} cannot hold picture subtitles (PGS, DVD): {1} tracks left out. "
            "Choose MKV to keep them.", len(bitmaps),
        ).format(context["output_ext"].lstrip(".").upper(), len(bitmaps)))
        bitmaps = []
    if not tracks and not bitmaps:
        return joined
    output = str(Path(work_dir) / f"subtitled{context['output_ext']}")
    encoder = {".mp4": "mov_text", ".m4v": "mov_text", ".mov": "mov_text",
               ".webm": "webvtt"}.get(context["output_ext"].lower(), "srt")
    cmd = [get_ffmpeg_executable(), "-nostdin", "-n", "-i", joined]
    for path, _language in tracks:
        cmd += ["-i", path]
    if bitmaps:
        # Read the source only up to the last cut; language, title and the
        # forced/default flags come with each copied stream.
        cmd += ["-t", f"{max(cut['end'] for cut in cuts):.6f}", "-i", source]
    cmd += ["-map", "0"]
    for number in range(1, len(tracks) + 1):
        cmd += ["-map", f"{number}:s:0"]
    for index in bitmaps:
        cmd += ["-map", f"{len(tracks) + 1}:{index}"]
    cmd += ["-c", "copy", "-c:s", encoder]
    for number, (path, language) in enumerate(tracks):
        cmd += [f"-metadata:s:s:{number}", f"language={language}"]
        if path.endswith(".forced.srt"):
            cmd += [f"-disposition:s:{number}", "forced"]
    if bitmaps:
        timeline = _bitmap_timeline(cuts)
        for number in range(len(tracks), len(tracks) + len(bitmaps)):
            cmd += [f"-c:s:{number}", "copy", f"-bsf:s:{number}", timeline]
    cmd.append(output)
    env = {**context["env_vars"], "output_file": output, "options": ""}
    result = run_with_progress_dialog(
        app, cmd, os.path.basename(context["input_file"]), None, False, env,
        wait_for_completion=True, is_segment_batch=True, segment_duration=duration,
        job_id=context.get("job_id"), cancel_event=cancel_event, progress_item=row,
        output_file=output,
    )
    if not result.success:
        if result.cancelled:
            raise InterruptedError(result.error)
        raise RuntimeError(result.error or "Adding the subtitles failed")
    return output


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
    # Splitting to a size: the cuts (or the whole video) are encoded once with
    # a short GOP, and that file is split by copying, into even parts.
    split_size = context.get("split_size")
    # A single cut (a copied trim) is one part published under the job's name.
    single = len(segments) == 1 and not split_size
    if single:
        mode = "split"
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

    reencoded = False

    def work():
        nonlocal reencoded
        outputs = []
        reason = None
        if context.get("plan") is not None:
            call_on_main(row.add_output_text, describe_plan(context))
        result = ConversionResult(False, -1)
        try:
            with tempfile.TemporaryDirectory(prefix=".bvc-segments-", dir=context["output_folder"]) as work_dir:
                if context["env_vars"].get("only_extract_subtitles") == "1":
                    from utils.subtitle_processor import SubtitleProcessor
                    groups = [segments] if mode == "join" else [(segment,) for segment in segments]
                    for index, group in enumerate(groups):
                        if cancel_event.is_set():
                            raise InterruptedError("Segment batch cancelled")
                        name = (os.path.basename(context["full_output_path"]) if mode == "join" or single else
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
                env_vars = context["env_vars"]
                copy_mode = env_vars.get("force_copy_video") == "1"
                cuts = segments
                if copy_mode:
                    # A stream copy can only start on a keyframe; from anywhere
                    # else it would carry seconds the user cut away. Copy only
                    # when every cut starts within a frame of one, otherwise
                    # re-encode the job with the user's encoding settings.
                    try:
                        keyframe_cuts, frame = _keyframe_starts(source, segments, cancel_event)
                    except (OSError, subprocess.SubprocessError, ValueError, KeyError,
                            ZeroDivisionError) as error:
                        # Some files FFprobe cannot read still copy fine.
                        logger.warning("Keyframes unavailable, cutting as asked: %s", error)
                    else:
                        late = next(((index, asked["start"] - cut["keyframe"])
                                     for index, (asked, cut) in enumerate(zip(segments, keyframe_cuts, strict=True))
                                     if asked["start"] - cut["keyframe"] > frame + 0.001), None)
                        if late is None:
                            cuts = keyframe_cuts
                        else:
                            copy_mode = False
                            reencoded = True
                            env_vars = context["reencode_env"]
                            call_on_main(row.add_output_text, _(
                                "Part {0} starts {1:.3f} s after a keyframe, where a stream "
                                "copy would have to begin. Re-encoding the video instead, so "
                                "every part starts exactly where it was cut; this takes longer."
                            ).format(late[0] + 1, late[1]))
                duration = sum(cut["end"] - cut["start"] for cut in cuts)
                paths = []
                if split_size:
                    full = str(Path(work_dir) / f"encoded{context['output_ext']}")
                    env = dict(env_vars)
                    env.update(output_file=full,
                               keyframe_interval=f"{context['keyframe_interval']:g}")
                    if len(segments) > 1:
                        env["segments"] = ",".join(
                            f"{cut['start']:.6f}+{cut['end'] - cut['start']:.6f}" for cut in segments)
                        # Joined cuts have no single clock to extract subtitles by.
                        env["subtitle_extract"] = "none"
                        env_vars = {**env_vars, "subtitle_extract": "none"}
                    else:
                        trim = (f"-ss {page._format_time_ffmpeg(segments[0]['start'])} "
                                f"-t {page._format_time_ffmpeg(duration)}")
                        env["options"] = " ".join(filter(None, [env.get("options", "").strip(), trim]))
                    result = run_with_progress_dialog(
                        app, context["cmd"][:2], os.path.basename(source),
                        None, False, env, wait_for_completion=True,
                        is_segment_batch=True, segment_duration=duration,
                        preset_source=context.get("preset_source"),
                        job_id=job_id, cancel_event=cancel_event,
                        progress_item=row, source_file=source,
                    )
                    if not result.success:
                        if result.cancelled:
                            raise InterruptedError(result.error)
                        raise RuntimeError(result.error or "The encode to split failed")
                    call_on_main(row.update_status, _("Splitting into parts…"))
                    paths, part_cuts = _split_to_size(full, split_size, work_dir,
                                                      context["output_ext"], cancel_event)
                    # Part times for subtitle extraction, in the source's clock
                    # (a single cut or the whole video starts at its own start).
                    offset = segments[0]["start"]
                    cuts = [{"start": offset + start,
                             "end": offset + (duration if math.isinf(end) else end)}
                            for start, end in part_cuts]
                elif mode == "join" and not copy_mode:
                    # One encode with every cut as its own input (see the
                    # script's "segments"): exact seams, no priming gaps.
                    joined = str(Path(work_dir) / f"joined{context['output_ext']}")
                    env = dict(env_vars)
                    env["output_file"] = joined
                    env["subtitle_extract"] = "none"
                    env["segments"] = ",".join(
                        f"{cut['start']:.6f}+{cut['end'] - cut['start']:.6f}" for cut in cuts)
                    result = run_with_progress_dialog(
                        app, context["cmd"][:2], os.path.basename(source),
                        None, False, env, wait_for_completion=True,
                        is_segment_batch=True, segment_duration=duration,
                        preset_source=context.get("preset_source"),
                        job_id=job_id, cancel_event=cancel_event,
                        progress_item=row, source_file=source,
                    )
                    if not result.success:
                        if result.cancelled:
                            raise InterruptedError(result.error)
                        raise RuntimeError(result.error or "Joining the segments failed")
                else:
                    for index, cut in enumerate(cuts):
                        if cancel_event.is_set():
                            raise InterruptedError("Segment batch cancelled")
                        path = str(Path(work_dir) / f"segment-{index:04d}{context['output_ext']}")
                        env = dict(env_vars)
                        env["output_file"] = path
                        length = cut["end"] - cut["start"]
                        trim = f"-ss {page._format_time_ffmpeg(cut['start'])} -t {page._format_time_ffmpeg(length)}"
                        env["options"] = " ".join(filter(None, [env.get("options", "").strip(), trim]))
                        stage = run_with_progress_dialog(
                            app, context["cmd"][:2], os.path.basename(source),
                            None, False, env, wait_for_completion=True,
                            is_segment_batch=True, segment_duration=length,
                            preset_source=context.get("preset_source"),
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
                    if copy_mode:
                        concat_list = Path(work_dir) / "segments.ffconcat"
                        # All filenames are generated ASCII basenames, not user
                        # paths. No unescaped apostrophes can enter ffconcat syntax.
                        concat_list.write_text("ffconcat version 1.0\n" + "".join(
                            f"file '{Path(path).name}'\n" for path in paths), encoding="utf-8")
                        joined = str(Path(work_dir) / f"joined{context['output_ext']}")
                        cmd = [get_ffmpeg_executable(), "-nostdin", "-n", "-f", "concat",
                               "-safe", "1", "-i", str(concat_list), "-map", "0:v:0",
                               "-map", "0:a?", "-map", "0:s?", "-c", "copy", joined]
                        env = dict(env_vars)
                        env["output_file"] = joined
                        env["options"] = ""
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
                    elif env_vars.get("subtitle_extract") == "embedded":
                        joined = _embed_subtitles(app, row, context, cuts, joined,
                                                  work_dir, duration, cancel_event)
                    if cancel_event.is_set():
                        raise InterruptedError("Segment batch cancelled")
                    outputs.append(_publish_available(joined, context["full_output_path"]))
                    if env_vars.get("subtitle_extract") == "extract":
                        from utils.subtitle_processor import SubtitleProcessor
                        processor = SubtitleProcessor(source, context["output_folder"],
                            os.path.basename(outputs[0]), list(cuts), work_dir, "extract", cancel_event=cancel_event)
                        processor.process()
                else:
                    for index, path in enumerate(paths):
                        if cancel_event.is_set():
                            raise InterruptedError("Segment batch cancelled")
                        alone = single or (split_size and len(paths) == 1)
                        desired = context["full_output_path"] if alone else os.path.join(
                            context["output_folder"],
                            f"{context['input_basename']}-part{index + 1}{context['output_ext']}")
                        final = _publish_available(path, desired)
                        outputs.append(final)
                        if env_vars.get("subtitle_extract") == "extract":
                            from utils.subtitle_processor import SubtitleProcessor
                            processor = SubtitleProcessor(source, context["output_folder"],
                                os.path.basename(final), [cuts[index]], work_dir, "extract", cancel_event=cancel_event)
                            processor.process()
                if context["delete_original"]:
                    try:
                        if identity is None:
                            raise ValueError("Source identity cannot authorize removal")
                        if _left_out_subtitles(source, context["env_vars"], env_vars,
                                               context["output_ext"]):
                            raise ValueError(_(
                                "The output does not carry every subtitle track of the original"))
                        remove_original(source, identity, outputs, cancel_event)
                    except InterruptedError:
                        raise
                    except Exception as error:  # noqa: BLE001 - any failure must keep the original and be reported without failing the finished batch
                        logger.warning("Original preserved: %s", error)
                        call_on_main(row.add_output_text, _("Original preserved: {0}").format(error))
                result = ConversionResult(True, 0, outputs[0] if len(outputs) == 1 else None)
        except InterruptedError as error:
            cancel_event.set()
            result = ConversionResult(False, -1, cancelled=True, error=str(error))
        except Exception as error:
            logger.exception("Segment batch failed")
            result = ConversionResult(False, result.returncode or -1, error=str(error))
            # The stage errors carry FFmpeg's evidence; the row shows one sentence.
            reason = (str(error) if isinstance(error, SegmentBatchError)
                      else _failure_reason(str(error).splitlines()) or None)

        finally:
            call_on_main(finish_result, result, outputs, reason)

    def finish_result(result, outputs, reason=None):
        app.conversions_running = max(0, app.conversions_running - 1)
        row.process = None
        row.is_segment_batch = False
        if result.cancelled:
            row.mark_cancelled()
            app.progress_page.mark_conversion_complete(row.conversion_id, False)
        elif result.success:
            row.mark_success()
            if reencoded:
                row.update_status(_("Completed — re-encoded: a cut was not on a keyframe"))
        else:
            row.mark_failure(reason, result.error or None)
            row.add_output_text(result.error)
        if not hasattr(app, "completed_conversions"):
            app.completed_conversions = []
        app.completed_conversions.append({"input_file": source,
            "output_file": result.output_file, "output_files": outputs,
            "success": result.success, "cancelled": result.cancelled,
            "job_id": job_id})
        if not result.cancelled:
            count = len(outputs) or len(segments)
            if split_size and len(outputs) > 1:
                body = (ngettext(
                    "Split into {0} part that fits the size limit. The video was re-encoded "
                    "because a cut did not start on a keyframe.",
                    "Split into {0} parts that fit the size limit. The video was re-encoded "
                    "because a cut did not start on a keyframe.", count) if reencoded else ngettext(
                    "Split into {0} part that fits the size limit.",
                    "Split into {0} parts that fit the size limit.", count)).format(count)
            elif single or len(outputs) == 1:
                body = (_("Conversion completed successfully! The video was re-encoded because "
                          "a cut did not start on a keyframe.") if reencoded else
                        _("Conversion completed successfully!"))
            elif mode == "join":
                body = (ngettext(
                    "All {0} segment has been joined successfully! The video was re-encoded "
                    "because a cut did not start on a keyframe.",
                    "All {0} segments have been joined successfully! The video was re-encoded "
                    "because a cut did not start on a keyframe.", count) if reencoded else ngettext(
                    "All {0} segment has been joined successfully!",
                    "All {0} segments have been joined successfully!", count)).format(count)
            else:
                body = (ngettext(
                    "All {0} segment has been processed successfully! The video was re-encoded "
                    "because a cut did not start on a keyframe.",
                    "All {0} segments have been processed successfully! The video was re-encoded "
                    "because a cut did not start on a keyframe.", count) if reencoded else ngettext(
                    "All {0} segment has been processed successfully!",
                    "All {0} segments have been processed successfully!", count)).format(count)
            notify_completion(app, result, body=body)
        app.conversion_completed(result.success, file_path=source, job_id=job_id)

    threading.Thread(target=work, daemon=True).start()
    return True
