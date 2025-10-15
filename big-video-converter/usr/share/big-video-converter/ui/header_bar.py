import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

# Setup translation
import gettext

_ = gettext.gettext


class HeaderBar(Gtk.Box):
    """
    Custom header bar with action buttons for file management and conversion.
    """

    def __init__(self, app, window_buttons_left=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.app = app
        self.window_buttons_left = window_buttons_left

        # Garantir que o Box ocupe toda a largura
        self.set_hexpand(True)

        # Create the header bar
        self.header_bar = Adw.HeaderBar()
        # Garantir que o HeaderBar ocupe toda a largura
        self.header_bar.set_hexpand(True)
        # Configure decoration layout based on window button position
        if window_buttons_left:
            self.header_bar.set_decoration_layout("")
        else:
            self.header_bar.set_decoration_layout("menu:minimize,maximize,close")
        self.append(self.header_bar)

        # Add Back button at the start (hidden by default)
        self.back_button = Gtk.Button()
        # Create box for icon + label
        back_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        back_icon = Gtk.Image.new_from_icon_name("go-previous-symbolic")
        back_label = Gtk.Label(label=_("Back"))
        back_box.append(back_icon)
        back_box.append(back_label)
        self.back_button.set_child(back_box)
        self.app.tooltip_helper.add_tooltip(self.back_button, "back_button")
        self.back_button.connect("clicked", self._on_back_clicked)
        self.back_button.set_visible(False)  # Hidden by default
        self.header_bar.pack_start(self.back_button)

        # Create left controls box for queue info
        left_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        left_controls.set_margin_start(14)
        left_controls.set_halign(Gtk.Align.START)

        # Clear queue icon button (left side)
        self.clear_queue_button = Gtk.Button()
        self.clear_queue_button.set_icon_name("trash-symbolic")
        self.app.tooltip_helper.add_tooltip(
            self.clear_queue_button, "clear_queue_button"
        )
        self.clear_queue_button.add_css_class("circular")
        self.clear_queue_button.add_css_class("destructive-action")
        self.clear_queue_button.connect("clicked", self._on_clear_queue_clicked)
        self.clear_queue_button.set_visible(False)  # Initially hidden
        left_controls.append(self.clear_queue_button)

        # Queue size label (left side)
        self.queue_size_label = Gtk.Label(label=_("0 files"))
        self.queue_size_label.add_css_class("caption")
        self.queue_size_label.add_css_class("dim-label")
        self.queue_size_label.set_visible(False)
        self.queue_size_label.set_margin_start(4)
        self.queue_size_label.set_margin_end(8)
        self.queue_size_label.set_valign(Gtk.Align.CENTER)
        left_controls.append(self.queue_size_label)

        # Pack left controls at the start of the headerbar
        self.header_bar.pack_start(left_controls)

        # Create action buttons container for title area
        self.action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.action_box.set_halign(Gtk.Align.CENTER)
        self.action_box.set_spacing(6)

        # Add Files button (SplitButton for Add Files/Add Folder)
        self.add_button = Adw.SplitButton(label=_("Add Files"))
        self.app.tooltip_helper.add_tooltip(self.add_button, "add_files_button")
        self.add_button.add_css_class("suggested-action")
        self.add_button.connect("clicked", self._on_add_files_clicked)

        # Create menu model for the dropdown
        menu = Gio.Menu()
        menu_item = Gio.MenuItem.new(_("Add Folder"), "app.add_folder")
        icon = Gio.ThemedIcon.new("folder-symbolic")
        menu_item.set_icon(icon)
        menu.append_item(menu_item)
        self.add_button.set_menu_model(menu)

        self.action_box.append(self.add_button)

        # Convert All button
        self.convert_button = Gtk.Button(label=_("Convert All"))
        self.convert_button.add_css_class("suggested-action")
        self.app.tooltip_helper.add_tooltip(self.convert_button, "convert_all_button")
        self.convert_button.set_margin_start(12)
        self.convert_button.connect("clicked", self._on_convert_all_clicked)
        self.convert_button.set_visible(
            False
        )  # Hidden by default, shown when files exist
        self.action_box.append(self.convert_button)

        # Convert This File button (for editor view)
        self.convert_current_button = Gtk.Button(label=_("Convert This File"))
        self.convert_current_button.add_css_class("suggested-action")
        self.app.tooltip_helper.add_tooltip(
            self.convert_current_button, "convert_current_button"
        )
        self.convert_current_button.connect("clicked", self._on_convert_current_clicked)
        self.convert_current_button.set_visible(False)  # Hidden by default
        self.action_box.append(self.convert_current_button)

        # Set action box as title widget
        self.header_bar.set_title_widget(self.action_box)

        # Add menu button (three dots) at the end
        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_icon_name("open-menu-symbolic")
        self.app.tooltip_helper.add_tooltip(self.menu_button, "menu_button")

        # Create menu model
        menu = Gio.Menu.new()
        menu.append(_("Welcome Screen"), "app.welcome")
        menu.append(_("About"), "app.about")
        menu.append(_("Quit"), "app.quit")

        self.menu_button.set_menu_model(menu)

        # Add app icon to right headerbar if window buttons are on left
        if self.window_buttons_left:
            icon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            icon_box.set_halign(Gtk.Align.END)
            icon_box.set_valign(Gtk.Align.CENTER)
            icon_box.append(self.menu_button)
            app_icon = Gtk.Image.new_from_icon_name("big-video-converter")
            app_icon.set_pixel_size(20)
            app_icon.set_halign(Gtk.Align.END)
            app_icon.set_valign(Gtk.Align.CENTER)
            icon_box.append(app_icon)
            self.header_bar.pack_end(icon_box)
        else:
            self.header_bar.pack_end(self.menu_button)

    def _on_add_files_clicked(self, button):
        """Handle Add Files button click"""
        if hasattr(self.app, "select_files_for_queue"):
            self.app.select_files_for_queue()

    def _on_back_clicked(self, button):
        """Handle Back button click"""
        if hasattr(self.app, "show_queue_view"):
            self.app.show_queue_view()

    def _on_clear_queue_clicked(self, button):
        """Handle Clear Queue button click"""
        if hasattr(self.app, "clear_queue"):
            self.app.clear_queue()

    def _on_convert_all_clicked(self, button):
        """Handle Convert All button click"""
        if hasattr(self.app, "start_queue_processing"):
            self.app.start_queue_processing()

    def _on_convert_current_clicked(self, button):
        """Handle Convert This File button click"""
        if hasattr(self.app, "convert_current_file"):
            self.app.convert_current_file()

    def set_buttons_sensitive(self, sensitive):
        """Enable or disable action buttons"""
        self.add_button.set_sensitive(sensitive)
        self.clear_queue_button.set_sensitive(sensitive)
        self.convert_button.set_sensitive(sensitive)

    def update_queue_size(self, count):
        """Update queue size label and show/hide clear button based on file count"""
        # Update label text
        if count == 1:
            text = _("1 file")
        else:
            text = _("{} files").format(count)
        self.queue_size_label.set_text(text)

        # Show clear button and label only when there are 2+ files
        has_multiple_files = count >= 2
        self.clear_queue_button.set_visible(has_multiple_files)
        self.queue_size_label.set_visible(has_multiple_files)

    def set_view(self, view_name):
        """Set the header bar context based on current view
        Args:
            view_name: 'queue' or 'editor'
        """
        if view_name == "queue":
            # Show file management buttons
            self.add_button.set_visible(True)
            self.convert_current_button.set_visible(False)
            # Hide back button
            self.back_button.set_visible(False)
            # Restore button visibility based on queue size
            if hasattr(self, 'app') and hasattr(self.app, 'conversion_queue'):
                queue_count = len(self.app.conversion_queue)
                has_multiple_files = queue_count >= 2
                has_files = queue_count > 0
                self.clear_queue_button.set_visible(has_multiple_files)
                self.queue_size_label.set_visible(has_multiple_files)
                self.convert_button.set_visible(has_files)
            else:
                # Fallback if queue not accessible
                self.convert_button.set_visible(False)
        elif view_name == "editor":
            # Hide file management buttons
            self.add_button.set_visible(False)
            self.clear_queue_button.set_visible(False)
            self.queue_size_label.set_visible(False)
            self.convert_button.set_visible(False)
            # Show editor buttons
            self.convert_current_button.set_visible(True)
            self.back_button.set_visible(True)
