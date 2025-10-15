"""
Unified video settings management module.
Provides constants, utilities, and management for video adjustments.
"""

# Default values for all video adjustments
DEFAULT_VALUES = {
    "brightness": 0.0,  # GStreamer: -1.0 to 1.0, FFmpeg: -1.0 to 1.0 (direct map)
    "saturation": 1.0,  # GStreamer: 0.0 to 2.0, FFmpeg: 0.0 to 16.0 (needs conversion)
    "hue": 0.0,  # GStreamer: -1.0 to 1.0, FFmpeg: -3.14 to 3.14 radians (needs conversion)
    "crop_left": 0,  # Pixels to crop from left
    "crop_right": 0,  # Pixels to crop from right
    "crop_top": 0,  # Pixels to crop from top
    "crop_bottom": 0,  # Pixels to crop from bottom
    "trim_start": 0.0,  # Start time for trimming (seconds)
    "trim_end": -1.0,  # End time for trimming (seconds, -1 means no trim)
}

# Settings key mapping
SETTING_KEYS = {
    "brightness": "preview-brightness",
    "saturation": "preview-saturation",
    "hue": "preview-hue",
    "crop_left": "preview-crop-left",
    "crop_right": "preview-crop-right",
    "crop_top": "preview-crop-top",
    "crop_bottom": "preview-crop-bottom",
    "trim_start": "video-trim-start",
    "trim_end": "video-trim-end",
}

# Threshold for determining if a value needs to be included
FLOAT_THRESHOLD = 0.01


#
# Value Access Functions
#
def get_adjustment_value(settings, name):
    """Get adjustment value from settings"""
    setting_key = SETTING_KEYS.get(name)
    if not setting_key:
        return DEFAULT_VALUES.get(name, 0)

    if name in ["crop_left", "crop_right", "crop_top", "crop_bottom"]:
        return settings.get_value(setting_key, DEFAULT_VALUES.get(name, 0))
    elif name == "trim_end":
        # Special handling for trim_end to ensure we get None when it's -1
        value = settings.get_value(setting_key, DEFAULT_VALUES.get(name, -1.0))
        return None if value < 0 else value
    else:
        return settings.get_value(setting_key, DEFAULT_VALUES.get(name, 0.0))


def save_adjustment_value(settings, name, value):
    """Save an adjustment value to settings"""
    setting_key = SETTING_KEYS.get(name)
    if not setting_key:
        return False

    if name in ["crop_left", "crop_right", "crop_top", "crop_bottom"]:
        return settings.set_int(setting_key, value)
    else:
        return settings.set_double(setting_key, value)


#
# Value Conversion Functions (GStreamer to FFmpeg)
#
def gstreamer_brightness_to_ffmpeg(gst_brightness):
    """
    GStreamer videobalance brightness: -1.0 to 1.0 (0.0 is neutral)
    FFmpeg eq brightness: -1.0 to 1.0 (0.0 is neutral)
    Direct 1:1 mapping.
    """
    return gst_brightness


def gstreamer_saturation_to_ffmpeg(gst_saturation):
    """
    GStreamer videobalance saturation: 0.0 to 2.0 (1.0 is neutral)
    FFmpeg eq saturation: 0.0 to 16.0 (1.0 is neutral)

    Mapping strategy:
    - Below neutral: GStreamer [0.0, 1.0] → FFmpeg [0.0, 1.0] (direct map)
    - Above neutral: GStreamer [1.0, 2.0] → FFmpeg [1.0, 3.0] (direct map fake)
    """
    if gst_saturation >= 1.0:
        # Map GStreamer's [1.0, 2.0] to FFmpeg's [1.0, 3.0]
        return 1.0 + (gst_saturation - 1.0) * 1.5
    else:
        # Direct 1:1 map for values below neutral
        return gst_saturation


def gstreamer_hue_to_ffmpeg(gst_hue):
    """
    GStreamer videobalance hue: -1.0 to 1.0 (0.0 is neutral)
    FFmpeg hue filter: -3.14 to 3.14 radians / -π to π (0.0 is neutral)

    GStreamer videobalance DOES support hue in the range [-1.0, 1.0].
    We need to convert this normalized range to FFmpeg's radian range.

    Mapping: GStreamer [-1.0, 1.0] → FFmpeg [-π, π] radians
    Formula: ffmpeg_hue = gstreamer_hue * π

    This ensures:
    - GStreamer hue = -1.0 → FFmpeg hue = -π (-180°)
    - GStreamer hue =  0.0 → FFmpeg hue =  0  (0°)
    - GStreamer hue = +1.0 → FFmpeg hue = +π (+180°)
    """
    # Map from GStreamer normalized [-1.0, 1.0] to FFmpeg radians [-π, π]
    import math

    return gst_hue * math.pi


#
# FFmpeg Filter Generation
#
def generate_video_filters(
    settings, video_width=None, video_height=None, input_file=None
):
    """
    Generate all needed FFmpeg filters in one go.
    """
    filters = []

    # ... (O resto da função, como a detecção de HEVC 10-bit, permanece o mesmo) ...
    is_hevc_10bit_to_h264 = False
    if input_file:
        try:
            import subprocess

            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=pix_fmt,codec_name",
                    "-of",
                    "csv=p=0",
                    input_file,
                ],
                capture_output=True,
                text=True,
            )

            output = result.stdout.strip().split(",")
            if len(output) >= 2:
                pix_fmt = output[0]
                codec = output[1]

                is_10bit = "p10" in pix_fmt or "10le" in pix_fmt
                is_hevc = codec in ["hevc", "h265"]
                is_h264_output = settings.get_value("video-codec", "h264") == "h264"

                if is_10bit and is_hevc and is_h264_output:
                    is_hevc_10bit_to_h264 = True
        except:
            pass

    if is_hevc_10bit_to_h264:
        print(
            "Skipping custom video filters for optimized H.265 10-bit to H.264 GPU conversion"
        )
        return []

    # 1. Add crop filter
    crop_left = get_adjustment_value(settings, "crop_left")
    crop_right = get_adjustment_value(settings, "crop_right")
    crop_top = get_adjustment_value(settings, "crop_top")
    crop_bottom = get_adjustment_value(settings, "crop_bottom")

    if (
        (crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0)
        and video_width is not None
        and video_height is not None
    ):
        crop_width = video_width - crop_left - crop_right
        crop_height = video_height - crop_top - crop_bottom

        if crop_width > 0 and crop_height > 0:
            filters.append(f"crop={crop_width}:{crop_height}:{crop_left}:{crop_top}")

    # 2. Add eq filter with calibrated values (brightness, saturation)
    eq_parts = []

    brightness = get_adjustment_value(settings, "brightness")
    if abs(brightness) > FLOAT_THRESHOLD:
        ffmpeg_brightness = gstreamer_brightness_to_ffmpeg(brightness)
        eq_parts.append(f"brightness={ffmpeg_brightness:.3f}")

    saturation = get_adjustment_value(settings, "saturation")
    if abs(saturation - 1.0) > FLOAT_THRESHOLD:
        ffmpeg_saturation = gstreamer_saturation_to_ffmpeg(saturation)
        eq_parts.append(f"saturation={ffmpeg_saturation:.3f}")

    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    # 3. Add hue filter separately (FFmpeg requires separate hue filter, not in eq)
    hue = get_adjustment_value(settings, "hue")
    if abs(hue) > FLOAT_THRESHOLD:
        ffmpeg_hue = gstreamer_hue_to_ffmpeg(hue) * 180 / 3.14159
        filters.append(f"hue=h={ffmpeg_hue:.3f}")

    return filters


def get_ffmpeg_filter_string(
    settings, video_width=None, video_height=None, input_file=None
):
    """Get the complete FFmpeg filter string for command-line use"""
    filters = generate_video_filters(settings, video_width, video_height, input_file)
    if not filters:
        return ""
    return ",".join(filters)


# Legacy functions kept for compatibility
generate_all_filters = generate_video_filters
get_video_filter_string = get_ffmpeg_filter_string


#
# Video Adjustment Manager
#
class VideoAdjustmentManager:
    """
    Manages video adjustment settings with UI updates.
    """

    def __init__(self, settings_manager, page=None):
        self.settings = settings_manager
        self.page = page
        self.values = {name: self.get_value(name) for name in DEFAULT_VALUES}

    def get_value(self, name):
        return get_adjustment_value(self.settings, name)

    def set_value(self, name, value, update_ui=True):
        setting_key = SETTING_KEYS.get(name)
        if not setting_key:
            return False
        self.values[name] = value
        success = save_adjustment_value(self.settings, name, value)
        if update_ui and success and self.page:
            self._update_ui_for_setting(name, value)
        return success

    def _update_ui_for_setting(self, name, value):
        if not self.page or not hasattr(self.page, "ui"):
            return
        ui = self.page.ui
        ui_controls = {
            "brightness": getattr(ui, "brightness_scale", None),
            "saturation": getattr(ui, "saturation_scale", None),
            "crop_left": getattr(ui, "crop_left_spin", None),
            "crop_right": getattr(ui, "crop_right_spin", None),
            "crop_top": getattr(ui, "crop_top_spin", None),
            "crop_bottom": getattr(ui, "crop_bottom_spin", None),
        }
        control = ui_controls.get(name)
        if control:
            control.set_value(value)