"""The legacy NVIDIA driver notice: when it is offered."""

import subprocess

import pytest
from engine import legacy_nvidia


@pytest.mark.parametrize(
    ("version", "legacy"),
    [
        ("580.178.04", True),
        ("470.256.02", True),
        ("610.12", False),
        ("615.4", False),
        ("", False),
    ],
)
def test_legacy_branch_by_version(version, legacy):
    assert legacy_nvidia.is_legacy(version) == legacy


def test_offered_once_per_driver():
    assert legacy_nvidia.should_offer("580.178.04", "", installed=False)
    assert not legacy_nvidia.should_offer("580.178.04", "580.178.04", installed=False)
    assert legacy_nvidia.should_offer("580.180.01", "580.178.04", installed=False)
    assert not legacy_nvidia.should_offer("580.178.04", "", installed=True)
    assert not legacy_nvidia.should_offer("615.4", "", installed=False)


def test_driver_version_without_module():
    def run(argv, **_):
        return subprocess.CompletedProcess(
            argv, 1, "", "modinfo: ERROR: Module nvidia not found."
        )

    assert legacy_nvidia.driver_version(run) == ""


def test_driver_version_read_from_modinfo():
    def run(argv, **_):
        assert argv == ["modinfo", "-F", "version", "nvidia"]
        return subprocess.CompletedProcess(argv, 0, "580.178.04\n", "")

    assert legacy_nvidia.driver_version(run) == "580.178.04"
