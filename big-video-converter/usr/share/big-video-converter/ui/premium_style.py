"""Application-wide visual system for Big Video Converter.

The CSS intentionally relies on libadwaita semantic colors so it remains
readable in light, dark and high-contrast themes.  Spacing and radii follow a
small set of reusable classes instead of per-widget one-off styling.
"""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gdk, Gtk

_PROVIDER = None


def install() -> None:
    """Install the premium visual system once for the current display."""

    global _PROVIDER
    if _PROVIDER is not None:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    css_path = Path(__file__).with_name("premium.css")
    provider = Gtk.CssProvider()
    provider.load_from_path(str(css_path))
    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _PROVIDER = provider
