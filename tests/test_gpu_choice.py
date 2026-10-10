"""The automatic GPU choice shared by the GUI and the conversion script."""

import subprocess

import pytest
from engine import gpu_selector as gs


def gpu(vendor, node):
    return {
        "type": vendor,
        "device": f"/dev/dri/{node}",
        "slot": f"0000:0{node[-1]}:00.0",
        "name": f"{vendor} {node}",
        "label": vendor,
    }


NVIDIA, AMD, INTEL = (
    gpu("nvidia", "renderD128"),
    gpu("amd", "renderD129"),
    gpu("intel", "renderD130"),
)


def table(answers):
    """A supports() that answers from {node: {codec: True/False/None}}."""
    return lambda g, _gpus, codec: answers[g["device"][-10:]].get(codec)


def test_the_only_card_that_encodes_av1_wins_over_the_vendor_order():
    answers = {
        "renderD128": {"h264": True, "av1": False, "vp9": False},
        "renderD130": {"h264": True, "av1": True, "vp9": True},
    }
    assert gs.choose_gpu([NVIDIA, INTEL], "h264", table(answers)) is INTEL


def test_with_equal_av1_the_only_card_that_encodes_vp9_wins():
    answers = {
        "renderD128": {"h264": True, "av1": True, "vp9": False},
        "renderD130": {"h264": True, "av1": True, "vp9": True},
    }
    assert gs.choose_gpu([NVIDIA, INTEL], "h264", table(answers)) is INTEL


def test_equal_support_follows_nvidia_amd_intel():
    same = {"h264": True, "av1": True, "vp9": True}
    answers = {"renderD128": same, "renderD129": same, "renderD130": same}
    assert gs.choose_gpu([INTEL, AMD, NVIDIA], "h264", table(answers)) is NVIDIA
    assert gs.choose_gpu([INTEL, AMD], "h264", table(answers)) is AMD


def test_an_unknown_answer_does_not_count_as_the_other_card_lacking_it():
    answers = {
        "renderD129": {"h264": True, "av1": None, "vp9": True},
        "renderD130": {"h264": True, "av1": True, "vp9": True},
    }
    # Intel alone is known to encode AV1, but AMD might too: vendor order.
    assert gs.choose_gpu([INTEL, AMD], "h264", table(answers)) is AMD


def test_a_card_that_cannot_encode_the_codec_is_never_chosen():
    answers = {
        "renderD128": {"vp9": False, "av1": True},
        "renderD130": {"vp9": True, "av1": False},
    }
    assert gs.choose_gpu([NVIDIA, INTEL], "vp9", table(answers)) is INTEL
    assert gs.choose_gpu([NVIDIA, INTEL], "h265", table(answers)) is None


VAINFO = """vainfo: Driver version: Mesa Gallium driver
      VAProfileH264Main               :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointEncSlice
      VAProfileHEVCMain               :	VAEntrypointEncSlice
      VAProfileVP9Profile0            :	VAEntrypointVLD
      VAProfileAV1Profile0            :	VAEntrypointEncSliceLP
"""


@pytest.fixture
def probes(monkeypatch):
    """Fake ffmpeg -encoders, vainfo and NVENC test frames by argv."""
    replies = {}

    def run(argv, **kwargs):
        if argv[0] == "vainfo":
            code, out = replies.get("vainfo", (0, VAINFO))
            return subprocess.CompletedProcess(argv, code, out, "")
        if "-encoders" in argv:
            names = replies.get(
                "encoders",
                [
                    "h264_vaapi",
                    "hevc_vaapi",
                    "av1_vaapi",
                    "vp9_vaapi",
                    "h264_nvenc",
                    "av1_nvenc",
                ],
            )
            out = "".join(f" V....D {name} x\n" for name in names)
            return subprocess.CompletedProcess(argv, 0, out, "")
        code, err = replies.get("nvenc", (0, ""))
        assert kwargs["env"]["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
        return subprocess.CompletedProcess(argv, code, "", err)

    monkeypatch.setattr(gs.subprocess, "run", run)
    for cached in (
        gs._available_ffmpeg_encoders,
        gs._vainfo_profiles,
        gs._nvenc_encodes,
    ):
        cached.cache_clear()
    yield replies
    for cached in (
        gs._available_ffmpeg_encoders,
        gs._vainfo_profiles,
        gs._nvenc_encodes,
    ):
        cached.cache_clear()


def test_vaapi_support_comes_from_the_device_encode_entrypoints(probes):
    gpus = [AMD]
    assert [gs.supports(AMD, gpus, c) for c in ("h264", "h265", "av1", "vp9")] == [
        True,
        True,
        True,
        False,  # VP9 only decodes on this device
    ]


def test_a_failed_vainfo_leaves_vaapi_support_unknown(probes):
    probes["vainfo"] = (1, "")
    assert gs.supports(AMD, [AMD], "av1") is None


def test_nvenc_av1_is_asked_of_the_card_and_vp9_does_not_exist(probes):
    gpus = [NVIDIA, INTEL]
    assert gs.supports(NVIDIA, gpus, "vp9") is False
    probes["nvenc"] = (1, "[av1_nvenc] Codec not supported")
    assert gs.supports(NVIDIA, gpus, "av1") is False
    gs._nvenc_encodes.cache_clear()
    probes["nvenc"] = (1, "Cannot load libcuda.so.1")
    assert gs.supports(NVIDIA, gpus, "av1") is None
    gs._nvenc_encodes.cache_clear()
    probes["nvenc"] = (0, "")
    assert gs.supports(NVIDIA, gpus, "av1") is True


def test_an_encoder_missing_from_the_ffmpeg_build_is_unsupported(probes):
    probes["encoders"] = ["h264_vaapi"]
    assert gs.supports(AMD, [AMD], "h265") is False


def test_cuda_index_counts_nvidia_cards_in_pci_order():
    second = {**gpu("nvidia", "renderD131"), "slot": "0000:01:00.0"}
    first = {**gpu("nvidia", "renderD128"), "slot": "0000:00:02.0"}
    cards = [second, INTEL, first]
    assert gs.cuda_index(first, cards) == 0
    assert gs.cuda_index(second, cards) == 1
