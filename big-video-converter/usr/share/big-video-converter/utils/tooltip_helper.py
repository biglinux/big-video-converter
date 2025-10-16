"""
Tooltip helper for showing helpful explanations on UI elements.
Provides a simple way to add tooltips to any GTK widget.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from constants import get_tooltips


class TooltipHelper:
    """Helper class to manage tooltips across the application."""

    def __init__(self, settings_manager):
        """Initialize the tooltip helper with settings manager."""
        self.settings_manager = settings_manager
        self.tooltip_popovers = {}  # Store popovers for cleanup
        self.tooltip_timers = {}  # Store timer IDs for delay
        self.tooltips = get_tooltips()  # Get translated tooltips

    def is_enabled(self):
        """Check if tooltips are enabled in settings."""
        return self.settings_manager.load_setting("show-tooltips", True)

    def add_tooltip(self, widget, tooltip_key):
        """
        Add a tooltip to a widget.

        Args:
            widget: The GTK widget to attach tooltip to
            tooltip_key: The key in TOOLTIPS dictionary
        """
        if not self.is_enabled():
            return

        tooltip_text = self.tooltips.get(tooltip_key)
        if not tooltip_text:
            return

        # Create popover for this widget
        popover = Gtk.Popover()
        popover.set_autohide(False)
        popover.set_position(Gtk.PositionType.TOP)
        popover.set_parent(widget)

        # Create label with tooltip text
        label = Gtk.Label()
        label.set_text(tooltip_text)
        label.set_wrap(True)
        label.set_max_width_chars(50)
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        label.set_halign(Gtk.Align.START)
        popover.set_child(label)

        # Store popover reference
        self.tooltip_popovers[widget] = popover

        # Add motion controller to show/hide tooltip
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect(
            "enter", lambda c, x, y: self._schedule_show_tooltip(widget, popover) if self.is_enabled() else None
        )
        motion_controller.connect("leave", lambda c: self._cancel_and_hide_tooltip(widget, popover))
        widget.add_controller(motion_controller)

    def _schedule_show_tooltip(self, widget, popover):
        """Schedule tooltip to show after 200ms delay."""
        # Cancel any existing timer for this widget
        if widget in self.tooltip_timers:
            GLib.source_remove(self.tooltip_timers[widget])
            del self.tooltip_timers[widget]
        
        # Schedule tooltip to show after 200ms
        timer_id = GLib.timeout_add(200, lambda: self._show_tooltip_with_animation(widget, popover))
        self.tooltip_timers[widget] = timer_id

    def _show_tooltip_with_animation(self, widget, popover):
        """Show tooltip popover with 200ms fade-in animation."""
        # Remove timer reference
        if widget in self.tooltip_timers:
            del self.tooltip_timers[widget]
        
        # Set initial opacity to 0
        popover.set_opacity(0.0)
        
        # Show the popover
        popover.popup()
        
        # Animate opacity from 0 to 1 over 200ms
        self._animate_opacity(popover, 0.0, 1.0, 200)
        
        return False  # Don't repeat timer

    def _cancel_and_hide_tooltip(self, widget, popover):
        """Cancel scheduled tooltip and hide if visible."""
        # Cancel any pending timer
        if widget in self.tooltip_timers:
            GLib.source_remove(self.tooltip_timers[widget])
            del self.tooltip_timers[widget]
        
        # Hide tooltip with animation
        self._hide_tooltip(popover)

    def _animate_opacity(self, popover, start_opacity, end_opacity, duration_ms):
        """Animate popover opacity over specified duration."""
        steps = 20  # Number of animation steps
        step_duration = duration_ms // steps
        opacity_increment = (end_opacity - start_opacity) / steps
        current_step = [0]  # Use list to allow modification in nested function
        
        def update_opacity():
            current_step[0] += 1
            new_opacity = start_opacity + (opacity_increment * current_step[0])
            
            if current_step[0] >= steps:
                popover.set_opacity(end_opacity)
                return False  # Stop animation
            else:
                popover.set_opacity(new_opacity)
                return True  # Continue animation
        
        GLib.timeout_add(step_duration, update_opacity)

    def _show_tooltip(self, popover):
        """Show tooltip popover."""
        popover.popup()

    def _hide_tooltip(self, popover):
        """Hide tooltip popover."""
        popover.popdown()

    def refresh_all(self):
        """Refresh all tooltips based on current settings."""
        enabled = self.is_enabled()

        for widget, popover in self.tooltip_popovers.items():
            if not enabled:
                # Hide all tooltips if disabled and cancel any pending timers
                if widget in self.tooltip_timers:
                    GLib.source_remove(self.tooltip_timers[widget])
                    del self.tooltip_timers[widget]
                popover.popdown()

    def cleanup(self):
        """Clean up all tooltip popovers and timers."""
        # Cancel all pending timers
        for timer_id in self.tooltip_timers.values():
            GLib.source_remove(timer_id)
        self.tooltip_timers.clear()
        
        # Clean up popovers
        for popover in self.tooltip_popovers.values():
            popover.unparent()
        self.tooltip_popovers.clear()

