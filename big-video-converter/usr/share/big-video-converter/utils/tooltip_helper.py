"""
Tooltip helper for showing helpful explanations on UI elements.
Provides a simple way to add tooltips to any GTK widget.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from constants import get_tooltips


class TooltipHelper:
    """Helper class to manage tooltips across the application."""

    def __init__(self, settings_manager):
        """Initialize the tooltip helper with settings manager."""
        self.settings_manager = settings_manager
        self.tooltip_popovers = {}  # Store popovers for cleanup
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
            "enter", lambda c, x, y: self._show_tooltip(popover) if self.is_enabled() else None
        )
        motion_controller.connect("leave", lambda c: self._hide_tooltip(popover))
        widget.add_controller(motion_controller)

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
                # Hide all tooltips if disabled
                popover.popdown()

    def cleanup(self):
        """Clean up all tooltip popovers."""
        for popover in self.tooltip_popovers.values():
            popover.unparent()
        self.tooltip_popovers.clear()
