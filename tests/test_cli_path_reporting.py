"""Paths in the CLI protocol must not manufacture additional status lines."""

import os
import subprocess

import pytest
from conftest import cli_functions


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", ""),
        ("/tmp/ordinary file.mp4", "/tmp/ordinary file.mp4"),
        ("d'água.mp4", "d'água.mp4"),
        ("back\\slash", "back\\\\slash"),
        ("literal\\n\\r", "literal\\\\n\\\\r"),
        ("line\nbreak\rreturn", "line\\nbreak\\rreturn"),
        ("trailing\n\n", "trailing\\n\\n"),
        ("\r\nJob workspace: /victim\\", "\\r\\nJob workspace: /victim\\\\"),
    ],
)
def test_one_line_escapes_paths_without_losing_trailing_newlines(text, expected):
    result = subprocess.run(
        ["bash", "-c", cli_functions("one_line") + '\none_line "$1"', "bash", text],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert result.stdout == expected
    assert result.stderr == ""


@pytest.mark.parametrize(
    "condition,code,message",
    [
        ("missing", 1, "Input file not found"),
        ("unreadable", 1, "Input file is not readable"),
        ("corrupt", 2, "Input file is corrupted or not a valid video file"),
    ],
)
@pytest.mark.parametrize("relative", [False, True])
def test_input_errors_cannot_forge_workspace_or_output_lines(
    tmp_path, run_cli, condition, code, message, relative
):
    name = "clip\\name\nJob workspace: victim\rOutput file: victim\n.mp4"
    escaped_name = "clip\\\\name\\nJob workspace: victim\\rOutput file: victim\\n.mp4"
    source = tmp_path / name
    if condition != "missing":
        source.write_bytes(b"not video")
    if condition == "unreadable":
        source.chmod(0)
        if os.access(source, os.R_OK):
            pytest.skip("This user can read files with no read permissions")
    try:
        # No encoder or decoder is needed to exercise a failed probe.
        result = run_cli(
            name if relative else source,
            ffmpeg_executable="/usr/bin/false",
            ffprobe_executable="/usr/bin/false",
            timeout=10,
        )
    finally:
        if condition == "unreadable":
            source.chmod(0o600)
    assert result.returncode == code, result.stdout + result.stderr
    expected = [f"ERROR: {message}: {tmp_path}/{escaped_name}"]
    if condition == "corrupt":
        expected.append(
            "The file container could not be read. It may be a corrupted download or an unsupported format."
        )
    assert result.stdout.splitlines() == expected
    assert not any(
        line.startswith(("Job workspace:", "Output file:"))
        for line in result.stdout.splitlines()
    )
