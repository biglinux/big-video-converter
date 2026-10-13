"""Converter script: the noise reduction filter and its models."""

import re
import subprocess
from array import array
from pathlib import Path

import pytest
from conftest import media_command
from constants import NOISE_MODELS, noise_reduction_filter
from engine.media_validation import probe_media, stream_count


def test_noise_reduction_never_blocks_the_conversion(media, tmp_path, run_cli):
    """The noise model is optional. Installed, it filters the audio; missing,
    it warns. Either way the conversion the user asked for still happens."""
    out = tmp_path / "denoised.mkv"
    result = run_cli(
        media["video"],
        out,
        noise_reduction="1",
        noise_strength="0.5",
        audio_handling="reencode",
        options="-t 0.5 -threads 1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if not Path(NOISE_MODELS[0][0]).exists():
        assert "WARNING: Noise reduction requested" in result.stdout
    assert stream_count(probe_media(str(out)), "audio") == 1


def _installed_noise_models():
    return [
        pytest.param(
            model,
            marks=pytest.mark.skipif(
                not Path(entry[0]).exists(), reason=f"{entry[4]} not installed"
            ),
        )
        for model, entry in enumerate(NOISE_MODELS)
    ]


@pytest.mark.parametrize("model", _installed_noise_models())
def test_noise_reduction_keeps_surround_channels(media, tmp_path, run_cli, model):
    """The plugins are mono; FFmpeg runs one instance per channel."""
    source = tmp_path / "surround.mkv"
    media_command(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=128x72:rate=25",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-t",
        "1",
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-c:a",
        "aac",
        "-ac",
        "6",
        "-y",
        source,
    )
    out = tmp_path / "denoised.mkv"
    result = run_cli(
        source, out, noise_reduction="1", noise_model=model, audio_handling="reencode"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Noise reduction enabled" in result.stdout
    audio = [s for s in probe_media(str(out))["streams"] if s["codec_type"] == "audio"]
    assert [s["channels"] for s in audio] == [6]


@pytest.mark.parametrize("model", _installed_noise_models())
def test_preview_denoises_like_the_export(media, tmp_path, run_cli, model):
    """The editor's mpv preview builds the chain in Python; the export script
    builds it in shell. What is heard must be what is exported."""
    result = run_cli(
        media["video"],
        tmp_path / "out.mkv",
        noise_reduction="1",
        noise_model=model,
        noise_strength="0.7",
        audio_handling="reencode",
        options="-t 0.2 -threads 1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert noise_reduction_filter(model, 0.7) in result.stdout


def test_voice_only_preview_matches_export(media, tmp_path, run_cli):
    if not Path(NOISE_MODELS[0][0]).exists():
        pytest.skip(f"{NOISE_MODELS[0][4]} not installed")
    result = run_cli(
        media["video"],
        tmp_path / "out.mkv",
        noise_reduction="1",
        noise_strength="0.7",
        voice_only="1",
        voice_only_sensitivity="4",
        audio_handling="reencode",
        options="-t 0.2 -threads 1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert noise_reduction_filter(0, 0.7, 4) in result.stdout


@pytest.mark.parametrize(
    "model,gate", [(0, "|c17=60|c18=1|c19=2"), (1, "|c1=60|c2=1|c3=2")]
)
def test_voice_only_gates_either_plugin(model, gate):
    chain = noise_reduction_filter(model, 1.0, 2)
    assert gate in chain
    # The gate holds either plugin to the same delay, cut away again.
    assert "apad=pad_len=3168," in chain and "atrim=start_sample=27168," in chain


def test_noise_strength_rises_linearly_to_cap():
    """Past 24 dB DFN3 removes no more noise; a squared 0-100 dB curve left
    the top half of the slider doing nothing."""
    assert "controls=c0=12.00|c6=0|c17=0," in noise_reduction_filter(0, 0.5)
    assert "controls=c0=24.00|c6=0|c17=0," in noise_reduction_filter(0, 1.0)
    assert "controls=c0=24.00," in noise_reduction_filter(1, 0.5)
    assert "controls=c0=48.00," in noise_reduction_filter(1, 3.0)


def _pcm(path, *options):
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            *options,
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return array("f", raw.stdout)


@pytest.mark.parametrize("model", _installed_noise_models())
@pytest.mark.parametrize("seconds", [0.3, 2])
def test_noise_reduction_keeps_timing_and_samples(tmp_path, model, seconds):
    """At strength 0 the plugin passes its input through, only delayed: the
    chain must give back every sample in place, including a clip shorter than
    its half-second lead-in, and keep a stream's starting timestamp."""
    source = tmp_path / "tone.wav"
    media_command(
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=330:sample_rate=48000",
        "-af",
        "volume=0.5",
        "-t",
        str(seconds),
        "-c:a",
        "pcm_f32le",
        "-y",
        source,
    )
    chain = noise_reduction_filter(model, 0.0)
    original = _pcm(source)
    filtered = _pcm(source, "-af", chain)
    assert len(filtered) == len(original)
    assert max(abs(a - b) for a, b in zip(original, filtered)) < 1e-4

    shown = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-i",
            str(source),
            "-af",
            f"asetpts=PTS+2.5/TB,{chain},ashowinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    start = float(re.search(r"n:0 pts:\S+ pts_time:(\S+)", shown).group(1))
    assert abs(start - 2.5) < 1e-3
