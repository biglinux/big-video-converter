"""
Unified video settings management module.
Provides constants, utilities, and management for video adjustments.
"""

# Default values for all video adjustments
DEFAULT_VALUES = {
    "brightness": 0.0,  # Range: -1.0 to 1.0 (FFmpeg range)
    "contrast": 1.0,  # Range: 0.0 to 2.0 (FFmpeg range)
    "saturation": 1.0,  # Range: 0.0 to 3.0 (FFmpeg range)
    "gamma": 1.0,  # Range: 0.1 to 10.0 (FFmpeg range)
    "gamma_r": 1.0,  # Range: 0.1 to 10.0 (FFmpeg range)
    "gamma_g": 1.0,  # Range: 0.1 to 10.0 (FFmpeg range)
    "gamma_b": 1.0,  # Range: 0.1 to 10.0 (FFmpeg range)
    "gamma_weight": 1.0,  # Range: 0.0 to 1.0 (FFmpeg range)
    "hue": 0.0,  # Range: -3.14 to 3.14 radians (converted to degrees for FFmpeg)
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
    "contrast": "preview-contrast",
    "saturation": "preview-saturation",
    "gamma": "preview-gamma",
    "gamma_r": "preview-gamma-r",
    "gamma_g": "preview-gamma-g",
    "gamma_b": "preview-gamma-b",
    "gamma_weight": "preview-gamma-weight",
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


def reset_adjustment(settings, name):
    """Reset an adjustment to its default value"""
    setting_key = SETTING_KEYS.get(name)
    if not setting_key:
        return False

    default_value = DEFAULT_VALUES.get(name)
    if default_value is None:
        return False

    if name in ["crop_left", "crop_right", "crop_top", "crop_bottom"]:
        return settings.set_int(setting_key, default_value)
    else:
        return settings.set_double(setting_key, default_value)


#
# Value Conversion Functions
#
def ui_to_ffmpeg_hue(ui_hue):
    """Convert UI hue (radians) to FFmpeg hue (degrees)"""
    return ui_hue * 180 / 3.14159


#
# FFmpeg Filter Generation
#
def generate_video_filters(settings, video_width=None, video_height=None, input_file=None):
    """
    Generate all needed FFmpeg filters in one go.

    Args:
        settings: Settings manager
        video_width: Width of the video (needed for crop)
        video_height: Height of the video (needed for crop)
        input_file: Path to input file (needed for H.265 10-bit detection)

    Returns:
        List of filter strings ready to join with commas
    """
    filters = []

    # Check if this is H.265 10-bit to H.264 conversion (skip custom filters in this case)
    is_hevc_10bit_to_h264 = False
    if input_file:
        try:
            # Quick check for H.265 10-bit to H.264 conversion
            import subprocess
            result = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=pix_fmt,codec_name", "-of", "csv=p=0", input_file
            ], capture_output=True, text=True)
            
            output = result.stdout.strip().split(',')
            if len(output) >= 2:
                pix_fmt = output[0]
                codec = output[1]
                
                is_10bit = "p10" in pix_fmt or "10le" in pix_fmt
                is_hevc = codec in ['hevc', 'h265']
                is_h264_output = settings.get_value("video-codec", "h264") == "h264"
                
                if is_10bit and is_hevc and is_h264_output:
                    is_hevc_10bit_to_h264 = True
                    print("Detected H.265 10-bit to H.264 conversion - using optimized GPU filters")
        except:
            pass

    # For H.265 10-bit to H.264 conversion, let the GPU handle format conversion
    if is_hevc_10bit_to_h264:
        print("Skipping custom video filters for optimized H.265 10-bit to H.264 GPU conversion")
        return []  # Let the bash script handle the GPU-optimized conversion

    # DEBUG: Print settings values to verify they're being read
    debug_values = {
        "brightness": get_adjustment_value(settings, "brightness"),
        "contrast": get_adjustment_value(settings, "contrast"),
        "saturation": get_adjustment_value(settings, "saturation"),
        "gamma": get_adjustment_value(settings, "gamma"),
        "hue": get_adjustment_value(settings, "hue"),
        "crop_left": get_adjustment_value(settings, "crop_left"),
        "crop_right": get_adjustment_value(settings, "crop_right"),
        "crop_top": get_adjustment_value(settings, "crop_top"),
        "crop_bottom": get_adjustment_value(settings, "crop_bottom"),
    }
    print(f"Video adjustment values: {debug_values}")
    print(f"Video dimensions for crop: width={video_width}, height={video_height}")

    # 1. Add crop filter if needed
    crop_left = get_adjustment_value(settings, "crop_left")
    crop_right = get_adjustment_value(settings, "crop_right")
    crop_top = get_adjustment_value(settings, "crop_top")
    crop_bottom = get_adjustment_value(settings, "crop_bottom")

    if (
        (crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0)
        and video_width is not None
        and video_height is not None
    ):
        # Calculate the final dimensions after cropping
        crop_width = video_width - crop_left - crop_right
        crop_height = video_height - crop_top - crop_bottom

        if crop_width > 0 and crop_height > 0:
            crop_filter = f"crop={crop_width}:{crop_height}:{crop_left}:{crop_top}"
            filters.append(crop_filter)
            print(f"Adding crop filter: {crop_filter}")
        else:
            print(f"Invalid crop dimensions: width={crop_width}, height={crop_height}")
    elif crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0:
        # We have crop values but are missing dimensions
        print(
            f"Cannot apply crop: Missing video dimensions. Have left={crop_left}, right={crop_right}, top={crop_top}, bottom={crop_bottom}"
        )
        print(f"Video dimensions required: width={video_width}, height={video_height}")

    # 2. Add hue adjustment
    hue = get_adjustment_value(settings, "hue")
    if abs(hue) > FLOAT_THRESHOLD:
        hue_degrees = ui_to_ffmpeg_hue(hue)
        filters.append(f"hue=h={hue_degrees}")

    # 3. Add eq filter for brightness, contrast, saturation, gamma
    eq_parts = []

    brightness = get_adjustment_value(settings, "brightness")
    if abs(brightness) > FLOAT_THRESHOLD:
        eq_parts.append(f"brightness={brightness}")

    contrast = get_adjustment_value(settings, "contrast")
    if abs(contrast - 1.0) > FLOAT_THRESHOLD:
        # Use contrast value directly instead of calculating a delta
        eq_parts.append(f"contrast={contrast}")

    saturation = get_adjustment_value(settings, "saturation")
    if abs(saturation - 1.0) > FLOAT_THRESHOLD:
        eq_parts.append(f"saturation={saturation}")

    gamma = get_adjustment_value(settings, "gamma")
    if abs(gamma - 1.0) > FLOAT_THRESHOLD:
        eq_parts.append(f"gamma={gamma}")

    gamma_r = get_adjustment_value(settings, "gamma_r")
    if abs(gamma_r - 1.0) > FLOAT_THRESHOLD:
        eq_parts.append(f"gamma_r={gamma_r}")

    gamma_g = get_adjustment_value(settings, "gamma_g")
    if abs(gamma_g - 1.0) > FLOAT_THRESHOLD:
        eq_parts.append(f"gamma_g={gamma_g}")

    gamma_b = get_adjustment_value(settings, "gamma_b")
    if abs(gamma_b - 1.0) > FLOAT_THRESHOLD:
        eq_parts.append(f"gamma_b={gamma_b}")

    gamma_weight = get_adjustment_value(settings, "gamma_weight")
    if abs(gamma_weight - 1.0) > FLOAT_THRESHOLD:
        eq_parts.append(f"gamma_weight={gamma_weight}")

    # Add eq filter if we have parts
    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    # 4. Add resolution scaling if needed
    video_resolution = settings.get_value("video-resolution", "")
    if video_resolution:
        # Ensure we use the right format (width:height)
        if "x" in video_resolution:
            video_resolution = video_resolution.replace("x", ":")
        filters.append(f"scale={video_resolution}")

    # Debug what filters were generated
    print(f"Generated filters: {filters}")

    return filters


def get_ffmpeg_filter_string(settings, video_width=None, video_height=None, input_file=None):
    """Get the complete FFmpeg filter string for command-line use"""
    filters = generate_video_filters(settings, video_width, video_height, input_file)

    if not filters:
        return ""

    filter_string = ",".join(filters)
    return f"-vf {filter_string}"


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

        # Cache adjustment values
        self.values = {}
        for name in DEFAULT_VALUES:
            self.values[name] = self.get_value(name)

    def get_value(self, name):
        """Get adjustment value from settings"""
        return get_adjustment_value(self.settings, name)

    def set_value(self, name, value, update_ui=True):
        """Set adjustment value and update UI if requested"""
        setting_key = SETTING_KEYS.get(name)
        if not setting_key:
            return False

        # Store value in cache
        self.values[name] = value

        # Save to settings
        success = save_adjustment_value(self.settings, name, value)

        # Update UI if requested
        if update_ui and success and self.page:
            self._update_ui_for_setting(name, value)

        return success

    def reset_value(self, name, update_ui=True):
        """Reset adjustment to default value"""
        default = DEFAULT_VALUES.get(name)
        if default is None:
            return False

        return self.set_value(name, default, update_ui)

    def reset_all_values(self, update_ui=True):
        """Reset all adjustments to defaults"""
        for name, default in DEFAULT_VALUES.items():
            self.set_value(name, default, False)

        if update_ui and self.page:
            self._update_all_ui()

        return True

    def _update_ui_for_setting(self, name, value):
        """Update UI control for a specific setting"""
        if not self.page or not hasattr(self.page, "ui"):
            return

        ui = self.page.ui

        # Map setting name to UI control
        ui_controls = {
            "brightness": getattr(ui, "brightness_scale", None),
            "contrast": getattr(ui, "contrast_scale", None),
            "saturation": getattr(ui, "saturation_scale", None),
            "gamma": getattr(ui, "gamma_scale", None),
            "gamma_r": getattr(ui, "red_gamma_scale", None),
            "gamma_g": getattr(ui, "green_gamma_scale", None),
            "gamma_b": getattr(ui, "blue_gamma_scale", None),
            "gamma_weight": getattr(ui, "gamma_weight_scale", None),
            "hue": getattr(ui, "hue_scale", None),
            "crop_left": getattr(ui, "crop_left_spin", None),
            "crop_right": getattr(ui, "crop_right_spin", None),
            "crop_top": getattr(ui, "crop_top_spin", None),
            "crop_bottom": getattr(ui, "crop_bottom_spin", None),
        }

        control = ui_controls.get(name)
        if control:
            control.set_value(value)

    def _update_all_ui(self):
        """Update all UI controls with current values"""
        # Update all controls
        for name, value in self.values.items():
            self._update_ui_for_setting(name, value)

        # Refresh preview if possible
        if hasattr(self.page, "processor") and hasattr(self.page, "current_position"):
            if hasattr(self.page, "invalidate_current_frame_cache"):
                self.page.invalidate_current_frame_cache()
            self.page.processor.extract_frame(self.page.current_position)


# Add these new functions to validate trim settings


def validate_trim_settings(settings, duration=None):
    """
    Validate trim start and end times

    Args:
        settings: Settings manager
        duration: Video duration (optional)

    Returns:
        (bool, str): (is_valid, error_message)
    """
    start_time = get_adjustment_value(settings, "trim_start")
    end_time = get_adjustment_value(settings, "trim_end")

    # Check if end time is set and valid
    if end_time is not None:
        if start_time >= end_time:
            return False, "End time must be greater than start time"

    # Check against duration if provided
    if duration is not None:
        if start_time >= duration:
            return False, "Start time cannot be greater than video duration"
        if end_time is not None and end_time > duration:
            # Auto-adjust end time to duration
            save_adjustment_value(settings, "trim_end", duration)

    return True, ""


def get_trim_duration(settings):
    """
    Calculate the duration between trim start and end times

    Args:
        settings: Settings manager

    Returns:
        float: Duration in seconds, or None if trim end is not set
    """
    start_time = get_adjustment_value(settings, "trim_start")
    end_time = get_adjustment_value(settings, "trim_end")

    if end_time is None:
        return None

    return end_time - start_time


def format_time_for_ffmpeg(seconds):
    """
    Format time in seconds to HH:MM:SS.mmm format for ffmpeg

    Args:
        seconds: Time in seconds

    Returns:
        str: Formatted time string
    """
    if seconds is None:
        return "0"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def get_trim_params(settings):
    """
    Get FFmpeg trim parameters based on current settings

    Args:
        settings: Settings manager

    Returns:
        dict: Dictionary with trim parameters or empty if no trimming
    """
    start_time = get_adjustment_value(settings, "trim_start")
    end_time = get_adjustment_value(settings, "trim_end")

    params = {}

    # Only add trim parameters if they're actually needed
    if start_time > 0:
        params["trim_start"] = format_time_for_ffmpeg(start_time)

    if end_time is not None:
        # Calculate duration for FFmpeg trimming
        duration = end_time - start_time
        if duration > 0:
            params["trim_duration"] = format_time_for_ffmpeg(duration)

    # Debug info
    if params:
        print(f"Trim parameters: {params}")

    return params
