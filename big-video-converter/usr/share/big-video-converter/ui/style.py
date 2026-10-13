"""Application-wide visual system for Big Video Converter.

The CSS uses libadwaita's semantic colors, so it stays readable in light,
dark and high-contrast themes. Spacing and radii come from a few reusable
classes.
"""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gdk, Gtk

_PROVIDER = None


def install() -> None:
    """Install the application stylesheet once for the current display."""

    global _PROVIDER
    if _PROVIDER is not None:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    css_path = Path(__file__).with_name("style.css")
    provider = Gtk.CssProvider()
    provider.load_from_path(str(css_path))
    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _PROVIDER = provider
