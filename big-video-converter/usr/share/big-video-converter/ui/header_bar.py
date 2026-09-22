"""Actions and queue status for the queue and editor views."""

import gettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

_ = gettext.gettext
ngettext = gettext.ngettext


def _labeled_button(label: str, icon_name: str) -> Gtk.Button:
    button = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.append(Gtk.Image.new_from_icon_name(icon_name))
    box.append(Gtk.Label(label=label))
    button.set_child(box)
    return button


def _action_popover(actions: tuple[tuple[str, str], ...]) -> Gtk.Popover:
    popover = Gtk.Popover()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    for label, action in actions:
        button = Gtk.Button(label=label, action_name=action)
        button.add_css_class("flat")
        button.get_child().set_xalign(0)
        button.connect("clicked", lambda button: button.get_ancestor(Gtk.Popover).popdown())
        box.append(button)
    popover.set_child(box)
    return popover


class HeaderBar(Gtk.Box):
    """Stable application header with one obvious primary action."""

    def __init__(self, app, window_buttons_left=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.app = app
        self.view_name = "queue"
        self.window_buttons_left = window_buttons_left
        self.set_hexpand(True)

        self.header_bar = Adw.HeaderBar()
        self.header_bar.set_hexpand(True)
        self.header_bar.add_css_class("bvc-app-header")
        self.header_bar.set_decoration_layout(
            "" if window_buttons_left else ":minimize,maximize,close"
        )
        self.append(self.header_bar)
        self.sidebar_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_button.set_tooltip_text(_("Show conversion settings"))
        self.sidebar_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Show conversion settings")])
        self.app.split_view.bind_property("show-sidebar", self.sidebar_button, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.header_bar.pack_start(self.sidebar_button)

        self.back_button = _labeled_button(_("Back"), "go-previous-symbolic")
        self.back_button.add_css_class("bvc-secondary")
        self.back_button.connect("clicked", self._on_back_clicked)
        self.back_button.set_visible(False)
        self.header_bar.pack_start(self.back_button)

        self.add_button = Adw.SplitButton(label=_("Add videos"))
        self.add_button.add_css_class("bvc-secondary")
        self.add_button.connect("clicked", self._on_add_files_clicked)
        self.add_button.set_popover(_action_popover((
            (_("Add a folder"), "app.add_folder"),
            (_("Add from the network"), "app.add_network_file"),
        )))
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
        self.context_label.set_ellipsize(3)
        self.context_label.add_css_class("caption")
        self.context_label.add_css_class("dim-label")
        title_box.append(self.title_label)
        title_box.append(self.context_label)
        self.header_bar.set_title_widget(title_box)

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
        self.menu_button.set_popover(_action_popover((
            (_("Welcome and quick tour"), "app.welcome"),
            (_("Restore default settings"), "app.restore_settings"),
            (_("About Big Video Converter"), "app.about"),
            (_("Quit"), "app.quit"),
        )))
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

    def _can_start_conversion(self) -> bool:
        return not any(getattr(self.app, name, False) for name in (
            "currently_converting", "active_conversions", "conversions_running",
            "_pending_imports", "_quitting",
        ))

    def set_buttons_sensitive(self, sensitive: bool) -> None:
        sensitive = sensitive and self._can_start_conversion()
        self.add_button.set_sensitive(sensitive)
        self.clear_queue_button.set_sensitive(sensitive)
        self.convert_button.set_sensitive(sensitive)
        self.convert_current_button.set_sensitive(sensitive)

    def update_queue_size(self, count: int) -> None:
        has_files = count > 0 and self.view_name == "queue"
        has_multiple = count > 1 and self.view_name == "queue"
        self.clear_queue_button.set_visible(has_multiple)
        self.convert_button.set_visible(has_files)
        self.convert_button.set_sensitive(has_files and self._can_start_conversion())
        if self.view_name == "queue":
            self.context_label.set_text(
                ngettext("{} video ready", "{} videos ready", count).format(count)
                if count > 0
                else _("Add videos to begin")
            )

    def set_view(self, view_name) -> None:
        self.view_name = view_name
        queue = view_name == "queue"
        can_start = self._can_start_conversion()
        self.add_button.set_visible(queue)
        self.clear_queue_button.set_visible(
            queue and len(getattr(self.app, "conversion_queue", ())) > 1
        )
        self.convert_button.set_visible(
            queue and len(getattr(self.app, "conversion_queue", ())) > 0
        )
        self.convert_current_button.set_visible(not queue)
        self.back_button.set_visible(not queue)
        if queue:
            self.title_label.set_text(_("Your videos"))
            self.update_queue_size(len(getattr(self.app, "conversion_queue", ())))
            self.convert_button.set_sensitive(can_start)
        else:
            self.title_label.set_text(_("Edit video"))
            self.context_label.set_text(
                _("Changes apply only to the selected video")
            )
            self.convert_current_button.set_sensitive(can_start)
