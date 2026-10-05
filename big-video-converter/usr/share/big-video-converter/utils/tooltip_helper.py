"""Help tooltips by key, shown as the shared BigLinux card while enabled."""

import weakref

from constants import get_tooltips


class TooltipHelper:
    def __init__(self, settings_manager):
        self.settings_manager = settings_manager
        self.tooltips = get_tooltips()
        self.widgets = weakref.WeakKeyDictionary()

    def is_enabled(self):
        return self.settings_manager.load_setting("show-tooltips", True)

    def add_tooltip(self, widget, tooltip_key) -> None:
        """big_gtk_kit.tooltip draws the card; GTK keeps the accessible text."""
        self.widgets[widget] = self.tooltips.get(tooltip_key) or None
        widget.set_tooltip_text(self.widgets[widget] if self.is_enabled() else None)

    def refresh(self) -> None:
        enabled = self.is_enabled()
        for widget, text in list(self.widgets.items()):
            widget.set_tooltip_text(text if enabled else None)
