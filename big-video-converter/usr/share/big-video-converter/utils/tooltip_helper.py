"""
Tooltip helper for showing helpful explanations on UI elements.
Provides a simple way to add tooltips to any GTK widget using the native GTK tooltip API.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from constants import get_tooltips


class TooltipHelper:
    """Helper class to manage tooltips across the application."""

    def __init__(self, app):
        """Initialize the tooltip helper with the main application instance."""
        self.app = app
        self.settings_manager = app.settings_manager
        self.tooltips = get_tooltips()  # Get translated tooltips
        # Store a map of widgets to their tooltip keys to re-apply them if needed
        self.widget_map = {}

    def is_enabled(self):
        """Check if tooltips are enabled in settings."""
        return self.settings_manager.load_setting("show-tooltips", True)

    def add_tooltip(self, widget, tooltip_key):
        """
        Add a tooltip to a widget using the native GTK tooltip API.

        Args:
            widget: The GTK widget to attach tooltip to.
            tooltip_key: The key in the tooltips dictionary.
        """
        # Store the relationship for later, e.g., if tooltips are toggled off and on
        self.widget_map[widget] = tooltip_key

        if not self.is_enabled():
            widget.set_tooltip_text(None)
            return

        tooltip_text = self.tooltips.get(tooltip_key)
        if tooltip_text:
            widget.set_tooltip_text(tooltip_text)

    def refresh_all(self):
        """Refresh all tooltips based on current settings."""
        enabled = self.is_enabled()

        for widget, tooltip_key in self.widget_map.items():
            if enabled:
                tooltip_text = self.tooltips.get(tooltip_key)
                widget.set_tooltip_text(tooltip_text)
            else:
                widget.set_tooltip_text(None)

    def cleanup(self):
        """Clean up tooltip references."""
        # With the native API, there's less to clean, but clearing the map is good practice.
        self.widget_map.clear()