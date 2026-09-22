"""Context-aware premium header for queue and editor views."""

import gettext
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

_ = gettext.gettext


def _labeled_button(label: str, icon_name: str) -> Gtk.Button:
    button = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.append(Gtk.Image.new_from_icon_name(icon_name))
    box.append(Gtk.Label(label=label))
    button.set_child(box)
    return button


class HeaderBar(Gtk.Box):
    """Stable application header with one obvious primary action."""

    def __init__(self, app, window_buttons_left=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.app = app
        self.window_buttons_left = window_buttons_left
        self.set_hexpand(True)

        self.header_bar = Adw.HeaderBar()
        self.header_bar.set_hexpand(True)
        self.header_bar.add_css_class("bvc-app-header")
        self.header_bar.set_decoration_layout(
            "" if window_buttons_left else ":minimize,maximize,close"
        )
        self.append(self.header_bar)

        self.back_button = _labeled_button(_("Back"), "go-previous-symbolic")
        self.back_button.add_css_class("bvc-secondary")
        self.back_button.connect("clicked", self._on_back_clicked)
        self.back_button.set_visible(False)
        self.header_bar.pack_start(self.back_button)

        self.add_button = Adw.SplitButton(label=_("Add videos"))
        self.add_button.set_icon_name("list-add-symbolic")
        self.add_button.add_css_class("bvc-secondary")
        self.add_button.connect("clicked", self._on_add_files_clicked)
        menu = Gio.Menu()
        add_folder = Gio.MenuItem.new(_("Add a folder"), "app.add_folder")
        add_folder.set_icon(Gio.ThemedIcon.new("folder-symbolic"))
        menu.append_item(add_folder)
        add_network = Gio.MenuItem.new(
            _("Add from the network"), "app.add_network_file"
        )
        add_network.set_icon(Gio.ThemedIcon.new("network-server-symbolic"))
        menu.append_item(add_network)
        self.add_button.set_menu_model(menu)
        self.header_bar.pack_start(self.add_button)

        self.clear_queue_button = Gtk.Button.new_from_icon_name(
            "user-trash-symbolic"
        )
        self.clear_queue_button.add_css_class("bvc-icon-button")
        self.clear_queue_button.add_css_class("bvc-quiet")
        self.clear_queue_button.add_css_class("bvc-danger")
        self.clear_queue_button.set_tooltip_text(_("Clear the queue"))
        self.clear_queue_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Clear the queue")]
        )
        self.clear_queue_button.connect("clicked", self._on_clear_queue_clicked)
        self.clear_queue_button.set_visible(False)
        self.header_bar.pack_start(self.clear_queue_button)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_box.set_halign(Gtk.Align.CENTER)
        self.title_label = Gtk.Label(label=_("Your videos"))
        self.title_label.add_css_class("heading")
        self.title_label.set_ellipsize(3)
        self.context_label = Gtk.Label(label=_("Add videos to begin"))
        self.context_label.add_css_class("caption")
        self.context_label.add_css_class("dim-label")
        title_box.append(self.title_label)
        title_box.append(self.context_label)
        self.header_bar.set_title_widget(title_box)

        self.queue_size_label = Gtk.Label(label="")
        self.queue_size_label.add_css_class("bvc-count-chip")
        self.queue_size_label.set_visible(False)
        self.header_bar.pack_end(self.queue_size_label)

        self.convert_button = _labeled_button(
            _("Convert videos"), "media-playback-start-symbolic"
        )
        self.convert_button.add_css_class("suggested-action")
        self.convert_button.add_css_class("bvc-primary")
        self.convert_button.connect("clicked", self._on_convert_all_clicked)
        self.convert_button.set_visible(False)
        self.header_bar.pack_end(self.convert_button)

        self.convert_current_button = _labeled_button(
            _("Convert this video"), "media-playback-start-symbolic"
        )
        self.convert_current_button.add_css_class("suggested-action")
        self.convert_current_button.add_css_class("bvc-primary")
        self.convert_current_button.connect(
            "clicked", self._on_convert_current_clicked
        )
        self.convert_current_button.set_visible(False)
        self.header_bar.pack_end(self.convert_current_button)

        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_button.add_css_class("bvc-icon-button")
        self.menu_button.add_css_class("bvc-quiet")
        self.menu_button.set_tooltip_text(_("Main menu"))
        self.menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Main menu")]
        )
        app_menu = Gio.Menu()
        app_menu.append(_("Welcome and quick tour"), "app.welcome")
        app_menu.append(_("Restore default settings"), "app.restore_settings")
        app_menu.append(_("About Big Video Converter"), "app.about")
        app_menu.append(_("Quit"), "app.quit")
        self.menu_button.set_menu_model(app_menu)
        self.header_bar.pack_end(self.menu_button)

    def _on_add_files_clicked(self, _button):
        if hasattr(self.app, "select_files_for_queue"):
            self.app.select_files_for_queue()

    def _on_back_clicked(self, _button):
        if hasattr(self.app, "show_queue_view"):
            self.app.show_queue_view()

    def _on_clear_queue_clicked(self, _button):
        if hasattr(self.app, "clear_queue"):
            self.app.clear_queue()

    def _on_convert_all_clicked(self, button):
        button.set_sensitive(False)
        if hasattr(self.app, "start_queue_processing"):
            self.app.start_queue_processing()

    def _on_convert_current_clicked(self, button):
        button.set_sensitive(False)
        if hasattr(self.app, "convert_current_file"):
            self.app.convert_current_file()

    def set_buttons_sensitive(self, sensitive: bool) -> None:
        self.add_button.set_sensitive(sensitive)
        self.clear_queue_button.set_sensitive(sensitive)
        self.convert_button.set_sensitive(sensitive)
        self.convert_current_button.set_sensitive(sensitive)

    def update_queue_size(self, count: int) -> None:
        has_files = count > 0
        has_multiple = count > 1
        self.queue_size_label.set_text(
            _("1 video") if count == 1 else _("{} videos").format(count)
        )
        self.queue_size_label.set_visible(has_files)
        self.clear_queue_button.set_visible(has_multiple)
        self.convert_button.set_visible(has_files)
        self.context_label.set_text(
            _("1 video ready")
            if count == 1
            else _("{} videos ready").format(count)
            if count > 1
            else _("Add videos to begin")
        )

    def set_view(self, view_name) -> None:
        queue = view_name == "queue"
        self.add_button.set_visible(queue)
        self.clear_queue_button.set_visible(
            queue and len(getattr(self.app, "conversion_queue", ())) > 1
        )
        self.queue_size_label.set_visible(
            queue and len(getattr(self.app, "conversion_queue", ())) > 0
        )
        self.convert_button.set_visible(
            queue and len(getattr(self.app, "conversion_queue", ())) > 0
        )
        self.convert_current_button.set_visible(not queue)
        self.back_button.set_visible(not queue)
        if queue:
            self.title_label.set_text(_("Your videos"))
            self.update_queue_size(len(getattr(self.app, "conversion_queue", ())))
            self.convert_button.set_sensitive(True)
        else:
            self.title_label.set_text(_("Edit video"))
            self.context_label.set_text(
                _("Changes apply only to the selected video")
            )
            self.convert_current_button.set_sensitive(True)
