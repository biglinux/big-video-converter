import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

# Setup translation
import gettext

_ = gettext.gettext


class CompletionPage(Gtk.Box):
    """Page displayed when all conversions are completed"""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.completed_files = []

        # Create header bar
        self.header_bar = Adw.HeaderBar()
        self.header_bar.set_title_widget(Gtk.Label(label=_("Conversions Complete")))
        
        # Add the header bar to the top
        self.append(self.header_bar)
        
        # Create content box with spacing
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_spacing(12)
        content_box.set_margin_bottom(24)
        content_box.set_margin_start(24)
        content_box.set_vexpand(False)

        # Create status page for main content
        self.status_page = Adw.StatusPage()
        self.status_page.set_icon_name("emblem-ok-symbolic")
        self.status_page.set_title(_("All Conversions Complete"))
        self.status_page.set_description(
            _("All video conversions have been completed successfully!")
        )

        # Create scrolled window for file list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(200)

        # Create list box for completed files
        self.file_listbox = Gtk.ListBox()
        self.file_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.file_listbox.add_css_class("boxed-list")

        scrolled.set_child(self.file_listbox)

        # Create clamp for the list
        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        clamp.set_child(scrolled)

        # Create button box
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_spacing(12)

        # OK button to return to queue view
        self.ok_button = Gtk.Button(label=_("OK"))
        self.ok_button.add_css_class("pill")
        self.ok_button.add_css_class("suggested-action")
        self.ok_button.set_size_request(120, -1)
        self.ok_button.connect("clicked", self.on_ok_clicked)

        button_box.append(self.ok_button)

        # Pack content
        content_box.append(self.status_page)
        content_box.append(clamp)
        content_box.append(button_box)
        
        # Add content box to main container
        self.append(content_box)

    def set_completed_files(self, files):
        """Set the list of completed files to display"""
        self.completed_files = files

        # Clear existing list
        while True:
            row = self.file_listbox.get_first_child()
            if row is None:
                break
            self.file_listbox.remove(row)

        # Add files to list
        for file_info in files:
            row = Adw.ActionRow()
            
            # Extract file info
            input_file = file_info.get("input_file", "")
            output_file = file_info.get("output_file", "")
            success = file_info.get("success", True)
            
            # Set title and subtitle
            if input_file:
                row.set_title(os.path.basename(input_file))
            
            if output_file:
                row.set_subtitle(f"→ {os.path.basename(output_file)}")
            
            # Add status icon
            if success:
                icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
                icon.add_css_class("success")
            else:
                icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
                icon.add_css_class("error")
            
            row.add_prefix(icon)
            
            self.file_listbox.append(row)



    def on_ok_clicked(self, button):
        """Handle OK button click - return to queue view"""
        self.app.return_to_main_view()
