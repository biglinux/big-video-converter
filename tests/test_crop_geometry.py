from utils.crop_geometry import centered_crop


def output_size(width, height, margins):
    left, right, top, bottom = margins
    return width - left - right, height - top - bottom


def test_square_crop_is_centered_and_even():
    margins = centered_crop(1920, 1080, "1:1")
    width, height = output_size(1920, 1080, margins)
    assert width == height
    assert width % 2 == 0 and height % 2 == 0
    assert abs(margins[0] - margins[1]) <= 1


def test_vertical_ratio_is_interpreted_in_rotated_output_orientation():
    margins = centered_crop(1920, 1080, "9:16", rotation=90)
    width, height = output_size(1920, 1080, margins)
    assert abs(width / height - 16 / 9) < 0.01


def test_original_and_free_keep_the_whole_frame():
    assert centered_crop(1920, 1080, "original") == (0, 0, 0, 0)
    assert centered_crop(1920, 1080, "free") == (0, 0, 0, 0)


def test_all_social_ratios_fit_source_bounds():
    for ratio in ("16:9", "9:16", "1:1", "4:5", "3:2", "2.39:1"):
        margins = centered_crop(3840, 2160, ratio)
        width, height = output_size(3840, 2160, margins)
        assert 2 <= width <= 3840
        assert 2 <= height <= 2160
        assert width % 2 == 0 and height % 2 == 0


# The conversion crops the source, then turns it clockwise (transpose=1 per
# quarter turn) and mirrors it (hflip, vflip); the preview shows the picture
# turned and mirrored and the crop editor works on what it shows.
def _picture(width, height):
    return [[(x, y) for x in range(width)] for y in range(height)]


def _crop(picture, margins):
    left, right, top, bottom = margins
    return [row[left:len(row) - right] for row in picture[top:len(picture) - bottom]]


def _transform(picture, rotation, flip_h, flip_v):
    for _ in range(rotation // 90):
        picture = [list(row) for row in zip(*picture[::-1], strict=True)]
    if flip_h:
        picture = [row[::-1] for row in picture]
    if flip_v:
        picture = picture[::-1]
    return picture


TRANSFORMS = [(rotation, flip_h, flip_v) for rotation in (0, 90, 180, 270)
              for flip_h in (False, True) for flip_v in (False, True)]


def test_displayed_crop_is_the_crop_the_conversion_makes():
    from ui.crop_overlay import displayed_size, source_to_display

    source = _picture(7, 4)
    margins = (1, 2, 0, 3)  # all different, so a swapped edge shows
    for transform in TRANSFORMS:
        shown = _transform(source, *transform)
        assert (len(shown[0]), len(shown)) == displayed_size(7, 4, transform[0])
        converted = _transform(_crop(source, margins), *transform)
        assert _crop(shown, source_to_display(margins, *transform)) == converted, transform


def test_display_margins_map_back_to_the_same_source_margins():
    from ui.crop_overlay import display_to_source, source_to_display

    for transform in TRANSFORMS:
        assert display_to_source(source_to_display((1, 2, 3, 4), *transform), *transform) == (1, 2, 3, 4)


def test_dragging_the_visible_edge_crops_the_matching_source_edge():
    import gi

    gi.require_version("Gtk", "4.0")
    from ui.crop_overlay import _RIGHT, CropOverlay

    changes = []
    overlay = CropOverlay()
    overlay.set_video_dimensions(640, 360)
    overlay.set_transform(90, False, False)
    overlay.set_on_crop_changed(lambda *margins: changes.append(margins))
    # Shown 360 wide and 640 high, drawn at half size.
    overlay._get_video_rect = lambda: (0, 0, 180, 320)
    overlay._drag_target = _RIGHT
    overlay._on_drag_update(None, -10, 0)
    # The visible right edge of a picture turned clockwise is the source top.
    assert changes == [(0, 0, 20, 0)]
    assert (overlay._crop_left, overlay._crop_right) == (0, 20)


def test_crop_that_cannot_be_applied_is_an_error_not_an_uncropped_video():
    import pytest
    from utils.video_settings import CropError, get_ffmpeg_filter_string

    class Settings:
        def __init__(self, **values):
            self.values = values

        def get_value(self, key, default=None):
            return self.values.get(key, default)

        load_setting = get_value

        def get_boolean(self, key, default=False):
            return bool(self.values.get(key, default))

    crop = Settings(**{"preview-crop-left": 60, "preview-crop-right": 40})
    assert get_ffmpeg_filter_string(crop, 128, 72) == "crop=28:72:60:0"
    with pytest.raises(CropError):
        get_ffmpeg_filter_string(crop, 100, 72)
    with pytest.raises(CropError):
        get_ffmpeg_filter_string(crop)
    assert get_ffmpeg_filter_string(Settings()) == ""
