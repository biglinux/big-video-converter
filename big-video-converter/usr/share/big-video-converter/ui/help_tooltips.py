"""Help tooltips by key, shown as the shared BigLinux card."""

from constants import get_tooltips


class HelpTooltips:
    def __init__(self):
        self.tooltips = get_tooltips()

    def add_tooltip(self, widget, tooltip_key) -> None:
        """big_gtk_kit.tooltip draws the card; GTK keeps the accessible text."""
        widget.set_tooltip_text(self.tooltips.get(tooltip_key) or None)
