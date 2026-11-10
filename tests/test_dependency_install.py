"""The install command the missing-dependency dialog offers, per distro."""

import pytest
from engine import dependency_checker as dc


def checker(base):
    obj = dc.DependencyChecker.__new__(dc.DependencyChecker)
    obj.distro = {"id": None, "base": base}
    return obj


@pytest.mark.parametrize(
    ("base", "packages"),
    [("arch", ["ffmpeg", "mpv"]), ("debian", ["ffmpeg", "libmpv2"]),
     ("rpm", ["ffmpeg", "mpv-libs"])],
)
def test_appimage_asks_only_for_ffmpeg_and_libmpv(monkeypatch, base, packages):
    # python-mpv and PyOpenGL travel inside the AppImage's own Python.
    monkeypatch.setenv("APPIMAGE", "/tmp/x.AppImage")
    info = checker(base).get_install_command()
    assert info["packages"] == packages
    assert info["command"][-len(packages):] == packages


@pytest.mark.parametrize(
    ("base", "module"),
    [("arch", "python-mpv"), ("debian", "python3-mpv"), ("rpm", "python3-mpv")],
)
def test_native_install_still_brings_python_mpv(monkeypatch, base, module):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)
    assert module in checker(base).get_install_command()["packages"]


def test_unknown_distro_has_no_command():
    assert checker(None).get_install_command() is None
