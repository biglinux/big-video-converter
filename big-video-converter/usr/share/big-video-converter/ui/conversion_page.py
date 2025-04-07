import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, Pango, GLib, Gdk, GObject

from constants import CONVERT_SCRIPT_PATH
from utils.conversion import run_with_progress_dialog
from utils.video_settings import get_video_filter_string

# Setup translation
import gettext

_ = gettext.gettext


class ConversionPage:
    """
    Conversion page UI component.
    Provides interface for selecting and converting video files.
    """

    def __init__(self, app):
        self.app = app
        self.page = self._create_page()

        # Connect settings after UI is created
        self._connect_settings()

        # Show help on startup if enabled (default: True)
        try:
            # Try to load the setting
            show_help_on_startup = self.app.settings_manager.load_setting(
                "show-conversion-help-on-startup", True
            )
            print(
                f"Loaded setting show-conversion-help-on-startup: {show_help_on_startup}"
            )

            # Check if it's explicitly False (not just None or some other falsy value)
            if show_help_on_startup is False:
                print("Help dialog disabled by user setting")
            else:
                # Default behavior is to show dialog
                print("Help dialog will be shown (default or user setting)")
                # Use GLib.idle_add to show the dialog after the UI is fully loaded
                GLib.idle_add(self.on_help_clicked, None)
        except Exception as e:
            # If there's an error loading the setting, log it and default to showing help
            print(f"Error loading dialog setting: {e}")
            print("Defaulting to show help dialog")
            GLib.idle_add(self.on_help_clicked, None)

    def get_page(self):
        """Return the page widget"""
        return self.page

    def _create_page(self):
        # Create page for conversion
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Add ScrolledWindow to enable scrolling when window is small
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        page.append(scrolled_window)

        # Container for scrollable content - use FILL alignment for full height
        scrollable_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrollable_content.set_valign(Gtk.Align.FILL)
        scrollable_content.set_vexpand(True)
        scrolled_window.set_child(scrollable_content)

        # Use Adw.Clamp to constrain content width nicely
        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        clamp.set_tightening_threshold(600)
        clamp.set_vexpand(True)  # Make clamp expand vertically
        scrollable_content.append(clamp)

        # Main content box inside the clamp
        main_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_content.set_spacing(16)
        main_content.set_margin_start(12)
        main_content.set_margin_end(12)
        main_content.set_margin_top(24)
        main_content.set_margin_bottom(24)
        main_content.set_vexpand(True)
        clamp.set_child(main_content)

        # ===== QUEUE SECTION FIRST =====
        # Create a wrapper box that will expand to fill available space
        queue_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        queue_wrapper.set_vexpand(True)
        queue_wrapper.set_valign(Gtk.Align.FILL)
        queue_wrapper.set_hexpand(True)

        # Create help button to be placed in the PreferencesGroup header
        help_button = Gtk.Button()
        help_button.set_icon_name("help-about-symbolic")
        help_button.add_css_class("accent")
        help_button.add_css_class("flat")
        help_button.add_css_class("circular")
        help_button.set_tooltip_text(_("Show help"))
        help_button.connect("clicked", self.on_help_clicked)
        help_button.set_valign(Gtk.Align.CENTER)

        # Create the PreferencesGroup with title and help button as header suffix
        queue_group = Adw.PreferencesGroup(title=_("Conversion Queue"))

        # Set the help button as the header_suffix to position it on the right
        # This is the proper way to add buttons to PreferencesGroup headers in Adwaita
        queue_group.set_header_suffix(help_button)

        queue_group.set_hexpand(True)
        queue_group.set_vexpand(True)
        queue_group.set_valign(Gtk.Align.FILL)

        # Create a queue listbox with a scrolled window
        queue_scroll = Gtk.ScrolledWindow()
        queue_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        queue_scroll.set_vexpand(True)
        queue_scroll.set_hexpand(True)
        queue_scroll.add_css_class("card")

        # Remove fixed size constraints to allow dynamic resizing
        queue_scroll.set_propagate_natural_height(False)
        queue_scroll.set_propagate_natural_width(False)

        # Create a listbox for the queue items
        self.queue_listbox = Gtk.ListBox()
        self.queue_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.queue_listbox.connect("row-activated", self.on_queue_item_activated)
        self.queue_listbox.set_hexpand(True)
        self.queue_listbox.set_vexpand(True)
        self.queue_listbox.set_valign(Gtk.Align.FILL)

        # Add CSS styling for drag and drop
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .dragging {
                opacity: 0.7;
                background-color: alpha(@accent_color, 0.2);
            }
            .drag-hover {
                border-bottom: 2px solid @accent_color;
                background-color: alpha(@accent_color, 0.1);
            }
            .transparent-background {
                background-color: transparent;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.queue_listbox.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.queue_listbox.add_css_class("transparent-background")

        # Single instance of dragged row tracker
        self.dragged_row = None

        # Remove old conflicting controllers if they exist
        self.queue_dragging_enabled = False

        queue_scroll.set_child(self.queue_listbox)

        # Create a content box for the queue to allow flexible layout
        queue_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        queue_content_box.set_vexpand(True)
        queue_content_box.set_hexpand(True)

        # Add the queue scroll window directly to the content box
        queue_content_box.append(queue_scroll)

        # Add the content box to the queue group
        queue_group.add(queue_content_box)

        # Create button box for queue management
        queue_buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        queue_buttons_box.set_halign(Gtk.Align.CENTER)
        queue_buttons_box.set_spacing(12)
        queue_buttons_box.set_margin_top(12)
        queue_buttons_box.set_margin_bottom(12)
        queue_buttons_box.set_vexpand(False)  # Explicitly don't expand

        # Queue management buttons
        clear_queue_button = Gtk.Button(label=_("Clear Queue"))
        clear_queue_button.connect("clicked", self.on_clear_queue_clicked)
        clear_queue_button.add_css_class("pill")
        queue_buttons_box.append(clear_queue_button)

        # Create a proper AdwSplitButton which has integrated button and menu
        self.add_button = Adw.SplitButton(label=_("Add Files"))
        self.add_button.set_tooltip_text(_("Add video files to queue"))
        self.add_button.add_css_class("suggested-action")
        # Don't add pill class here as it won't work properly
        self.add_button.connect("clicked", self.on_add_files_clicked)

        # Add custom CSS to style the SplitButton with rounded corners
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            splitbutton.suggested-action {
                border-radius: 99px;
            }
            
            /* Style for the dropdown button part */
            splitbutton > button:last-child {
                border-top-right-radius: 99px;
                border-bottom-right-radius: 99px;
            }
            
            /* Style for the main button part */
            splitbutton > button:first-child {
                border-top-left-radius: 99px;
                border-bottom-left-radius: 99px;
            }
        """)

        Gtk.StyleContext.add_provider_for_display(
            self.add_button.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Create menu model for the dropdown
        menu = Gio.Menu()
        menu_item = Gio.MenuItem.new(_("Add Folder"), "app.add_folder")
        icon = Gio.ThemedIcon.new("folder-symbolic")
        menu_item.set_icon(icon)
        menu.append_item(menu_item)

        # Set the menu model for the dropdown part
        self.add_button.set_menu_model(menu)

        # Add the split button to the button box
        queue_buttons_box.append(self.add_button)

        # Single convert button that processes the queue
        convert_button = Gtk.Button(label=_("Convert All"))
        convert_button.add_css_class("pill")
        convert_button.add_css_class("suggested-action")
        convert_button.connect("clicked", self.on_convert_clicked)
        self.convert_button = convert_button  # Store reference for enabling/disabling
        queue_buttons_box.append(convert_button)

        # Add the button box directly to the queue group
        queue_button_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        queue_button_container.set_vexpand(False)  # Explicitly don't expand
        queue_button_container.set_halign(Gtk.Align.CENTER)
        queue_button_container.append(queue_buttons_box)
        queue_group.add(queue_button_container)

        # Add the queue group to the wrapper
        queue_wrapper.append(queue_group)

        # Add the wrapper to main content
        main_content.append(queue_wrapper)

        # Create a single-row layout for output folder and delete original
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        options_box.set_spacing(12)
        options_box.set_margin_top(16)
        options_box.set_vexpand(False)  # Ensure this doesn't steal space

        # Create ComboBox for folder options
        folder_options_store = Gtk.StringList()
        folder_options_store.append(_("Save in the same folder as the original file"))
        folder_options_store.append(_("Folder to save"))

        self.folder_combo = Gtk.DropDown()
        self.folder_combo.set_model(folder_options_store)
        self.folder_combo.set_selected(0)  # Default to "Same as input"
        self.folder_combo.set_valign(Gtk.Align.CENTER)
        options_box.append(self.folder_combo)

        # Folder entry box (container that can be shown/hidden)
        self.folder_entry_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.folder_entry_box.set_spacing(4)
        self.folder_entry_box.set_visible(False)  # Initially hidden
        self.folder_entry_box.set_hexpand(True)

        # Output folder entry
        self.output_folder_entry = Gtk.Entry()
        self.output_folder_entry.set_hexpand(True)
        self.output_folder_entry.set_placeholder_text(_("Select folder"))
        self.folder_entry_box.append(self.output_folder_entry)

        # Folder button
        folder_button = Gtk.Button()
        folder_button.set_icon_name("folder-symbolic")
        folder_button.connect("clicked", self.on_folder_button_clicked)
        folder_button.add_css_class("flat")
        folder_button.add_css_class("circular")
        folder_button.set_valign(Gtk.Align.CENTER)
        self.folder_entry_box.append(folder_button)

        options_box.append(self.folder_entry_box)

        # Connect combo box signal
        self.folder_combo.connect("notify::selected", self._on_folder_type_changed)

        # Create a spacer to push delete controls to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        options_box.append(spacer)

        # Delete original checkbox - now aligned to the right
        delete_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        delete_box.set_spacing(8)
        delete_box.set_halign(Gtk.Align.END)

        delete_label = Gtk.Label(label=_("Delete original files"))
        delete_label.set_halign(Gtk.Align.END)
        delete_label.set_valign(Gtk.Align.CENTER)
        delete_box.append(delete_label)

        self.delete_original_check = Gtk.Switch()
        self.delete_original_check.set_valign(Gtk.Align.CENTER)
        delete_box.append(self.delete_original_check)

        options_box.append(delete_box)

        main_content.append(options_box)

        # Update the queue display initially
        self.update_queue_display()

        return page

    def _connect_settings(self):
        """Connect UI elements to settings"""
        settings = self.app.settings_manager

        # Load settings and update UI
        output_folder = settings.load_setting("output-folder", "")
        delete_original = settings.load_setting("delete-original", False)
        use_custom_folder = settings.load_setting("use-custom-output-folder", False)

        # Set folder combo selection and visibility
        self.folder_combo.set_selected(1 if use_custom_folder else 0)
        self.folder_entry_box.set_visible(use_custom_folder)

        # Set output folder path if using custom folder
        self.output_folder_entry.set_text(output_folder)

        # Set delete original switch
        self.delete_original_check.set_active(delete_original)

        # Connect signals
        self.output_folder_entry.connect(
            "changed", lambda w: settings.save_setting("output-folder", w.get_text())
        )

        self.delete_original_check.connect(
            "notify::active",
            lambda w, p: settings.save_setting("delete-original", w.get_active()),
        )

    def on_help_clicked(self, button):
        """Show help information for conversion mode with a switch to control startup behavior"""
        # Create a dialog window properly using Adw.Window
        dialog = Adw.Window()
        dialog.set_default_size(700, 550)
        dialog.set_modal(True)
        dialog.set_transient_for(self.app.window)
        dialog.set_hide_on_close(True)

        # Create content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Add header bar
        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Gtk.Label(label="Big Video Converter"))
        content_box.append(header_bar)

        # Create main box to hold everything with proper layout
        outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer_box.set_vexpand(True)

        # Create scrolled window for content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        # Main content
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.set_margin_start(24)
        main_box.set_margin_end(24)
        main_box.set_margin_top(12)
        main_box.set_spacing(12)

        # Help introduction
        intro_label = Gtk.Label()
        intro_label.set_wrap(True)
        intro_label.set_xalign(0)
        intro_label.set_margin_bottom(16)
        intro_label.set_markup(
            _("A powerful tool for converting video files to MP4 format.")
        )
        main_box.append(intro_label)

        # Features list using bullet points
        features_list = [
            _("• GPU-accelerated conversion for NVIDIA, AMD, and Intel GPUs"),
            _("• High-quality video processing with customizable settings"),
            _("• Support for various video codecs (H.264, H.265/HEVC, AV1, VP9)"),
            _("• Subtitle extraction and embedding"),
            _("• Video preview with trimming and effects"),
        ]

        for feature in features_list:
            feature_label = Gtk.Label()
            feature_label.set_wrap(True)
            feature_label.set_xalign(0)
            feature_label.set_markup(feature)
            feature_label.set_margin_start(12)
            feature_label.set_margin_bottom(4)
            main_box.append(feature_label)

        # Additional information
        info_label = Gtk.Label()
        info_label.set_wrap(True)
        info_label.set_xalign(0)
        info_label.set_margin_top(16)
        info_label.set_markup(
            _(
                "This application uses <b>FFmpeg</b> for reliable, high-performance video conversion. "
                "The GPU acceleration significantly reduces conversion time compared to software-only processing."
            )
        )
        main_box.append(info_label)

        # Add main box to scrolled window
        scrolled.set_child(main_box)
        outer_box.append(scrolled)

        # Create bottom area with fixed height
        bottom_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        bottom_area.set_margin_start(24)
        bottom_area.set_margin_end(24)
        bottom_area.set_margin_top(12)
        bottom_area.set_margin_bottom(12)

        # Add separator above bottom area
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        bottom_area.append(separator)

        # Create a box for controls with spacing
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls_box.set_margin_top(12)
        controls_box.set_margin_bottom(12)

        # Get current setting value
        current_value = self.app.settings_manager.load_setting(
            "show-conversion-help-on-startup", True
        )

        # Create switch with label
        switch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        switch_box.set_hexpand(True)

        switch_label = Gtk.Label(label=_("Show dialog on startup"))
        switch_label.set_halign(Gtk.Align.START)

        show_on_startup_switch = Gtk.Switch()
        show_on_startup_switch.set_active(current_value)
        show_on_startup_switch.set_valign(Gtk.Align.CENTER)

        switch_box.append(switch_label)
        switch_box.append(show_on_startup_switch)
        controls_box.append(switch_box)

        # Add close button
        close_button = Gtk.Button(label=_("Close"))
        close_button.add_css_class("pill")
        close_button.add_css_class("suggested-action")
        close_button.connect("clicked", lambda btn: dialog.close())
        close_button.set_halign(Gtk.Align.END)
        controls_box.append(close_button)

        bottom_area.append(controls_box)
        outer_box.append(bottom_area)

        content_box.append(outer_box)

        # Set content and present dialog
        dialog.set_content(content_box)

        # Connect the switch signal
        show_on_startup_switch.connect("notify::active", self._on_dialog_switch_toggled)

        dialog.present()

    def _on_dialog_switch_toggled(self, switch, param):
        """Handle toggling the switch in the help dialog"""
        try:
            value = switch.get_active()

            # Print debug information
            print(
                f"Attempting to save setting: show-conversion-help-on-startup = {value}"
            )

            # Update setting
            success = self.app.settings_manager.save_setting(
                "show-conversion-help-on-startup", value
            )

            if success:
                print(
                    f"Successfully saved setting: show-conversion-help-on-startup = {value}"
                )
            else:
                print("Warning: Setting may not have been saved properly.")

        except Exception as e:
            # Log the error
            print(f"Error toggling dialog setting: {str(e)}")

            # Fallback approach - try direct save
            try:
                settings_file = os.path.expanduser(
                    "~/.config/big-video-converter/settings.json"
                )
                os.makedirs(os.path.dirname(settings_file), exist_ok=True)

                # Load existing settings if available
                settings = {}
                if os.path.exists(settings_file):
                    with open(settings_file, "r") as f:
                        import json

                        try:
                            settings = json.load(f)
                        except:
                            settings = {}

                # Update the setting
                settings["show-conversion-help-on-startup"] = switch.get_active()

                # Write back to file
                with open(settings_file, "w") as f:
                    import json

                    json.dump(settings, f, indent=2)

                print(f"Saved setting using fallback method to: {settings_file}")
            except Exception as backup_error:
                print(f"Even fallback saving method failed: {str(backup_error)}")

    def on_add_files_clicked(self, button):
        """Open file chooser to add files to the queue"""
        self.app.select_files_for_queue()

    def on_folder_button_clicked(self, button):
        """Open folder chooser dialog to select output folder"""
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select the output folder"))
        dialog.set_initial_folder(
            Gio.File.new_for_path(self.app.last_accessed_directory)
        )
        dialog.select_folder(self.app.window, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result):
        """Handle selected folder from folder chooser"""
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                folder_path = folder.get_path()
                self.output_folder_entry.set_text(folder_path)
                # Save output folder to settings
                self.app.settings_manager.save_setting("output-folder", folder_path)
        except Exception as e:
            print(f"Error selecting folder: {e}")

    def on_convert_clicked(self, button):
        """Start processing the queue"""
        # If queue is empty, show error
        if not self.app.conversion_queue:
            self.app.show_error_dialog(_("Please add files to the queue first."))
            return

        # Set the global delete original setting based on checkbox
        self.app.delete_original_after_conversion = (
            self.delete_original_check.get_active()
        )

        # Set the global output folder setting
        output_folder = self.output_folder_entry.get_text().strip()
        if output_folder:
            self.app.settings_manager.save_setting("output-folder", output_folder)

        # Start queue processing
        self.app.start_queue_processing()

    def on_clear_queue_clicked(self, button):
        """Clear all files from the queue"""
        self.app.clear_queue()

    def on_queue_item_activated(self, listbox, row):
        """Handle selection of a queue item - preview or view details"""
        if row and hasattr(row, "file_path") and row.file_path:
            # Show file details dialog or preview
            file_path = row.file_path
            if os.path.exists(file_path):
                self.app.show_file_details(file_path)

    def update_queue_display(self):
        """Update the queue display with current items"""
        # Clear existing items
        while True:
            row = self.queue_listbox.get_first_child()
            if row:
                self.queue_listbox.remove(row)
            else:
                break

        # Make sure the queue listbox itself has no margins
        self.queue_listbox.set_margin_start(0)
        self.queue_listbox.set_margin_end(0)
        self.queue_listbox.set_margin_top(0)
        self.queue_listbox.set_margin_bottom(0)

        # Add current queue items
        for index, file_path in enumerate(self.app.conversion_queue):
            if not os.path.exists(file_path):
                continue

            # Create list row with full width
            row = Gtk.ListBoxRow()
            row.set_activatable(True)
            row.file_path = file_path
            row.index = index  # Store the index for drag and drop
            row.set_hexpand(True)

            # Simplified drag and drop handling - apply to the listbox instead of individual rows
            # Individual row-level DnD in GTK4 is causing assertion errors

            # 1. NUMBER COLUMN - fixed width
            main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            main_box.set_spacing(12)
            main_box.set_hexpand(True)

            # No vertical margins within items, but keep spacing between items
            main_box.set_margin_top(4)
            main_box.set_margin_bottom(4)
            main_box.set_margin_start(0)
            main_box.set_margin_end(0)

            number_label = Gtk.Label(label=str(index + 1))
            number_label.set_width_chars(2)
            number_label.set_xalign(0.5)
            number_label.set_margin_start(4)  # Small margin for spacing only
            main_box.append(number_label)

            # 2. FILE INFO COLUMN - takes up most space
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            info_box.set_spacing(4)
            info_box.set_hexpand(True)  # This column should expand

            # Filename row with icon
            name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            name_box.set_spacing(4)
            name_box.set_hexpand(True)

            # File type icon
            file_icon = Gtk.Image.new_from_icon_name("video-x-generic")
            file_icon.set_pixel_size(16)
            name_box.append(file_icon)

            # Filename (bold)
            filename = os.path.basename(file_path)
            name_label = Gtk.Label(label=filename)
            name_label.set_hexpand(True)
            name_label.set_halign(Gtk.Align.START)
            name_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            name_label.set_xalign(0)  # Left align
            name_box.append(name_label)

            info_box.append(name_box)

            # Directory path row
            path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            path_box.set_spacing(4)
            path_box.set_hexpand(True)

            # Path prefix icon
            folder_icon = Gtk.Image.new_from_icon_name("folder-symbolic")
            folder_icon.set_pixel_size(12)
            path_box.append(folder_icon)

            # Path label
            directory = os.path.dirname(file_path)
            path_label = Gtk.Label(label=directory)
            path_label.set_hexpand(True)
            path_label.set_halign(Gtk.Align.START)
            path_label.set_ellipsize(Pango.EllipsizeMode.START)
            path_label.set_xalign(0)  # Left align
            path_box.append(path_label)

            info_box.append(path_box)
            main_box.append(info_box)

            # 3. SIZE COLUMN - fixed width
            try:
                file_size = os.path.getsize(file_path) / (1024 * 1024)
                size_label = Gtk.Label(label=f"{file_size:.1f} MB")
                size_label.set_width_chars(8)
                size_label.set_xalign(1)  # Right align
                size_label.set_valign(Gtk.Align.CENTER)
                main_box.append(size_label)
            except:
                # Add a spacer if we can't get the file size
                spacer = Gtk.Box()
                spacer.set_size_request(70, 1)
                main_box.append(spacer)

            # 4. BUTTONS COLUMN - create a proper linked button group
            buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            buttons_box.set_valign(Gtk.Align.CENTER)
            buttons_box.set_margin_end(4)  # Minimal margin

            # Create a linked button box for a cohesive UI
            action_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            action_buttons.add_css_class(
                "linked"
            )  # This makes buttons appear connected
            action_buttons.set_valign(Gtk.Align.CENTER)

            # Info button to show file information
            info_button = Gtk.Button.new_from_icon_name("help-about-symbolic")
            info_button.add_css_class("flat")
            info_button.set_tooltip_text(_("Show file information"))
            info_button.connect(
                "clicked", lambda b, fp=file_path: self.on_show_file_info(b, fp)
            )
            action_buttons.append(info_button)

            # Play button to open in system video player
            play_button = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
            play_button.add_css_class("flat")
            play_button.set_tooltip_text(_("Play in default video player"))
            play_button.connect(
                "clicked", lambda b, fp=file_path: self.on_play_file(b, fp)
            )
            action_buttons.append(play_button)

            # # Edit/Preview button
            # edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            # edit_button.add_css_class("flat")
            # edit_button.set_tooltip_text(_("Preview in editor"))
            # # Use lambda to properly capture the specific file_path in the closure
            # edit_button.connect(
            #     "clicked", lambda b, fp=file_path: self.on_preview_file(b, fp)
            # )
            # action_buttons.append(edit_button)

            # Remove button - with destructive styling
            remove_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            remove_button.add_css_class("flat")
            remove_button.set_tooltip_text(_("Remove from queue"))
            remove_button.connect("clicked", self.on_remove_from_queue, file_path)
            action_buttons.append(remove_button)

            # Add the linked button box to the main buttons container
            buttons_box.append(action_buttons)
            main_box.append(buttons_box)

            # Set the main box as the row's child
            row.set_child(main_box)

            # Add row to listbox with alternating background
            if index % 2 == 1:
                row.add_css_class("alternate-row")
            self.queue_listbox.append(row)

        # Setup drag and drop on the listbox if we have items to reorder
        if len(self.app.conversion_queue) > 1 and not self.queue_dragging_enabled:
            # Remove any existing controllers to avoid duplication
            if hasattr(self, "drag_source") and self.drag_source:
                self.queue_listbox.remove_controller(self.drag_source)
            if hasattr(self, "drop_target") and self.drop_target:
                self.queue_listbox.remove_controller(self.drop_target)

            # Create new drag source controller
            self.drag_source = Gtk.DragSource.new()
            self.drag_source.set_actions(Gdk.DragAction.MOVE)
            self.drag_source.connect("prepare", self.on_drag_prepare_listbox)
            self.drag_source.connect("drag-begin", self.on_drag_begin_listbox)
            self.drag_source.connect("drag-end", self.on_drag_end_listbox)
            self.queue_listbox.add_controller(self.drag_source)

            # Create new drop target controller
            self.drop_target = Gtk.DropTarget.new(
                GObject.TYPE_STRING, Gdk.DragAction.MOVE
            )
            self.drop_target.connect("drop", self.on_drop_listbox)
            self.drop_target.connect("motion", self.on_drag_motion_listbox)
            self.queue_listbox.add_controller(self.drop_target)

            self.queue_dragging_enabled = True

        # Disable drag and drop if we don't need it
        elif len(self.app.conversion_queue) <= 1 and self.queue_dragging_enabled:
            if hasattr(self, "drag_source") and self.drag_source:
                self.queue_listbox.remove_controller(self.drag_source)
                self.drag_source = None
            if hasattr(self, "drop_target") and self.drop_target:
                self.queue_listbox.remove_controller(self.drop_target)
                self.drop_target = None
            self.queue_dragging_enabled = False

        # Show a message if the queue is empty
        if len(self.app.conversion_queue) == 0:
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            empty_box.set_margin_top(24)
            empty_box.set_margin_bottom(24)
            empty_box.set_spacing(12)
            empty_box.set_valign(Gtk.Align.CENTER)
            empty_box.set_hexpand(True)  # Make sure this expands horizontally

            empty_icon = Gtk.Image.new_from_icon_name("folder-open-symbolic")
            empty_icon.set_pixel_size(48)
            empty_icon.add_css_class("dim-label")
            empty_box.append(empty_icon)

            empty_label = Gtk.Label(label=_("Queue is empty. Add files to convert."))
            empty_label.add_css_class("dim-label")
            empty_box.append(empty_label)

            # Add the empty state in a row to ensure consistent layout
            empty_row = Gtk.ListBoxRow()
            empty_row.set_selectable(False)
            empty_row.set_child(empty_box)
            empty_row.set_hexpand(True)

            self.queue_listbox.append(empty_row)

        # Enable or disable convert button based on queue state
        self.convert_button.set_sensitive(len(self.app.conversion_queue) > 0)

    # Unified drag and drop handlers for listbox
    def on_drag_prepare_listbox(self, drag_source, x, y):
        """Prepare data for drag operation from the listbox"""
        row = self.queue_listbox.get_row_at_y(y)
        if row and hasattr(row, "index"):
            # Store the row being dragged for visual feedback
            self.dragged_row = row

            # Return content provider with row index as string
            return Gdk.ContentProvider.new_for_value(str(row.index))
        return None

    def on_drag_begin_listbox(self, drag_source, drag):
        """Handle start of drag operation"""
        if self.dragged_row:
            # Add visual styling
            self.dragged_row.add_css_class("dragging")

    def on_drag_end_listbox(self, drag_source, drag, delete_data):
        """Clean up after drag operation"""
        # Clear dragging state from all rows
        for i in range(len(self.app.conversion_queue)):
            row = self.queue_listbox.get_row_at_index(i)
            if row:
                row.remove_css_class("dragging")
                row.remove_css_class("drag-hover")

        # Clear reference to dragged row
        self.dragged_row = None

    def on_drag_motion_listbox(self, drop_target, x, y):
        """Handle drag motion to show drop target position"""
        # Clear all previous hover highlights
        for i in range(len(self.app.conversion_queue)):
            row = self.queue_listbox.get_row_at_index(i)
            if row:
                row.remove_css_class("drag-hover")

        # Highlight the row under the pointer
        target_row = self.queue_listbox.get_row_at_y(y)
        if target_row and target_row != self.dragged_row:
            target_row.add_css_class("drag-hover")

        return Gdk.DragAction.MOVE

    def on_drop_listbox(self, drop_target, value, x, y):
        """Handle dropping to reorder queue items"""
        try:
            # Get source index from drag data
            source_index = int(value)

            # Get target row
            target_row = self.queue_listbox.get_row_at_y(y)
            if not target_row:
                # If dropped outside any row, assume end of list
                target_index = len(self.app.conversion_queue) - 1
            else:
                target_index = target_row.index

                # If dropping onto self, do nothing
                if source_index == target_index:
                    return False

            # Clear drag styling
            for i in range(len(self.app.conversion_queue)):
                row = self.queue_listbox.get_row_at_index(i)
                if row:
                    row.remove_css_class("dragging")
                    row.remove_css_class("drag-hover")

            # Reorder the queue - properly handle deque.pop() which doesn't take arguments
            # First, get the item to move
            item_to_move = self.app.conversion_queue[source_index]

            # Create a new list from the deque, modify it, then recreate the deque
            queue_list = list(self.app.conversion_queue)
            del queue_list[source_index]
            queue_list.insert(
                target_index if target_index < source_index else target_index - 1,
                item_to_move,
            )

            # Clear and refill the deque
            self.app.conversion_queue.clear()
            self.app.conversion_queue.extend(queue_list)

            # Update the UI
            self.update_queue_display()

            return True
        except Exception as e:
            print(f"Error during drag and drop: {e}")
            import traceback

            traceback.print_exc()
            return False

    def on_preview_file(self, button, file_path):
        """Preview a file in the video editor"""
        # Make sure we're using the specific file path that was clicked
        if file_path and os.path.exists(file_path):
            print(f"Previewing file from button click: {file_path}")
            # Call preview directly without delay
            self.app.show_file_details(file_path)
        else:
            print(f"Error: Invalid file path for preview: {file_path}")
            self.app.show_error_dialog(_("Could not preview this video file"))

    def on_remove_from_queue(self, button, file_path):
        """Remove a specific file from the queue"""
        self.app.remove_from_queue(file_path)
        self.update_queue_display()

    def on_play_file(self, button, file_path):
        """Play file in the default system video player"""
        if file_path and os.path.exists(file_path):
            print(f"Opening file in default video player: {file_path}")
            try:
                # Create a GFile for the file path
                gfile = Gio.File.new_for_path(file_path)

                # Create an AppInfo for the default handler for this file type
                file_type = Gio.content_type_guess(file_path, None)[0]
                app_info = Gio.AppInfo.get_default_for_type(file_type, False)

                if app_info:
                    # Launch the application with the file
                    app_info.launch([gfile], None)
                else:
                    # Fallback using gtk_show
                    Gtk.show_uri(self.app.window, gfile.get_uri(), Gdk.CURRENT_TIME)
            except Exception as e:
                print(f"Error opening file: {e}")
                self.app.show_error_dialog(
                    _("Could not open the video file with the default player")
                )
        else:
            print(f"Error: Invalid file path: {file_path}")
            self.app.show_error_dialog(_("Could not find this video file"))

    def get_selected_file_path(self):
        """Get currently selected file in queue or None"""
        for i in range(len(self.app.conversion_queue)):
            file_path = self.app.conversion_queue[i]
            if os.path.exists(file_path):
                return file_path
        return None

    def set_file(self, file_path):
        """Set the current file path for conversion (required for queue processing)"""
        if file_path and os.path.exists(file_path):
            # Store the current file to be processed
            self.current_file_path = file_path

            # Update output folder ONLY if using "Same as input" option
            if self.folder_combo.get_selected() == 0:  # 0 = "Same folder as original file"
                input_dir = os.path.dirname(file_path)
                self.output_folder_entry.set_text(input_dir)

            # Keep last accessed directory updated
            input_dir = os.path.dirname(file_path)
            self.app.last_accessed_directory = input_dir
            self.app.settings_manager.save_setting("last-accessed-directory", input_dir)
            return True
        return False

    def force_start_conversion(self):
        """Start conversion process with the currently selected file"""
        # Check if we have a file to convert
        if not hasattr(self, "current_file_path") or not os.path.exists(
            self.current_file_path
        ):
            print("Cannot start conversion: No valid file selected")
            return False

        # Get the file to convert
        input_file = self.current_file_path
        print(f"Starting conversion for: {input_file}")

        # Get absolute path to input directory
        input_dir = os.path.dirname(os.path.abspath(input_file))

        # Build environment variables
        env_vars = os.environ.copy()  # Start with current environment

        # Generate up-to-date trim options before starting conversion
        trim_config = self.generate_trim_options()
        trim_start = trim_config["start_time"]
        trim_end = trim_config["end_time"]

        # Load app settings for conversion
        try:
            if hasattr(self.app, "settings_manager"):
                # Get settings directly using string values instead of indices
                env_vars = {}

                # GPU - Use direct string value
                env_vars["gpu"] = self.app.settings_manager.load_setting("gpu", "auto")

                # Video quality and codec
                env_vars["video_quality"] = self.app.settings_manager.load_setting(
                    "video-quality", "medium"
                )
                env_vars["video_encoder"] = self.app.settings_manager.load_setting(
                    "video-codec", "h264"
                )

                # Other encoding settings
                env_vars["preset"] = self.app.settings_manager.load_setting(
                    "preset", "medium"
                )
                env_vars["subtitle_extract"] = self.app.settings_manager.load_setting(
                    "subtitle-extract", "extract"
                )
                env_vars["audio_handling"] = self.app.settings_manager.load_setting(
                    "audio-handling", "copy"
                )

                # Set flags
                if self.app.settings_manager.get_boolean("gpu-partial", False):
                    env_vars["gpu_partial"] = "1"
                if self.app.settings_manager.get_boolean("force-copy-video", False):
                    env_vars["force_copy_video"] = "1"
                if self.app.settings_manager.get_boolean(
                    "only-extract-subtitles", False
                ):
                    env_vars["only_extract_subtitles"] = "1"

                # Handle audio settings
                audio_bitrate = self.app.settings_manager.load_setting(
                    "audio-bitrate", ""
                )
                if audio_bitrate:
                    env_vars["audio_bitrate"] = audio_bitrate
                audio_channels = self.app.settings_manager.load_setting(
                    "audio-channels", ""
                )
                if audio_channels:
                    env_vars["audio_channels"] = audio_channels

                # Get crop values first to check if we need to retrieve video dimensions
                crop_left = self.app.settings_manager.load_setting(
                    "preview-crop-left", 0
                )
                crop_right = self.app.settings_manager.load_setting(
                    "preview-crop-right", 0
                )
                crop_top = self.app.settings_manager.load_setting("preview-crop-top", 0)
                crop_bottom = self.app.settings_manager.load_setting(
                    "preview-crop-bottom", 0
                )

                # Try to get video dimensions if there are crop values
                video_width = getattr(self.app, "video_width", None)
                video_height = getattr(self.app, "video_height", None)

                # If we need to crop and don't have dimensions, try to get them
                if (
                    crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0
                ) and (video_width is None or video_height is None):
                    try:
                        import subprocess
                        import json

                        print(
                            f"Getting video dimensions for {input_file} using ffprobe"
                        )
                        cmd = [
                            "ffprobe",
                            "-v",
                            "error",
                            "-select_streams",
                            "v:0",
                            "-show_entries",
                            "stream=width,height",
                            "-of",
                            "json",
                            input_file,
                        ]

                        result = subprocess.run(cmd, capture_output=True, text=True)
                        if result.returncode == 0:
                            data = json.loads(result.stdout)
                            if "streams" in data and len(data["streams"]) > 0:
                                video_width = int(data["streams"][0].get("width", 0))
                                video_height = int(data["streams"][0].get("height", 0))
                                print(
                                    f"Detected video dimensions: {video_width}x{video_height}"
                                )

                                # Store these dimensions for future use
                                self.app.video_width = video_width
                                self.app.video_height = video_height
                            else:
                                print("No video streams found in file")
                        else:
                            print(f"ffprobe error: {result.stderr}")
                    except Exception as e:
                        print(f"Error getting video dimensions: {e}")
                        import traceback

                        traceback.print_exc()

                # Important: Apply crop values to settings manager so they'll be included in video_filter
                if crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0:
                    print(
                        f"Setting crop values in settings: left={crop_left}, right={crop_right}, top={crop_top}, bottom={crop_bottom}"
                    )

                    # Save crop values to settings
                    self.app.settings_manager.set_int("preview-crop-left", crop_left)
                    self.app.settings_manager.set_int("preview-crop-right", crop_right)
                    self.app.settings_manager.set_int("preview-crop-top", crop_top)
                    self.app.settings_manager.set_int(
                        "preview-crop-bottom", crop_bottom
                    )

                # Get the unified video filter string AFTER setting crop values
                video_filter = get_video_filter_string(
                    self.app.settings_manager,
                    video_width=video_width,
                    video_height=video_height,
                )

                if video_filter:
                    env_vars["video_filter"] = video_filter
                    print(f"Using video_filter: {env_vars['video_filter']}")
                else:
                    print("No video filters applied")

                # Handle additional options
                additional_options = self.app.settings_manager.load_setting(
                    "additional-options", ""
                )

                # Now use the updated trim values from our generate_trim_options method
                if trim_start > 0:
                    # Format time as HH:MM:SS.mmm for FFmpeg
                    start_str = self._format_time_ffmpeg(trim_start)

                    # Add the -ss option to the additional options
                    if additional_options:
                        additional_options += f" -ss {start_str}"
                    else:
                        additional_options = f"-ss {start_str}"

                    print(f"Adding trim start to options: -ss {start_str}")

                if trim_end is not None:
                    # Calculate duration between start and end
                    duration_secs = trim_end - trim_start
                    duration_str = self._format_time_ffmpeg(duration_secs)

                    # Add the -t option to the additional options
                    additional_options += f" -t {duration_str}"
                    print(f"Adding trim duration to options: -t {duration_str}")

                # Set the final options environment variable
                if additional_options:
                    env_vars["options"] = additional_options
                    print(f"Setting options={additional_options}")

                # REMOVED: Separate crop handling - this is now done through the video_filter mechanism
                # We still pass the dimensions to the environment for other potential uses
                if video_width is not None and video_height is not None:
                    env_vars["video_width"] = str(video_width)
                    env_vars["video_height"] = str(video_height)

        except Exception as e:
            print(f"Error setting up conversion environment: {e}")
            import traceback

            traceback.print_exc()

        # Get the extension of the selected output format
        output_ext = self.app.get_selected_format_extension()
        output_format = self.app.get_selected_format_name()

        # Check if input file has the same extension as the selected output format
        input_ext = os.path.splitext(input_file)[1].lower()
        input_basename = os.path.splitext(os.path.basename(input_file))[0]
        input_dir = os.path.dirname(os.path.abspath(input_file))

        if input_ext == output_ext:
            # If input format is the same as output format, add "-converted" to the name
            output_basename = f"{input_basename}-converted{output_ext}"
        else:
            # If format is different, just change the extension
            output_basename = f"{input_basename}{output_ext}"

        # Set output folder based on selection
        use_same_folder = self.folder_combo.get_selected() == 0  # 0 = "Same folder as original file"

        if use_same_folder:
            # Use same folder as input
            output_folder = input_dir
        else:
            # Custom folder selected
            output_folder = self.output_folder_entry.get_text()
            if not output_folder:
                # If custom folder is empty, use input directory
                output_folder = input_dir

        # Ensure output folder path is absolute
        if not os.path.isabs(output_folder):
            output_folder = os.path.abspath(output_folder)

        # IMPORTANT: Create the full output file path
        full_output_path = os.path.join(output_folder, output_basename)

        # Set the full path as output_file
        env_vars["output_file"] = full_output_path

        # Remove output_folder to avoid confusion in the bash script
        if "output_folder" in env_vars:
            del env_vars["output_folder"]

        print(f"Full output path: {full_output_path}")

        # Set the output format
        env_vars["output_format"] = output_format

        # Build the conversion command
        cmd = [CONVERT_SCRIPT_PATH, input_file]

        # Add trim options if applicable
        trim_options = self._get_trim_command_options()
        if trim_options:
            cmd.extend(trim_options)

        # Reset trim times after conversion
        # self.app.set_trim_times(0, None, 0)

        # Delete original setting
        delete_original = self.delete_original_check.get_active()

        # Log the command and environment variables for debugging
        print("\n=== CONVERSION COMMAND ===")
        print(f"Command: {' '.join(cmd)}")
        print("\n=== ENVIRONMENT VARIABLES ===")
        conversion_vars = {
            k: v
            for k, v in env_vars.items()
            if k
            in [
                "gpu",
                "video_quality",
                "video_encoder",
                "preset",
                "subtitle_extract",
                "audio_handling",
                "audio_bitrate",
                "audio_channels",
                "video_resolution",
                "options",
                "gpu_partial",
                "force_copy_video",
                "only_extract_subtitles",
                "video_filter",
                "output_folder",
                "trim_start",
                "trim_end",
                "trim_duration",
                # Add the new crop environment variables
                "crop_x",
                "crop_y",
                "crop_width",
                "crop_height",
                "crop_left",
                "crop_right",
                "crop_top",
                "crop_bottom",
                "video_width",
                "video_height",
            ]
        }

        # Show raw settings value for debugging
        settings_dict = {}
        if hasattr(self.app.settings_manager, "settings"):
            settings_dict = self.app.settings_manager.settings

        if "video-quality" in settings_dict:
            print(f"Raw video-quality setting: {settings_dict['video-quality']}")
        else:
            print("video-quality setting not found in config")

        if "video-codec" in settings_dict:
            print(f"Raw video-codec setting: {settings_dict['video-codec']}")
        else:
            print("video-codec setting not found in config")

        for key, value in conversion_vars.items():
            print(f"{key}={value}")
        print("===========================\n")

        # Create and display progress dialog
        run_with_progress_dialog(
            self.app,
            cmd,
            f"{os.path.basename(input_file)}",
            input_file if delete_original else None,
            delete_original,
            env_vars,
        )

        return True

    def _get_trim_command_options(self):
        """Get ffmpeg command options for trimming based on set trim points"""
        # Get trim times - first try app values, then fall back to settings
        start_time, end_time, duration = self.app.get_trim_times()

        # If we don't have values from the app (video edit page), check settings
        if start_time == 0 and end_time is None:
            # Get trim values from settings
            start_time = self.app.settings_manager.load_setting("video-trim-start", 0.0)
            end_time_setting = self.app.settings_manager.load_setting(
                "video-trim-end", -1.0
            )
            end_time = None if end_time_setting < 0 else end_time_setting
            print(
                f"Using trim settings from settings: start={start_time}, end={end_time}"
            )

        # Always store the trim values as object attributes for force_start_conversion to use
        self.trim_start_time = start_time
        self.trim_end_time = end_time
        self.trim_duration = duration

        # Return empty list since we're using environment variables instead of command-line args
        return []

    def _format_time_ffmpeg(self, seconds):
        """Format time in seconds to HH:MM:SS.mmm format for ffmpeg"""
        hours = int(seconds) // 3600
        minutes = (int(seconds) % 3600) // 60
        seconds_remainder = int(seconds) % 60
        milliseconds = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds_remainder:02d}.{milliseconds:03d}"

    def _on_folder_type_changed(self, combo, param):
        """Handle folder type combo change"""
        selected = combo.get_selected()
        use_custom_folder = selected == 1  # "Custom folder" option is selected

        # Show/hide folder entry when selection changes
        self.folder_entry_box.set_visible(use_custom_folder)

        # Save setting
        self.app.settings_manager.save_setting(
            "use-custom-output-folder", use_custom_folder
        )

        # If not using custom folder, clear the path
        if not use_custom_folder:
            self.app.settings_manager.save_setting("output-folder", "")

    # Row-specific drag and drop handlers
    def on_drag_prepare_row(self, drag_source, x, y, row):
        """Prepare data for dragging a specific row"""
        # Create content with the row index
        content = Gdk.ContentProvider.new_for_value(row.index)
        return content

    def on_drag_begin_row(self, drag_source, drag, row):
        """Handle start of drag operation for a specific row"""
        # Add visual styling to indicate the row is being dragged
        row.add_css_class("dragging")

    def on_drag_end_row(self, drag_source, drag, delete_data, row):
        """Clean up after drag operation completes"""
        # Remove visual styling
        row.remove_css_class("dragging")

    def on_drop_enter(self, drop_target, x, y, row):
        """Handle drag entering a potential drop target"""
        # Add visual styling to indicate possible drop target
        row.add_css_class("drag-hover")
        return Gdk.DragAction.MOVE

    def on_drop_leave(self, drop_target, row):
        """Handle drag leaving a potential drop target"""
        # Remove visual styling
        row.remove_css_class("drag-hover")

    def on_drop_motion_row(self, drop_target, x, y):
        """Handle drag motion over a drop target"""
        return Gdk.DragAction.MOVE

    def on_drop_item(self, drop_target, value, x, y, target_row):
        """Handle dropping item to reorder queue"""
        try:
            # Get the source index from the drag data
            source_index = value
            # Get the target index from the row
            target_index = target_row.index

            # Don't reorder if dropping at the same position
            if source_index == target_index:
                return False

            # Reorder the queue
            item = self.app.conversion_queue.pop(source_index)
            self.app.conversion_queue.insert(target_index, item)

            # Update the UI
            self.update_queue_display()
            return True
        except Exception as e:
            print(f"Error during drop operation: {e}")
            return False

    def on_show_file_info(self, button, file_path):
        """Show detailed information about the video file"""
        if file_path and os.path.exists(file_path):
            from utils.file_info import VideoInfoDialog

            info_dialog = VideoInfoDialog(self.app.window, file_path)
            info_dialog.show()
        else:
            print(f"Error: Invalid file path: {file_path}")
            self.app.show_error_dialog(_("Could not find this video file"))

    def generate_trim_options(self):
        """
        Generate trim options with the most up-to-date values
        and update the object attributes and environment variables.
        Should be called right before conversion starts.
        """
        # Always get the latest trim values directly from the app
        start_time, end_time, duration = self.app.get_trim_times()

        # Get video total duration if available
        video_duration = getattr(self.app, "video_duration", None)

        # If we don't have values from the app, check settings
        if start_time == 0 and end_time is None:
            start_time = self.app.settings_manager.load_setting("video-trim-start", 0.0)
            end_time_setting = self.app.settings_manager.load_setting(
                "video-trim-end", -1.0
            )
            end_time = None if end_time_setting < 0 else end_time_setting
            print(
                f"Using trim settings from settings: start={start_time}, end={end_time}"
            )
        else:
            print(
                f"Using trim settings from app state: start={start_time}, end={end_time}"
            )

        # Validate that end_time is not less than or equal to start_time
        if end_time is not None and end_time <= start_time:
            print("WARNING: Invalid trim values detected (end_time <= start_time)")
            end_time = None
            self.app.settings_manager.save_setting("video-trim-end", -1.0)

        # Always update the object attributes for consistency
        self.trim_start_time = start_time
        self.trim_end_time = end_time
        self.trim_duration = duration

        # Return a dictionary with trim configuration to be used by force_start_conversion
        return {"start_time": start_time, "end_time": end_time, "duration": duration}