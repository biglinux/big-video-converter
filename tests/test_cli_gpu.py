"""Converter script: hardware encoders, device selection and the GPU check."""

import os
import shlex
import shutil
import signal
import subprocess
import time

import pytest
from conftest import CLI, cli_functions, fake_ffmpeg, group_members, wait_until


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


def test_failed_qsv_gives_way_to_vaapi(media, tmp_path, run_cli, cli_env):
    """QSV needs a runtime for the GPU's generation (VPL starts at Tiger Lake):
    an Intel whose QSV fails its check encodes through VA-API, not software."""
    wrapper = fake_ffmpeg(
        tmp_path,
        "no-qsv",
        'for arg in "$@"; do [[ $arg == h264_qsv ]] && exit 1; done\n'
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 0; done',
    )
    tools = fake_gpu_selector(tmp_path, "intel /dev/dri/renderD128")
    result = run_cli(
        media["video"],
        tmp_path / "out.mkv",
        PATH=f"{tools}:{cli_env['PATH']}",
        force_software="",
        gpu="",
        ffmpeg_executable=wrapper,
        options="-t 0.4 -threads 1",
    )
    assert "QSV did not encode a test frame: using VA-API" in result.stdout
    assert hardware_commands(result.stdout, "h264_vaapi"), result.stdout
    assert not hardware_commands(result.stdout, "h264_qsv"), result.stdout


def fake_dgpu(directory):
    """A big-dgpu that reports the dedicated GPU off and records a wake."""
    tool = directory / "big-dgpu"
    tool.write_text(
        "#!/bin/bash\n"
        f"[[ $1 == wake ]] && {{ touch {directory}/woken; exit 0; }}\n"
        f"[[ -e {directory}/woken ]] && echo on || echo off\n"
    )
    tool.chmod(0o755)
    return directory


def test_named_dedicated_gpu_is_woken(media, tmp_path, run_cli, cli_env):
    tools = fake_dgpu(fake_gpu_selector(tmp_path, ""))
    run_cli(
        media["video"],
        tmp_path / "out.mkv",
        PATH=f"{tools}:{cli_env['PATH']}",
        gpu="nvidia",
        force_software="",
        gpu_smoke_test="0",
        options="-t 0.4 -threads 1",
    )
    assert (tmp_path / "woken").exists()


def test_software_job_leaves_dedicated_gpu(media, tmp_path, run_cli, cli_env):
    tools = fake_dgpu(fake_gpu_selector(tmp_path, ""))
    result = run_cli(
        media["video"],
        tmp_path / "out.mkv",
        PATH=f"{tools}:{cli_env['PATH']}",
        options="-t 0.4 -threads 1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "woken").exists()


@pytest.mark.parametrize(
    ("supplies", "mains"),
    [
        ({"AC": ("Mains", "1"), "BAT0": ("Battery", "1")}, True),
        ({"AC": ("Mains", "0"), "BAT0": ("Battery", "1")}, False),
        ({"BAT0": ("Battery", "1")}, False),
        ({}, True),
    ],
)
def test_mains_power_read_from_supplies(tmp_path, supplies, mains):
    """A desktop has no battery and counts as on mains."""
    for name, (kind, online) in supplies.items():
        supply = tmp_path / name
        supply.mkdir()
        (supply / "type").write_text(kind + "\n")
        (supply / "online").write_text(online + "\n")
    script = cli_functions("on_mains_power") + f"\non_mains_power {tmp_path}"
    result = subprocess.run(["bash", "-c", script], check=False, timeout=10)
    assert (result.returncode == 0) == mains


def test_refused_nvenc_is_remembered(media, tmp_path, run_cli, cli_env):
    """An NVENC too new for the card's driver (FFmpeg 9 needs driver 610) left
    the job to software encoding; another GPU encodes it, and the dedicated
    GPU is not woken for it again."""
    selector = tmp_path / "python3"
    selector.write_text(
        "#!/bin/bash\n"
        "if [[ $1 == */gpu_selector.py ]]; then\n"
        '  [[ " $* " == *" --without nvidia "* ]] && echo "intel /dev/dri/renderD128" '
        '|| echo "nvidia /dev/dri/renderD129 0"\n'
        "  exit 0\n"
        "fi\n"
        f'exec {shutil.which("python3")} "$@"\n'
    )
    selector.chmod(0o755)
    fake_dgpu(tmp_path)
    wrapper = fake_ffmpeg(
        tmp_path,
        "old-nvenc",
        'for arg in "$@"; do [[ $arg == h264_nvenc ]] && {\n'
        '  echo "Driver does not support the required nvenc API version." >&2; exit 1; }\n'
        "done\n"
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 1; done',
    )
    settings = {
        "PATH": f"{tmp_path}:{cli_env['PATH']}",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "gpu": "",
        "force_software": "",
        "gpu_smoke_test": "0",
        "ffmpeg_executable": wrapper,
        "options": "-t 0.4 -threads 1",
    }
    result = run_cli(media["video"], tmp_path / "first.mkv", **settings)
    assert "NVENC did not encode a test frame" in result.stdout, result.stdout
    assert hardware_commands(result.stdout, "h264_qsv"), result.stdout
    assert (tmp_path / "cache/big-video-converter/nvenc-failed").exists()

    (tmp_path / "woken").unlink(missing_ok=True)
    run_cli(media["video"], tmp_path / "second.mkv", **settings)
    assert not (tmp_path / "woken").exists()


def test_unready_cuda_is_not_remembered(media, tmp_path, run_cli, cli_env):
    """CUDA can answer "no device" for a moment after the GPU is woken: that is
    tried again, never remembered as NVENC refusing."""
    tools = fake_gpu_selector(tmp_path, "nvidia /dev/dri/renderD129 0")
    wrapper = fake_ffmpeg(
        tmp_path,
        "cuda-not-ready",
        'for arg in "$@"; do [[ $arg == h264_nvenc ]] && {\n'
        '  echo "cuInit(0) failed -> CUDA_ERROR_NO_DEVICE" >&2; exit 1; }\n'
        "done\n"
        'for arg in "$@"; do [[ $arg == -init_hw_device ]] && exit 1; done',
    )
    run_cli(
        media["video"],
        tmp_path / "out.mkv",
        PATH=f"{tools}:{cli_env['PATH']}",
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        gpu="",
        force_software="",
        gpu_smoke_test="0",
        ffmpeg_executable=wrapper,
        options="-t 0.4 -threads 1",
    )
    assert not (tmp_path / "cache/big-video-converter/nvenc-failed").exists()
