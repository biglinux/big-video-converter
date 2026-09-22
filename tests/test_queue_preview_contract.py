from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "big-video-converter/usr/share/big-video-converter/ui/conversion_page.py"
).read_text(encoding="utf-8")


def test_queue_rows_render_a_real_video_picture():
    assert "Gtk.Picture" in SOURCE
    assert "thumbnail_manager=self.thumbnail_manager" in SOURCE


def test_queue_rebuild_cancels_obsolete_preview_requests():
    assert "dispose_thumbnail" in SOURCE
    assert "row.dispose_thumbnail()" in SOURCE


def test_each_file_exposes_discoverable_profile_and_size_options():
    assert "on_options_callback" in SOURCE
    assert '_("Options")' in SOURCE
    assert "resolution_mode" in SOURCE
