"""
A robust, state-managed tooltip helper for GTK4.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib

from constants import get_tooltips


class TooltipHelper:
    """
    Manages a single, reusable Gtk.Popover to display custom tooltips.

    Rationale: This is the canonical implementation. It uses a singleton popover
    to prevent state conflicts. The animation is handled by CSS classes, and the
    fade-in is reliably triggered by hooking into the popover's "map" signal.
    This avoids all race conditions with the GTK renderer.
    """

    def __init__(self, settings_manager):
        self.settings_manager = settings_manager
        self.tooltips = get_tooltips()

        # --- State Machine Variables ---
        self.active_widget = None
        self.show_timer_id = None

        # --- The Single, Reusable Popover ---
        self.popover = Gtk.Popover()
        self.popover.set_autohide(False)
        self.popover.set_has_arrow(True)
        self.popover.set_position(Gtk.PositionType.TOP)
        
        self.label = Gtk.Label(
            wrap=True,
            max_width_chars=50,
            margin_start=12,
            margin_end=12,
            margin_top=8,
            margin_bottom=8,
            halign=Gtk.Align.START,
        )
        self.popover.set_child(self.label)

        # --- CSS for Class-Based Animation ---
        self.css_provider = Gtk.CssProvider()
        css = b"""
        .tooltip-popover {
            opacity: 0;
            transition: opacity 250ms ease-in-out;
        }
        .tooltip-popover.visible {
            opacity: 1;
        }
        """
        self.css_provider.load_from_data(css)
        self.popover.add_css_class("tooltip-popover")
        
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Connect to the "map" signal to trigger the fade-in animation.
        self.popover.connect("map", self._on_popover_map)

    def _on_popover_map(self, popover):
        """Called when the popover is drawn. Adds the .visible class to fade in."""
        self.popover.add_css_class("visible")

    def is_enabled(self):
        return self.settings_manager.load_setting("show-tooltips", True)

    def add_tooltip(self, widget, tooltip_key):
        """Connects a widget to the tooltip management system."""
        widget.tooltip_key = tooltip_key
        
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("enter", self._on_enter, widget)
        motion_controller.connect("leave", self._on_leave)
        widget.add_controller(motion_controller)

    def _clear_timer(self):
        if self.show_timer_id:
            GLib.source_remove(self.show_timer_id)
            self.show_timer_id = None

    def _on_enter(self, controller, x, y, widget):
        if not self.is_enabled() or self.active_widget == widget:
            return

        self._clear_timer()
        self._hide_tooltip()

        self.active_widget = widget
        self.show_timer_id = GLib.timeout_add(250, self._show_tooltip)

    def _on_leave(self, controller):
        self._clear_timer()
        if self.active_widget:
            self._hide_tooltip(animate=True)
            self.active_widget = None

    def _show_tooltip(self):
        if not self.active_widget:
            return GLib.SOURCE_REMOVE

        # Safety check: ensure widget is still in valid state
        try:
            if not self.active_widget.get_mapped() or not self.active_widget.get_visible():
                self.active_widget = None
                return GLib.SOURCE_REMOVE
            
            # Check if widget has a valid parent and is in a toplevel
            parent = self.active_widget.get_parent()
            if parent is None:
                self.active_widget = None
                return GLib.SOURCE_REMOVE
            
            # Check if we can get a native ancestor
            native = self.active_widget.get_native()
            if native is None:
                self.active_widget = None
                return GLib.SOURCE_REMOVE
        except Exception:
            self.active_widget = None
            return GLib.SOURCE_REMOVE

        tooltip_key = getattr(self.active_widget, 'tooltip_key', None)
        if not tooltip_key:
            return GLib.SOURCE_REMOVE
            
        tooltip_text = self.tooltips.get(tooltip_key)

        if not tooltip_text:
            return GLib.SOURCE_REMOVE

        try:
            # Configure and place on screen. The popover is initially transparent
            # due to the .tooltip-popover class. The "map" signal will then
            # trigger the animation by adding the .visible class.
            self.label.set_text(tooltip_text)
            
            # Unparent first if already parented
            if self.popover.get_parent() is not None:
                self.popover.unparent()
            
            # Ensure clean CSS state before showing
            self.popover.remove_css_class("visible")
            
            self.popover.set_parent(self.active_widget)
            self.popover.popup()
        except Exception as e:
            print(f"Tooltip error: {e}")
            self.active_widget = None
        
        self.show_timer_id = None
        return GLib.SOURCE_REMOVE

    def _hide_tooltip(self, animate=False):
        try:
            if not self.popover.is_visible():
                return

            def do_cleanup():
                try:
                    self.popover.popdown()
                    if self.popover.get_parent():
                        self.popover.unparent()
                except Exception:
                    pass
                return GLib.SOURCE_REMOVE

            # This triggers the fade-out animation.
            self.popover.remove_css_class("visible")

            if animate:
                # Wait for animation to finish before cleaning up.
                GLib.timeout_add(200, do_cleanup)
            else:
                do_cleanup()
        except Exception:
            pass

    def cleanup(self):
        """Call this when the application is shutting down."""
        self._clear_timer()
        try:
            if self.popover.get_parent():
                self.popover.unparent()
        except Exception:
            pass
