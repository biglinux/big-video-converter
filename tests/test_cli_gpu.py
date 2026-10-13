"""Converter script: hardware encoders, device selection and the GPU check."""

import os
import shlex
import shutil
import signal
import subprocess
import time

import pytest
from conftest import CLI, fake_ffmpeg, group_members, wait_until


@pytest.mark.parametrize("bitrate", ["", "500000"])
def test_amd_hevc_keeps_its_encoder_options(media, tmp_path, run_cli, bitrate):
    """AMD's constant-QP HEVC command was rebuilt after the bitrate, GOP and
    hvc1 tag had been applied, dropping all three."""
    wrapper = fake_ffmpeg(
        tmp_path,
        "no-gpu",
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 1; done',
    )
    result = run_cli(
        media["video"],
        tmp_path / "out.mp4",
        gpu="amd",
        force_software="",
        gpu_smoke_test="0",
        video_encoder="h265",
        video_bitrate=bitrate,
        keyframe_interval="2",
        ffmpeg_executable=wrapper,
        options="-t 1 -threads 1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    vaapi = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("Running command:") and "hevc_vaapi" in line
    ]
    assert vaapi, result.stdout
    for line in vaapi:
        assert " -g 50 " in line and " -tag:v hvc1 " in line and "ICQ" not in line
        assert (
            (" -rc_mode VBR -b:v 500000 " in line)
            if bitrate
            else (" -rc_mode CQP " in line)
        )


def fake_gpu_selector(directory, answer):
    """A python3 that answers ``answer`` for the GPU selector and runs the rest."""
    wrapper = directory / "python3"
    wrapper.write_text(
        "#!/bin/bash\n"
        f'[[ $1 == */gpu_selector.py ]] && {{ echo "{answer}"; exit 0; }}\n'
        f'exec {shutil.which("python3")} "$@"\n'
    )
    wrapper.chmod(0o755)
    return directory


def hardware_commands(stdout, encoder):
    """The argv of each logged command that encodes with ``encoder`` on a GPU."""
    commands = []
    for line in stdout.splitlines():
        if line.startswith("Running command:") and "-init_hw_device" in line:
            argv = shlex.split(line.removeprefix("Running command:"))
            if encoder in argv:
                commands.append(argv)
    return commands


def device_options(argv):
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == "-init_hw_device"]


@pytest.mark.parametrize(
    "settings",
    [{"gpu": "auto"}, {"gpu": "nvidia", "gpu_device": "/dev/dri/renderD129"}],
)
def test_nvidia_uses_pci_ordered_cuda_index(
    media, tmp_path, run_cli, cli_env, settings
):
    """FFmpeg's cuda device is an index; a /dev/dri path made it fail on any
    machine and fall back to a slower stage."""
    order = tmp_path / "order"
    wrapper = fake_ffmpeg(
        tmp_path,
        "no-gpu",
        f'echo "$CUDA_DEVICE_ORDER" > {order}\n'
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 1; done',
    )
    tools = fake_gpu_selector(tmp_path, "nvidia /dev/dri/renderD129 1")
    result = run_cli(
        media["video"],
        tmp_path / "out.mkv",
        PATH=f"{tools}:{cli_env['PATH']}",
        force_software="",
        gpu_smoke_test="0",
        ffmpeg_executable=wrapper,
        options="-t 0.4 -threads 1",
        **settings,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commands = hardware_commands(result.stdout, "h264_nvenc")
    assert commands, result.stdout
    assert all(device_options(argv) == ["cuda=cu:1"] for argv in commands)
    assert order.read_text().strip() == "PCI_BUS_ID"


def test_qsv_opens_the_chosen_intel_device(media, tmp_path, run_cli):
    wrapper = fake_ffmpeg(
        tmp_path,
        "no-gpu",
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 1; done',
    )
    result = run_cli(
        media["video"],
        tmp_path / "out.mkv",
        gpu="intel",
        gpu_device="/dev/dri/renderD129",
        force_software="",
        gpu_smoke_test="0",
        ffmpeg_executable=wrapper,
        options="-t 0.4 -threads 1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commands = hardware_commands(result.stdout, "h264_qsv")
    assert commands, result.stdout
    assert all(
        device_options(argv)[0].startswith("vaapi=va:/dev/dri/renderD129,driver=iHD")
        for argv in commands
    )


def test_vulkan_opens_chosen_drm_device(media, tmp_path, run_cli):
    wrapper = fake_ffmpeg(
        tmp_path,
        "no-gpu",
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 1; done',
    )
    result = run_cli(
        media["video"],
        tmp_path / "out.mkv",
        gpu="vulkan",
        gpu_device="/dev/dri/renderD129",
        force_software="",
        gpu_smoke_test="0",
        ffmpeg_executable=wrapper,
        options="-t 0.4 -threads 1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    commands = hardware_commands(result.stdout, "h264_vulkan")
    assert commands, result.stdout
    assert all(
        device_options(argv) == ["drm=dr:/dev/dri/renderD129", "vulkan=vk@dr"]
        for argv in commands
    )


@pytest.mark.parametrize("target", ["group", "script"])
def test_cancel_during_gpu_check_ends_job(media, tmp_path, cli_env, target):
    """The GPU check runs in the job's process group and does not delay the
    TERM trap, so a cancel leaves no FFmpeg and no workspace behind."""
    wrapper = fake_ffmpeg(
        tmp_path,
        "hanging-gpu",
        'for arg in "$@"; do [[ $arg == color=c=gray* ]] && exec sleep 60; done',
    )
    env = {
        **cli_env,
        "gpu": "nvidia",
        "force_software": "",
        "ffmpeg_executable": str(wrapper),
        "output_file": str(tmp_path / "out.mkv"),
    }
    log = tmp_path / "log"
    with log.open("wb") as output:
        job = subprocess.Popen(
            ["bash", str(CLI), str(media["video"])],
            env=env,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
    try:
        wait_until(lambda: "Checking GPU encoder" in log.read_text(), 20)
        wait_until(
            lambda: len(group_members(job.pid)) > 2, 10, "the GPU check never started"
        )
        started = time.monotonic()
        if target == "group":
            os.killpg(job.pid, signal.SIGTERM)
        else:
            job.send_signal(signal.SIGTERM)
        assert job.wait(timeout=10) == 143
        assert time.monotonic() - started < 7
        wait_until(lambda: not group_members(job.pid), 5, "part of the job survived")
        assert not list(tmp_path.glob(".bvc-*"))
    finally:
        if job.poll() is None:
            os.killpg(job.pid, signal.SIGKILL)
            job.wait()


def test_undecodable_codec_skips_gpu_decode(media, tmp_path, run_cli):
    """A GPU without the source codec's decoder failed every frame of the
    whole file before falling back; one decoded frame is checked first."""
    wrapper = fake_ffmpeg(
        tmp_path,
        "no-av1-decoder",
        'for arg in "$@"; do [[ $arg == -xerror ]] && exit 218; done\n'
        'for arg in "$@"; do [[ $arg == *color=c=gray* ]] && exit 0; done\n'
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 1; done',
    )
    result = run_cli(
        media["video"],
        tmp_path / "out.mp4",
        gpu="amd",
        force_software="",
        ffmpeg_executable=wrapper,
        options="-t 1 -threads 1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GPU cannot decode this video" in result.stdout
    assert "Encode mode: Decode GPU, encode GPU" not in result.stdout
