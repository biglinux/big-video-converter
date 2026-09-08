"""Packet timestamp clipping for embedded subtitles, preserving their payload.

A/V is trimmed first. Seeking the original subtitle input loses cues crossing
its start; putting unclipped cues in the A/V pass can extend the output instead.
The second mux keeps intersecting packets and rebases/clamps their timestamps.
Only parsed finite numbers, never user expressions, enter the bitstream filter.
"""

from decimal import Decimal, InvalidOperation
import re
import sys


def seconds(text: str) -> Decimal:
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?(?:ms|us|s)?", text):
        raise ValueError("Invalid nonnegative trim time")
    factor = Decimal(1)
    if text.endswith("ms"):
        text, factor = text[:-2], Decimal(".001")
    elif text.endswith("us"):
        text, factor = text[:-2], Decimal(".000001")
    elif text.endswith("s"):
        text = text[:-1]
    parts = text.split(":")
    value = Decimal(0)
    try:
        for index, part in enumerate(parts):
            number = Decimal(part)
            if index and number >= 60:
                raise ValueError("Invalid timecode")
            value = value * 60 + number
        value *= factor
    except InvalidOperation as error:
        raise ValueError("Invalid trim time") from error
    if not value.is_finite() or value > 315576000:
        raise ValueError("Trim time is outside the supported range")
    return value


def clipping_options(argv: list[str]) -> tuple[str, str]:
    """Return original-input end limit and safe packet filter (empty if uncut)."""
    times = {flag: seconds(argv[i + 1]) for i, flag in enumerate(argv)
             if flag in ("-ss", "-t", "-to")}
    if not times:
        return "", ""
    if any(flag in argv for flag in ("-sseof", "-itsoffset", "-copyts", "-start_at_zero")):
        raise ValueError("Subtitle trimming cannot be combined with timestamp overrides")
    start = times.get("-ss", Decimal(0))
    end = start + times["-t"] if "-t" in times else times.get("-to")
    if end is not None and end <= start:
        raise ValueError("Trim interval must have positive duration")
    s = format(start, "f")
    drop = f"lte((pts+duration)*tb,{s})"
    right = "PTS+DURATION"
    if end is not None:
        e = format(end, "f")
        drop += f"+gte(pts*tb,{e})"
        right = f"min(PTS+DURATION,{e}/TB)"
    filters = (f"noise=amount=0:drop='{drop}',"
               f"setts=pts='max(PTS-{s}/TB,0)':dts='max(DTS-{s}/TB,0)':"
               f"duration='max(0,{right}-max(PTS,{s}/TB))'")
    return format(end, "f") if end is not None else "", filters


if __name__ == "__main__":
    from ffmpeg_options import parse_additional_options
    try:
        result = clipping_options(parse_additional_options(sys.argv[1]))
    except (IndexError, ValueError) as error:
        sys.exit(str(error))
    for item in result:
        sys.stdout.buffer.write(item.encode("utf-8") + b"\0")
