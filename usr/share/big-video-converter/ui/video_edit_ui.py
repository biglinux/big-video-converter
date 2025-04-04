import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

# Setup translation
import gettext

_ = gettext.gettext


class VideoEditUI:
    def __init__(self, page):
        self.page = page

    def create_page(self):
        """Create the main page layout and all UI elements"""
        # Create main container with a vertical BoxLayout
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Create a Paned container to allow resizing between video and controls
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_wide_handle(True)  # Make handle easier to grab
        paned.set_vexpand(True)

        # Add size constraints to prevent either part from becoming too small
        paned.set_shrink_start_child(
            False
        )  # Don't shrink the top part below its minimum
        paned.set_shrink_end_child(
            False
        )  # Don't shrink the bottom part below its minimum
        paned.set_resize_start_child(True)  # Allow the top part to be resized
        paned.set_resize_end_child(True)  # Allow the bottom part to be resized

        page.append(paned)

        # Create fixed preview area for the top pane - without any margins
        fixed_preview_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        fixed_preview_area.set_vexpand(True)
        fixed_preview_area.set_hexpand(True)
        fixed_preview_area.set_size_request(-1, 200)  # Set minimum height to 200px
        fixed_preview_area.add_css_class("background")

        # Create a simple image widget for the preview - directly in the container
        self.preview_image = Gtk.Picture()
        self.preview_image.set_can_shrink(True)
        self.preview_image.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.preview_image.set_hexpand(True)
        self.preview_image.set_vexpand(True)

        # Add the image directly to the fixed area for better screen usage
        fixed_preview_area.append(self.preview_image)

        # Add the fixed preview area to the top pane
        paned.set_start_child(fixed_preview_area)

        # Add ScrolledWindow for the rest of the content in the bottom pane
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_size_request(-1, 200)  # Set minimum height to 200px

        # This is our reference to the scrolled window
        self.scrolled_window = scrolled_window

        # Container for scrollable content
        scrollable_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrollable_content.set_vexpand(True)
        scrolled_window.set_child(scrollable_content)

        # Add the scrolled window to the bottom pane
        paned.set_end_child(scrolled_window)

        # Set initial position (approximately 60% for the video, 40% for controls)
        # This will be adjusted when the window is resized
        GLib.idle_add(lambda: paned.set_position(400))

        # Use Adw.Clamp for consistent width
        clamp = Adw.Clamp()
        clamp.set_maximum_size(900)
        clamp.set_tightening_threshold(700)
        scrollable_content.append(clamp)

        # Main content box for scrollable area
        main_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_content.set_spacing(24)
        main_content.set_margin_start(12)
        main_content.set_margin_end(12)
        main_content.set_margin_bottom(24)
        clamp.set_child(main_content)

        # Add the playback controls
        main_content.append(self._create_playback_controls())

        # Add the trimming controls
        main_content.append(self._create_trimming_controls())

        # Add the crop controls
        main_content.append(self._create_crop_controls())

        # Add the video adjustment controls
        main_content.append(self._create_adjustments_group())

        # Add the video information section
        main_content.append(self._create_info_group())

        # Store a reference to the main content area
        self.main_content = main_content

        # Create a custom tooltip popover for immediate display
        self._setup_tooltip_popover()

        return page

    def _setup_tooltip_popover(self):
        """Set up the tooltip popover for sliders"""
        self.tooltip_popover = Gtk.Popover()
        self.tooltip_popover.set_autohide(False)  # Don't hide when clicked elsewhere
        self.tooltip_popover.set_position(Gtk.PositionType.TOP)

        # Add a label to the popover
        self.tooltip_label = Gtk.Label()
        self.tooltip_label.set_margin_start(8)
        self.tooltip_label.set_margin_end(8)
        self.tooltip_label.set_margin_top(4)
        self.tooltip_label.set_margin_bottom(4)
        self.tooltip_popover.set_child(self.tooltip_label)

    def add_tooltip_to_slider(self, slider, format_func):
        """Add instant tooltip functionality to any slider"""
        # Store the format function with the slider for use in the motion handler
        slider.format_func = format_func

        # Create a unique tooltip popover for this slider
        tooltip_popover = Gtk.Popover()
        tooltip_popover.set_autohide(False)
        tooltip_popover.set_position(Gtk.PositionType.TOP)

        # Add a label to the popover
        tooltip_label = Gtk.Label()
        tooltip_label.set_margin_start(8)
        tooltip_label.set_margin_end(8)
        tooltip_label.set_margin_top(4)
        tooltip_label.set_margin_bottom(4)
        tooltip_popover.set_child(tooltip_label)

        # Store the tooltip popover in our dictionary with the slider as the key
        self.page.adjustment_tooltips[slider] = {
            "popover": tooltip_popover,
            "label": tooltip_label,
        }

        # Add motion controller to show tooltip
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("motion", self.page.on_adjustment_motion)
        motion_controller.connect("leave", self.page.on_adjustment_leave)
        slider.add_controller(motion_controller)

    def add_tooltip_to_button(self, button, tooltip_text):
        """Add custom tooltip functionality to any button"""
        # Create a unique tooltip popover for this button
        tooltip_popover = Gtk.Popover()
        tooltip_popover.set_autohide(False)
        tooltip_popover.set_position(Gtk.PositionType.TOP)

        # Add a label to the popover
        tooltip_label = Gtk.Label()
        tooltip_label.set_text(tooltip_text)
        tooltip_label.set_margin_start(8)
        tooltip_label.set_margin_end(8)
        tooltip_label.set_margin_top(4)
        tooltip_label.set_margin_bottom(4)
        tooltip_popover.set_child(tooltip_label)

        # Store the tooltip popover in our dictionary with the button as the key
        self.page.button_tooltips[button] = {
            "popover": tooltip_popover,
            "label": tooltip_label,
            "text": tooltip_text,
        }

        # Add motion controller to show/hide tooltip
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("enter", self.page.on_button_enter)
        motion_controller.connect("leave", self.page.on_button_leave)
        button.add_controller(motion_controller)

        # Remove the standard tooltip if it exists
        button.set_tooltip_text(None)

    def _create_playback_controls(self):
        """Create the playback controls group"""
        playback_group = Adw.PreferencesGroup()

        # Create position controls box directly in the group (no ActionRow wrapper)
        position_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        position_box.set_margin_top(12)
        position_box.set_margin_bottom(12)
        position_box.set_margin_start(12)
        position_box.set_margin_end(12)
        position_box.set_hexpand(True)

        # Position slider
        slider_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        adjustment = Gtk.Adjustment(value=0, lower=0, upper=100, step_increment=1)
        self.position_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment
        )
        self.position_scale.set_draw_value(False)
        self.position_scale.set_hexpand(True)
        self.page.position_changed_handler_id = self.position_scale.connect(
            "value-changed", self.page.on_position_changed
        )

        # Add motion controller for tooltip hover functionality
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("motion", self.page.on_slider_motion)
        motion_controller.connect("leave", self.page.on_slider_leave)
        self.position_scale.add_controller(motion_controller)

        # Store current hover position for tooltips
        self.hover_position = 0

        slider_box.append(self.position_scale)
        position_box.append(slider_box)

        # Create a combined info and navigation bar
        info_nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        info_nav_box.set_hexpand(True)

        # Position label on the left side
        self.position_label = Gtk.Label(label="0:00.000 / 0:00.000")
        self.position_label.set_halign(Gtk.Align.START)
        self.position_label.set_hexpand(True)
        info_nav_box.append(self.position_label)

        # Navigation buttons in center
        nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        nav_box.set_halign(Gtk.Align.CENTER)
        nav_box.set_hexpand(True)

        # Previous/next frame buttons
        prev_frame_button = Gtk.Button()
        prev_frame_button.set_icon_name("go-previous-symbolic")
        self.add_tooltip_to_button(prev_frame_button, _("Previous frame"))
        prev_frame_button.connect("clicked", lambda b: self.page.seek_relative(-1 / 25))
        nav_box.append(prev_frame_button)

        # Step back 1 second
        step_back_button = Gtk.Button()
        step_back_button.set_icon_name("media-seek-backward-symbolic")
        self.add_tooltip_to_button(step_back_button, _("Back 1 second"))
        step_back_button.connect("clicked", lambda b: self.page.seek_relative(-1))
        nav_box.append(step_back_button)

        # Step back 10 seconds
        step_back10_button = Gtk.Button()
        step_back10_button.set_icon_name("media-skip-backward-symbolic")
        self.add_tooltip_to_button(step_back10_button, _("Back 10 seconds"))
        step_back10_button.connect("clicked", lambda b: self.page.seek_relative(-10))
        nav_box.append(step_back10_button)

        # Reset button
        reset_button = Gtk.Button(label=_("Reset"))
        reset_button.add_css_class("destructive-action")  # Red styling for warning
        self.add_tooltip_to_button(reset_button, _("Reset all settings"))
        reset_button.connect("clicked", self.page.on_reset_all_settings)
        nav_box.append(reset_button)

        # Step forward 10 seconds
        step_fwd10_button = Gtk.Button()
        step_fwd10_button.set_icon_name("media-skip-forward-symbolic")
        self.add_tooltip_to_button(step_fwd10_button, _("Forward 10 seconds"))
        step_fwd10_button.connect("clicked", lambda b: self.page.seek_relative(10))
        nav_box.append(step_fwd10_button)

        # Step forward 1 second
        step_fwd_button = Gtk.Button()
        step_fwd_button.set_icon_name("media-seek-forward-symbolic")
        self.add_tooltip_to_button(step_fwd_button, _("Forward 1 second"))
        step_fwd_button.connect("clicked", lambda b: self.page.seek_relative(1))
        nav_box.append(step_fwd_button)

        # Next frame button
        next_frame_button = Gtk.Button()
        next_frame_button.set_icon_name("go-next-symbolic")
        self.add_tooltip_to_button(next_frame_button, _("Next frame"))
        next_frame_button.connect("clicked", lambda b: self.page.seek_relative(1 / 25))
        nav_box.append(next_frame_button)

        info_nav_box.append(nav_box)

        # Frame counter on the right side
        self.frame_label = Gtk.Label(label="Frame: 0/0")
        self.frame_label.set_halign(Gtk.Align.END)
        self.frame_label.set_hexpand(True)
        info_nav_box.append(self.frame_label)

        position_box.append(info_nav_box)

        # Add position controls directly to the group
        playback_group.add(position_box)

        return playback_group

    def _create_trimming_controls(self):
        """Create the trimming controls group"""
        trim_group = Adw.PreferencesGroup(title=_("Trim by Time"))

        # Create an ActionRow for trim controls
        trim_row = Adw.ActionRow()
        trim_row.set_activatable(False)

        # Create a single row for all trim controls
        trim_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        trim_box.set_margin_top(12)
        trim_box.set_margin_bottom(12)

        # Start time section
        start_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        start_box.set_hexpand(True)

        self.start_time_label = Gtk.Label(label="0:00.000")
        self.start_time_label.set_halign(Gtk.Align.START)
        self.start_time_label.set_width_chars(8)

        set_start_button = Gtk.Button(label=_("Start time"))
        self.add_tooltip_to_button(
            set_start_button, _("Set timeline marked time as start")
        )
        set_start_button.connect("clicked", self.page.on_set_start_time)

        start_box.append(set_start_button)
        start_box.append(self.start_time_label)
        trim_box.append(start_box)

        # End time section
        end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        end_box.set_hexpand(True)

        self.end_time_label = Gtk.Label()
        self.end_time_label.set_halign(Gtk.Align.START)
        self.end_time_label.set_width_chars(8)

        set_end_button = Gtk.Button(label=_("End time"))
        self.add_tooltip_to_button(set_end_button, _("Set timeline marked time as end"))
        set_end_button.connect("clicked", self.page.on_set_end_time)

        end_box.append(set_end_button)
        end_box.append(self.end_time_label)
        trim_box.append(end_box)

        # Duration section
        duration_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        duration_box.set_hexpand(False)

        duration_label = Gtk.Label(label=_("Duration:"))
        duration_label.set_halign(Gtk.Align.END)

        self.duration_label = Gtk.Label(label="0:00.000")
        self.duration_label.set_halign(Gtk.Align.START)

        duration_box.append(duration_label)
        duration_box.append(self.duration_label)
        trim_box.append(duration_box)

        # Reset button with icon
        reset_button = Gtk.Button()
        reset_button.set_icon_name("edit-undo-symbolic")
        reset_button.add_css_class("flat")
        reset_button.add_css_class("circular")
        self.add_tooltip_to_button(reset_button, _("Reset trim points"))
        reset_button.connect("clicked", self.page.on_reset_trim_points)
        trim_box.append(reset_button)

        # Add the trim box to the row and the row to the group
        trim_row.add_suffix(trim_box)
        trim_group.add(trim_row)

        return trim_group

    def _create_crop_controls(self):
        """Create the crop controls group"""
        crop_group = Adw.PreferencesGroup(title=_("Crop by Edges"))

        # Crop dimension controls with new terminology
        crop_controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        crop_controls_box.set_margin_top(12)
        crop_controls_box.set_margin_bottom(12)
        crop_controls_box.set_margin_start(16)
        crop_controls_box.set_margin_end(16)
        crop_controls_box.add_css_class("card")
        crop_controls_box.set_margin_start(0)
        crop_controls_box.set_margin_end(0)

        # Create a box for the crop margin inputs in a single horizontal row
        crop_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        crop_box.set_halign(Gtk.Align.FILL)
        # Add card styling to match other widgets
        crop_box.set_margin_top(8)
        crop_box.set_margin_bottom(12)
        crop_box.set_margin_start(12)
        crop_box.set_margin_end(12)
        # Use individual margins instead of set_padding
        crop_box.set_margin_top(crop_box.get_margin_top() + 12)
        crop_box.set_margin_bottom(crop_box.get_margin_bottom() + 12)
        crop_box.set_margin_start(crop_box.get_margin_start() + 12)
        crop_box.set_margin_end(crop_box.get_margin_end() + 12)

        # Create the crop controls for all four sides (left, right, top, bottom)
        # Left margin - vertical layout with label at top
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left_box.set_hexpand(True)
        left_box.set_halign(Gtk.Align.CENTER)

        left_label = Gtk.Label(label=_("Left Side"))
        left_label.set_halign(Gtk.Align.CENTER)
        left_box.append(left_label)

        left_input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        left_input_box.set_halign(Gtk.Align.CENTER)

        adjustment = Gtk.Adjustment(value=0, lower=0, upper=9999, step_increment=1)
        self.crop_left_spin = Gtk.SpinButton()
        self.crop_left_spin.set_adjustment(adjustment)
        self.crop_left_spin.set_numeric(True)
        self.crop_left_spin.set_width_chars(5)  # Set uniform width
        self.crop_left_spin.connect("value-changed", self.page.on_crop_value_changed)
        left_input_box.append(self.crop_left_spin)

        left_reset = Gtk.Button()
        left_reset.set_icon_name("edit-undo-symbolic")
        left_reset.add_css_class("flat")
        left_reset.add_css_class("circular")
        self.add_tooltip_to_button(left_reset, _("Reset to default"))
        left_reset.connect("clicked", lambda b: self.page.reset_crop_value("left"))
        left_input_box.append(left_reset)

        left_box.append(left_input_box)
        crop_box.append(left_box)

        # Right, Top, and Bottom crop controls follow the same pattern
        # Right margin control
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right_box.set_hexpand(True)
        right_box.set_halign(Gtk.Align.CENTER)

        right_label = Gtk.Label(label=_("Right Side"))
        right_label.set_halign(Gtk.Align.CENTER)
        right_box.append(right_label)

        right_input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        right_input_box.set_halign(Gtk.Align.CENTER)

        adjustment = Gtk.Adjustment(value=0, lower=0, upper=9999, step_increment=1)
        self.crop_right_spin = Gtk.SpinButton()
        self.crop_right_spin.set_adjustment(adjustment)
        self.crop_right_spin.set_numeric(True)
        self.crop_right_spin.set_width_chars(5)
        self.crop_right_spin.connect("value-changed", self.page.on_crop_value_changed)
        right_input_box.append(self.crop_right_spin)

        right_reset = Gtk.Button()
        right_reset.set_icon_name("edit-undo-symbolic")
        right_reset.add_css_class("flat")
        right_reset.add_css_class("circular")
        self.add_tooltip_to_button(right_reset, _("Reset to default"))
        right_reset.connect("clicked", lambda b: self.page.reset_crop_value("right"))
        right_input_box.append(right_reset)

        right_box.append(right_input_box)
        crop_box.append(right_box)

        # Top margin control
        top_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top_box.set_hexpand(True)
        top_box.set_halign(Gtk.Align.CENTER)

        top_label = Gtk.Label(label=_("Top Side"))
        top_label.set_halign(Gtk.Align.CENTER)
        top_box.append(top_label)

        top_input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        top_input_box.set_halign(Gtk.Align.CENTER)

        adjustment = Gtk.Adjustment(value=0, lower=0, upper=9999, step_increment=1)
        self.crop_top_spin = Gtk.SpinButton()
        self.crop_top_spin.set_adjustment(adjustment)
        self.crop_top_spin.set_numeric(True)
        self.crop_top_spin.set_width_chars(5)
        self.crop_top_spin.connect("value-changed", self.page.on_crop_value_changed)
        top_input_box.append(self.crop_top_spin)

        top_reset = Gtk.Button()
        top_reset.set_icon_name("edit-undo-symbolic")
        top_reset.add_css_class("flat")
        top_reset.add_css_class("circular")
        self.add_tooltip_to_button(top_reset, _("Reset to default"))
        top_reset.connect("clicked", lambda b: self.page.reset_crop_value("top"))
        top_input_box.append(top_reset)

        top_box.append(top_input_box)
        crop_box.append(top_box)

        # Bottom margin control
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bottom_box.set_hexpand(True)
        bottom_box.set_halign(Gtk.Align.CENTER)

        bottom_label = Gtk.Label(label=_("Bottom Side"))
        bottom_label.set_halign(Gtk.Align.CENTER)
        bottom_box.append(bottom_label)

        bottom_input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bottom_input_box.set_halign(Gtk.Align.CENTER)

        adjustment = Gtk.Adjustment(value=0, lower=0, upper=9999, step_increment=1)
        self.crop_bottom_spin = Gtk.SpinButton()
        self.crop_bottom_spin.set_adjustment(adjustment)
        self.crop_bottom_spin.set_numeric(True)
        self.crop_bottom_spin.set_width_chars(5)
        self.crop_bottom_spin.connect("value-changed", self.page.on_crop_value_changed)
        bottom_input_box.append(self.crop_bottom_spin)

        bottom_reset = Gtk.Button()
        bottom_reset.set_icon_name("edit-undo-symbolic")
        bottom_reset.add_css_class("flat")
        bottom_reset.add_css_class("circular")
        self.add_tooltip_to_button(bottom_reset, _("Reset to default"))
        bottom_reset.connect("clicked", lambda b: self.page.reset_crop_value("bottom"))
        bottom_input_box.append(bottom_reset)

        bottom_box.append(bottom_input_box)
        crop_box.append(bottom_box)

        # Result size indicator below the crop controls
        result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        result_box.set_halign(Gtk.Align.CENTER)

        result_label = Gtk.Label()
        result_label.set_markup(
            "<small>" + _("Final size: calculating...") + "</small>"
        )
        result_label.set_halign(Gtk.Align.CENTER)
        result_box.append(result_label)
        self.crop_result_label = result_label

        crop_controls_box.append(crop_box)
        crop_controls_box.append(result_box)

        # Add the crop controls directly to the group for better layout
        crop_group.add(crop_controls_box)

        return crop_group

    def _create_adjustments_group(self):
        """Create the video adjustments group"""
        adjustments_group = Adw.PreferencesGroup(title=_("Video Adjustments"))

        # Add the common adjustment sliders (brightness, contrast, saturation, etc.)
        adjustments_group.add(
            self._create_adjustment_row(
                "Brightness",
                "Between -1.0 and 1.0. Default: 0.0",
                -1.0,
                1.0,
                0.05,
                self.page.brightness,
                self.page.on_brightness_changed,
                self.page.reset_brightness,
            )
        )

        adjustments_group.add(
            self._create_adjustment_row(
                "Contrast",
                "Between 0.0 and 2.0. Default: 1.0",  # Updated to correct FFmpeg range
                0.0,
                2.0,  # New contrast range from 0 to 2
                0.01,  # Small step for precise control
                self.page.contrast,
                self.page.on_contrast_changed,
                self.page.reset_contrast,
            )
        )

        adjustments_group.add(
            self._create_adjustment_row(
                "Saturation",
                "Between 0.0 and 3.0. Default: 1.0",  # FFmpeg range
                0.0,
                3.0,
                0.05,
                self.page.saturation,
                self.page.on_saturation_changed,
                self.page.reset_saturation,
            )
        )

        adjustments_group.add(
            self._create_adjustment_row(
                "Gamma",
                "Between 0.1 and 10.0. Default: 1.0",  # FFmpeg range
                0.1,
                10.0,
                0.1,
                self.page.gamma,
                self.page.on_gamma_changed,
                self.page.reset_gamma,
            )
        )

        adjustments_group.add(
            self._create_adjustment_row(
                "Red Gamma",
                "Between 0.1 and 10.0. Default: 1.0",  # FFmpeg range
                0.1,
                10.0,
                0.1,
                self.page.gamma_r,
                self.page.on_gamma_r_changed,
                self.page.reset_gamma_r,
            )
        )

        adjustments_group.add(
            self._create_adjustment_row(
                "Green Gamma",
                "Between 0.1 and 10.0. Default: 1.0",  # FFmpeg range
                0.1,
                10.0,
                0.1,
                self.page.gamma_g,
                self.page.on_gamma_g_changed,
                self.page.reset_gamma_g,
            )
        )

        adjustments_group.add(
            self._create_adjustment_row(
                "Blue Gamma",
                "Between 0.1 and 10.0. Default: 1.0",  # FFmpeg range
                0.1,
                10.0,
                0.1,
                self.page.gamma_b,
                self.page.on_gamma_b_changed,
                self.page.reset_gamma_b,
            )
        )

        adjustments_group.add(
            self._create_adjustment_row(
                "Gamma Weight",
                "Between 0.0 and 1.0. Default: 1.0",  # FFmpeg range
                0.0,
                1.0,
                0.01,
                self.page.gamma_weight,
                self.page.on_gamma_weight_changed,
                self.page.reset_gamma_weight,
            )
        )

        adjustments_group.add(
            self._create_adjustment_row(
                "Hue",
                "Between -3.14 and 3.14 radians. Default: 0.0",
                -3.14,
                3.14,
                0.05,
                self.page.hue,
                self.page.on_hue_changed,
                self.page.reset_hue,
            )
        )

        return adjustments_group

    def _create_adjustment_row(
        self, title, subtitle, min_val, max_val, step, current_val, on_change, on_reset
    ):
        """Helper to create adjustment rows with consistent styling"""
        row = Adw.ActionRow(title=_(title))
        row.set_subtitle(_(subtitle))
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, min_val, max_val, step
        )
        scale.set_value(current_val)
        scale.set_size_request(400, -1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.connect("value-changed", on_change)

        # Add tooltip functionality
        self.add_tooltip_to_slider(scale, lambda x: f"{x:.2f}")

        reset_button = Gtk.Button()
        reset_button.set_icon_name("edit-undo-symbolic")
        reset_button.add_css_class("flat")
        reset_button.add_css_class("circular")
        self.add_tooltip_to_button(reset_button, _("Reset to default"))
        reset_button.connect("clicked", lambda b: on_reset())

        box.append(scale)
        box.append(reset_button)
        row.add_suffix(box)

        # Save a reference to the scale if needed later
        setattr(self, f"{title.lower().replace(' ', '_')}_scale", scale)

        return row

    def _create_info_group(self):
        """Create the video information section"""
        info_group = Adw.PreferencesGroup(title=_("Video Information"))

        # Create individual rows for each piece of information
        self.info_filename_row = Adw.ActionRow(title=_("Filename"))
        self.info_filename_label = Gtk.Label(label=_("Unknown"))
        self.info_filename_label.set_halign(Gtk.Align.END)
        self.info_filename_row.add_suffix(self.info_filename_label)
        info_group.add(self.info_filename_row)

        self.info_dimensions_row = Adw.ActionRow(title=_("Resolution"))
        self.info_dimensions_label = Gtk.Label(label=_("Unknown"))
        self.info_dimensions_label.set_halign(Gtk.Align.END)
        self.info_dimensions_row.add_suffix(self.info_dimensions_label)
        info_group.add(self.info_dimensions_row)

        self.info_codec_row = Adw.ActionRow(title=_("Codec"))
        self.info_codec_label = Gtk.Label(label=_("Unknown"))
        self.info_codec_label.set_halign(Gtk.Align.END)
        self.info_codec_row.add_suffix(self.info_codec_label)
        info_group.add(self.info_codec_row)

        # Add format row for displaying the format_long_name
        self.info_format_row = Adw.ActionRow(title=_("Format"))
        self.info_format_label = Gtk.Label(label=_("Unknown"))
        self.info_format_label.set_halign(Gtk.Align.END)
        self.info_format_row.add_suffix(self.info_format_label)
        info_group.add(self.info_format_row)

        self.info_filesize_row = Adw.ActionRow(title=_("File Size"))
        self.info_filesize_label = Gtk.Label(label=_("Unknown"))
        self.info_filesize_label.set_halign(Gtk.Align.END)
        self.info_filesize_row.add_suffix(self.info_filesize_label)
        info_group.add(self.info_filesize_row)

        self.info_duration_row = Adw.ActionRow(title=_("Duration"))
        self.info_duration_label = Gtk.Label(label=_("Unknown"))
        self.info_duration_label.set_halign(Gtk.Align.END)
        self.info_duration_row.add_suffix(self.info_duration_label)
        info_group.add(self.info_duration_row)

        self.info_fps_row = Adw.ActionRow(title=_("Frame Rate"))
        self.info_fps_label = Gtk.Label(label=_("Unknown"))
        self.info_fps_label.set_halign(Gtk.Align.END)
        self.info_fps_row.add_suffix(self.info_fps_label)
        info_group.add(self.info_fps_row)

        return info_group
