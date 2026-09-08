"""Extract text subtitles once and clip cues to each selected interval."""

from decimal import Decimal, ROUND_HALF_UP
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from utils.ffmpeg_path import get_ffmpeg_executable
from utils.media_validation import probe_media, terminate_process_group

logger = logging.getLogger(__name__)
_TIMECODE = r"\d{2,}:\d{2}:\d{2},\d{3}"
_CUE = re.compile(
    rf"(?:^|\n\n)\d+\n({_TIMECODE})\s+-->\s+({_TIMECODE})[^\n]*\n(.*?)(?=\n\n|\Z)",
    re.DOTALL,
)


def _milliseconds(seconds) -> int:
    value = Decimal(str(seconds))
    if not value.is_finite() or value < 0:
        raise ValueError("Invalid subtitle time")
    return int((value * 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _parse_timecode(value: str) -> int:
    time, milliseconds = value.split(",")
    hours, minutes, seconds = map(int, time.split(":"))
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60 or len(milliseconds) != 3:
        raise ValueError("Invalid SRT timecode")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(milliseconds)


def _format_timecode(value: int) -> str:
    seconds, milliseconds = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _serialize(cues) -> str:
    return "\n\n".join(
        f"{index}\n{_format_timecode(start)} --> {_format_timecode(end)}\n{text}"
        for index, (start, end, text) in enumerate(cues, 1)
    ) + ("\n" if cues else "")


def _read_cues(path):
    content = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for match in _CUE.finditer(content.strip()):
        start, end = _parse_timecode(match[1]), _parse_timecode(match[2])
        if end > start:
            cues.append((start, end, match[3]))
    return cues


def _clip(cues, start, end, offset):
    if end <= start:
        raise ValueError("Subtitle interval must have a positive duration")
    result = []
    for cue_start, cue_end, text in cues:
        left, right = max(cue_start, start), min(cue_end, end)
        if right > left:
            result.append((left - start + offset, right - start + offset, text))
    return result


class SubtitleProcessor:
    """Process subtitle intervals without using language as stream identity."""

    def __init__(self, input_file, output_folder, output_basename,
                 trim_segments, temp_dir, subtitle_mode, *, cancel_event=None):
        self.input_file = input_file
        self.output_folder = output_folder
        self.output_basename = output_basename
        self.trim_segments = tuple(dict(s) for s in trim_segments)
        self.temp_dir = temp_dir
        self.subtitle_mode = subtitle_mode
        self.created_files = []
        self.cancel_event = cancel_event

    def process(self):
        embedded = []
        # Same naming as the conversion script: "<lang>[N][.forced]", counted
        # per name so neither a repeated language nor two forced tracks of one
        # language end up writing to the same sidecar.
        seen = {}
        for stream in self._get_subtitle_streams():
            self._check_cancelled()
            index = stream["index"]
            tags = stream.get("tags", {})
            language = tags.get("language") or "und"
            if not re.fullmatch(r"[A-Za-z]{2,3}", language):
                language = "und"
            forced = (stream.get("disposition", {}).get("forced") == 1
                      or "(Forced)" in tags.get("title", ""))
            key = (language, forced)
            seen[key] = count = seen.get(key, 0) + 1
            name = f"{language}{count if count > 1 else ''}{'.forced' if forced else ''}"
            content = self._merge_subtitle_stream(index, language)
            if content:
                output = self._save_merged_subtitles(content, name)
                self.created_files.append(output)
                if self.subtitle_mode == "embedded":
                    embedded.append((output, language))
        return embedded

    def _check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("Subtitle processing cancelled")

    def _get_subtitle_streams(self):
        return [s for s in probe_media(self.input_file)["streams"]
                if s.get("codec_type") == "subtitle"]

    def _merge_subtitle_stream(self, stream_index, language):
        # Private file; one extraction/parse per stream, not per segment.
        fd, path = tempfile.mkstemp(prefix="source-subtitle-", suffix=".srt", dir=self.temp_dir)
        os.close(fd)
        try:
            if not self._extract_segment_subtitle(path, stream_index):
                raise RuntimeError(f"Could not extract subtitle stream {stream_index}")
            cues = _read_cues(path)
            merged = []
            offset = 0
            for segment in self.trim_segments:
                start, end = _milliseconds(segment["start"]), _milliseconds(segment["end"])
                merged.extend(_clip(cues, start, end, offset))
                offset += end - start
            return _serialize(merged)
        finally:
            os.unlink(path)

    def _extract_segment_subtitle(self, output_file, stream_index):
        process = None
        try:
            self._check_cancelled()
            with tempfile.TemporaryFile() as error_log:
                process = subprocess.Popen(
                    [get_ffmpeg_executable(), "-nostdin", "-v", "error", "-y", "-i", self.input_file,
                     "-map", f"0:{int(stream_index)}", "-c:s", "srt", output_file],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=error_log, start_new_session=True,
                )
                deadline = time.monotonic() + 60
                while process.poll() is None:
                    self._check_cancelled()
                    if time.monotonic() > deadline:
                        raise TimeoutError("Subtitle extraction timed out")
                    time.sleep(0.05)
                if process.returncode:
                    error_log.seek(max(0, error_log.tell() - 2000))
                    logger.error("Subtitle extraction failed: %s", error_log.read().decode("utf-8", "replace"))
                return process.returncode == 0 and os.path.isfile(output_file)
        except InterruptedError:
            raise
        except (subprocess.SubprocessError, OSError, ValueError):
            logger.exception("Subtitle extraction failed")
            return False
        finally:
            if process is not None and process.poll() is None:
                terminate_process_group(process)

    def _filter_subtitle_range(self, subtitle_file, start_time, end_time, time_offset):
        return _serialize(_clip(_read_cues(subtitle_file), _milliseconds(start_time),
                                _milliseconds(end_time), _milliseconds(time_offset)))

    def _timecode_to_seconds(self, timecode):
        return _parse_timecode(timecode) / 1000

    def _seconds_to_timecode(self, seconds):
        return _format_timecode(_milliseconds(seconds))

    def _save_merged_subtitles(self, content, name):
        self._check_cancelled()
        folder = self.temp_dir if self.subtitle_mode == "embedded" else self.output_folder
        basename = "merged" if self.subtitle_mode == "embedded" else os.path.splitext(self.output_basename)[0]
        destination = os.path.join(folder, f"{basename}.{name}.srt")
        fd, staged = tempfile.mkstemp(prefix=".bvc-subtitle-", suffix=".srt", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            # A finished file takes the name in one rename, so an interrupted
            # run cannot leave a half-written sidecar where a good one was.
            os.replace(staged, destination)
            return destination
        finally:
            if os.path.lexists(staged):
                os.unlink(staged)
