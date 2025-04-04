import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

# Setup translation
import gettext

_ = gettext.gettext


class HeaderBar(Gtk.Box):
    """
    Custom header bar with tabs and buttons for settings and menu.
    """

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.app = app

        # Garantir que o Box ocupe toda a largura
        self.set_hexpand(True)

        # Create the header bar
        self.header_bar = Adw.HeaderBar()
        # Garantir que o HeaderBar ocupe toda a largura
        self.header_bar.set_hexpand(True)
        self.append(self.header_bar)

        # Create tab buttons container
        self.tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.tab_box.add_css_class("linked")
        self.tab_box.set_halign(Gtk.Align.CENTER)

        # Create tab buttons
        self.conversion_button = Gtk.Button(label=_("Conversion"))
        self.conversion_button.connect("clicked", self._on_tab_clicked, "conversion")
        self.conversion_button.add_css_class("suggested-action")
        self.tab_box.append(self.conversion_button)

        self.preview_button = Gtk.Button(label=_("Video Edit"))
        self.preview_button.connect("clicked", self._on_tab_clicked, "edit")
        self.tab_box.append(self.preview_button)

        # Add settings tab button - treat it like other tabs
        self.settings_button = Gtk.Button(label=_("Settings"))
        self.settings_button.connect("clicked", self._on_tab_clicked, "settings")
        self.tab_box.append(self.settings_button)

        # Store buttons for easy access
        self.tab_buttons = {
            "conversion": self.conversion_button,
            "edit": self.preview_button,
            "settings": self.settings_button,
        }

        # Set title widget for header bar
        self.header_bar.set_title_widget(self.tab_box)

        # Add menu button (three dots)
        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_icon_name("open-menu-symbolic")
        self.menu_button.set_tooltip_text(_("Menu"))

        # Create menu model
        menu = Gio.Menu.new()
        menu.append(_("About"), "app.about")
        menu.append(_("Help"), "app.help")
        menu.append(_("Quit"), "app.quit")

        self.menu_button.set_menu_model(menu)
        self.header_bar.pack_end(self.menu_button)

    def _on_tab_clicked(self, button, tab_name):
        """Handle tab button clicks"""
        if hasattr(self.app, "activate_tab"):
            self.app.activate_tab(tab_name)

    def activate_tab(self, tab_name):
        """Update button styling to reflect current tab"""
        for name, button in self.tab_buttons.items():
            if name == tab_name:
                button.add_css_class("suggested-action")
            else:
                button.remove_css_class("suggested-action")

    def set_tabs_sensitive(self, sensitive):
        """Enable or disable tab buttons"""
        for _, button in self.tab_buttons.items():
            button.set_sensitive(sensitive)
