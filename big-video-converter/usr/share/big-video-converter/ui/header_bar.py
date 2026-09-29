"""Actions and queue status for the queue and editor views."""

import gettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GObject, Gtk

_ = gettext.gettext
ngettext = gettext.ngettext

# Actions that change the queue or its settings; shortcuts reach them from
# the progress view too, so they follow the buttons.
QUEUE_ACTIONS = ("add_files", "add_folder", "add_network_file", "clear_queue",
                 "start_conversion", "restore_settings")


class HeaderBar(Gtk.Box):
    """Header with the queue actions centered, as the BigLinux apps do."""

    def __init__(self, app, window_buttons_left=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.app = app
        self.window_buttons_left = window_buttons_left
        self.set_hexpand(True)

        self.header_bar = Adw.HeaderBar()
        self.header_bar.set_hexpand(True)
        self.header_bar.set_decoration_layout(
            "" if window_buttons_left else "menu:minimize,maximize,close"
        )
        self.append(self.header_bar)

        # Only the editor, or a window too narrow for both panes, lets the
        # sidebar go; a wide queue always shows its settings.
        self.view_name = "queue"
        self.sidebar_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.app.split_view.bind_property(
            "show-sidebar", self.sidebar_button, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        self.app.split_view.connect("notify::collapsed", self._sync_sidebar_toggle)
        self.header_bar.pack_start(self.sidebar_button)

        # Labels ellipsize rather than push the header past a narrow window.
        self.back_button = Gtk.Button(child=Adw.ButtonContent(
            icon_name="go-previous-symbolic", label=_("Back"), can_shrink=True))
        self.back_button.connect("clicked", self._on_back_clicked)
        self.back_button.set_visible(False)
        self.header_bar.pack_start(self.back_button)

        left_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        left_controls.set_margin_start(14)
        self.clear_queue_button = Gtk.Button(icon_name="trash-symbolic")
        self.clear_queue_button.set_tooltip_text(_("Clear queue"))
        self.clear_queue_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Clear queue")]
        )
        self.clear_queue_button.add_css_class("circular")
        self.clear_queue_button.add_css_class("destructive-action")
        self.clear_queue_button.connect("clicked", self._on_clear_queue_clicked)
        self.clear_queue_button.set_visible(False)
        left_controls.append(self.clear_queue_button)
        self.queue_size_label = Gtk.Label()
        self.queue_size_label.add_css_class("caption")
        self.queue_size_label.add_css_class("dim-label")
        self.queue_size_label.set_margin_start(4)
        self.queue_size_label.set_margin_end(8)
        self.queue_size_label.set_visible(False)
        left_controls.append(self.queue_size_label)
        self.header_bar.pack_start(left_controls)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        action_box.set_halign(Gtk.Align.CENTER)

        # Neutral: "Convert All" is the one primary action, and an empty
        # queue offers adding files as the primary action of its own page.
        self.add_button = Adw.SplitButton(label=_("Add Files"), can_shrink=True)
        self.add_button.add_css_class("suggested-action")
        self.add_button.connect("clicked", self._on_add_files_clicked)
        add_menu = Gio.Menu()
        for label, action, icon in (
            (_("Add Folder"), "app.add_folder", "folder-symbolic"),
            (_("Add Network File"), "app.add_network_file", "network-server-symbolic"),
        ):
            item = Gio.MenuItem.new(label, action)
            item.set_icon(Gio.ThemedIcon.new(icon))
            add_menu.append_item(item)
        self.add_button.set_menu_model(add_menu)
        action_box.append(self.add_button)

        self.convert_button = Gtk.Button(label=_("Convert All"), can_shrink=True)
        self.convert_button.add_css_class("suggested-action")
        self.convert_button.set_margin_start(12)
        self.convert_button.connect("clicked", self._on_convert_all_clicked)
        self.convert_button.set_visible(False)
        action_box.append(self.convert_button)

        self.convert_current_button = Gtk.Button(label=_("Convert This File"), can_shrink=True)
        self.convert_current_button.add_css_class("suggested-action")
        self.convert_current_button.connect(
            "clicked", self._on_convert_current_clicked
        )
        self.convert_current_button.set_visible(False)
        action_box.append(self.convert_current_button)

        self.header_bar.set_title_widget(action_box)

        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_button.set_tooltip_text(_("Main menu"))
        self.menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Main menu")]
        )
        menu = Gio.Menu()
        menu.append(_("Welcome Screen"), "app.welcome")
        menu.append(_("Restore Settings"), "app.restore_settings")
        menu.append(_("About"), "app.about")
        menu.append(_("Quit"), "app.quit")
        self.menu_button.set_menu_model(menu)

        if window_buttons_left:
            icon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            icon_box.set_valign(Gtk.Align.CENTER)
            icon_box.append(self.menu_button)
            app_icon = Gtk.Image.new_from_icon_name("big-video-converter")
            app_icon.set_pixel_size(20)
            app_icon.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
            icon_box.append(app_icon)
            self.header_bar.pack_end(icon_box)
        else:
            self.header_bar.pack_end(self.menu_button)
        self._sync_sidebar_toggle()

    def _on_add_files_clicked(self, _button):
        self.app.select_files_for_queue()

    def _on_back_clicked(self, _button):
        self.app.show_queue_view()

    def _on_clear_queue_clicked(self, _button):
        self.app.clear_queue()

    def _on_convert_all_clicked(self, button):
        # Disabled at once so a double click cannot start the queue twice.
        button.set_sensitive(False)
        self.app.start_queue_processing()

    def _on_convert_current_clicked(self, button):
        button.set_sensitive(False)
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
        for name in QUEUE_ACTIONS:
            self.app.lookup_action(name).set_enabled(sensitive)

    def update_queue_size(self, count: int) -> None:
        queue = self.add_button.get_visible()
        # Count and clear only matter once there is more than one file.
        self.queue_size_label.set_text(
            ngettext("{count} file", "{count} files", count).format(count=count))
        self.clear_queue_button.set_visible(queue and count > 1)
        self.queue_size_label.set_visible(queue and count > 1)
        self.convert_button.set_visible(queue and count > 0)
        self.convert_button.set_sensitive(count > 0 and self._can_start_conversion())

    def _sync_sidebar_toggle(self, *_args) -> None:
        queue = self.view_name == "queue"
        available = not queue or self.app.split_view.get_collapsed()
        self.sidebar_button.set_visible(available)
        self.app.lookup_action("toggle_sidebar").set_enabled(available)
        # Same msgids as the editor's own toggle (ui/video_edit_ui.py).
        if queue:
            label = _("Show conversion settings")
            tooltip = _("Show conversion settings ({shortcut})").format(shortcut="F9")
        else:
            label = _("Show editing tools")
            tooltip = _("Show editing tools ({shortcut})").format(shortcut="F9")
        self.sidebar_button.set_tooltip_text(tooltip)
        self.sidebar_button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def set_view(self, view_name) -> None:
        queue = view_name == "queue"
        self.view_name = view_name
        self._sync_sidebar_toggle()
        self.back_button.set_visible(not queue)
        self.add_button.set_visible(queue)
        self.convert_current_button.set_visible(not queue)
        self.convert_current_button.set_sensitive(self._can_start_conversion())
        self.update_queue_size(len(getattr(self.app, "conversion_queue", ())))
