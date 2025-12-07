import os
import threading
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, GObject

from constants import CONVERT_SCRIPT_PATH
from utils.conversion import run_with_progress_dialog
from utils.video_settings import get_video_filter_string

# Setup translation
import gettext

_ = gettext.gettext


class FileQueueRow(Adw.ActionRow):
    """Row representing a video file in the queue using Adwaita ActionRow."""

    def __init__(
        self,
        file_path,
        index,
        on_remove_callback,
        on_play_callback,
        on_edit_callback,
        on_info_callback,
        app=None,
    ):
        super().__init__()

        self.file_path = file_path
        self.index = index
        self.on_remove_callback = on_remove_callback
        self.on_play_callback = on_play_callback
        self.on_edit_callback = on_edit_callback
        self.on_info_callback = on_info_callback
        self.app = app

        # Set title to filename (escape special characters for Pango markup)
        filename = os.path.basename(file_path)
        self.set_title(GLib.markup_escape_text(filename))

        # Set subtitle with directory and file size
        try:
            directory = os.path.dirname(file_path)
            file_size = os.path.getsize(file_path) / (1024 * 1024)
            subtitle = f"{directory}  •  {file_size:.1f} MB"
            self.set_subtitle(subtitle)
        except Exception:
            self.set_subtitle(os.path.dirname(file_path))

        # Disable row activation - clicking on the name should not trigger navigation
        self.set_activatable(False)

        # Edit button (added third, appears last)
        edit_button = Gtk.Button.new_from_icon_name('document-edit-symbolic')
        self.app.tooltip_helper.add_tooltip(edit_button, "file_list_edit_button")
        edit_button.add_css_class("flat")
        edit_button.set_valign(Gtk.Align.CENTER)
        edit_button.connect(
            "clicked", lambda btn: self.on_edit_callback(self.file_path)
        )
        self.add_prefix(edit_button)

        # Play button (added second, appears middle)
        play_button = Gtk.Button.new_from_icon_name('media-playback-start-symbolic')
        self.app.tooltip_helper.add_tooltip(play_button, "file_list_play_button")
        play_button.add_css_class("flat")
        play_button.set_valign(Gtk.Align.CENTER)
        play_button.connect(
            "clicked", lambda btn: self.on_play_callback(self.file_path)
        )
        self.add_prefix(play_button)

        # Remove button (added first, appears first)
        remove_button = Gtk.Button.new_from_icon_name('trash-symbolic')
        self.app.tooltip_helper.add_tooltip(remove_button, "file_list_remove_button")
        remove_button.add_css_class("flat")
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.connect(
            "clicked", lambda btn: self.on_remove_callback(self.file_path)
        )
        self.add_prefix(remove_button)

        # Add right-click context menu
        self._setup_context_menu()

        # Connect to realize signal to add tooltip to title widget after it's created
        self.connect("realize", self._on_row_realized)

    def _setup_context_menu(self):
        """Setup right-click context menu for the file row."""
        # Create popup menu
        menu = Gtk.PopoverMenu()
        menu_model = Gio.Menu()

        # Open containing folder action
        menu_model.append(_("Open Containing Folder"), "row.open_folder")

        # More information action
        menu_model.append(_("More Information..."), "row.info")

        # Delete from disk action (destructive)
        menu_model.append(_("Delete from Disk..."), "row.delete_disk")

        menu.set_menu_model(menu_model)
        menu.set_parent(self)

        # Create action group
        action_group = Gio.SimpleActionGroup()

        # Open folder action
        open_folder_action = Gio.SimpleAction.new("open_folder", None)
        open_folder_action.connect("activate", self._on_open_folder)
        action_group.add_action(open_folder_action)

        # Info action
        info_action = Gio.SimpleAction.new("info", None)
        info_action.connect(
            "activate", lambda a, p: self.on_info_callback(self.file_path)
        )
        action_group.add_action(info_action)

        # Delete from disk action
        delete_disk_action = Gio.SimpleAction.new("delete_disk", None)
        delete_disk_action.connect("activate", self._on_delete_from_disk)
        action_group.add_action(delete_disk_action)

        self.insert_action_group("row", action_group)

        # Add right-click gesture
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)  # Right mouse button
        right_click.connect("pressed", lambda g, n, x, y: menu.popup())
        self.add_controller(right_click)

    def _on_row_realized(self, widget):
        """Add tooltip to the title label after the row is realized."""

        # The ActionRow creates internal widgets, we need to find the title label
        # In Adwaita, the title is typically in a Box containing labels
        def find_title_label(widget):
            """Recursively find the title label widget."""
            if isinstance(widget, Gtk.Label):
                # Check if this label's text matches our title
                if widget.get_label() == self.get_title():
                    return widget

            # If widget is a container, check its children
            if hasattr(widget, "get_first_child"):
                child = widget.get_first_child()
                while child:
                    result = find_title_label(child)
                    if result:
                        return result
                    child = child.get_next_sibling()
            return None

        # Find and add tooltip to the title label
        title_label = find_title_label(self)
        if title_label and hasattr(self.app, "tooltip_helper"):
            self.app.tooltip_helper.add_tooltip(title_label, "file_list_item")

    def _on_open_folder(self, action, param):
        """Open the folder containing the file."""
        import subprocess

        if os.path.isfile(self.file_path):
            folder_path = os.path.dirname(self.file_path)
            try:
                # Open file manager at folder location
                subprocess.Popen(["xdg-open", folder_path])
            except Exception as e:
                print(f"Failed to open folder: {e}")

    def _on_delete_from_disk(self, action, param):
        """Show confirmation dialog and delete file from disk."""
        if not os.path.isfile(self.file_path):
            return

        filename = os.path.basename(self.file_path)

        # Create confirmation dialog
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete File from Disk?"))
        dialog.set_body(
            _(
                "Are you sure you want to permanently delete '{}'?\n\nThis action cannot be undone."
            ).format(filename)
        )

        # Add responses
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))

        # Set delete button as destructive
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        # Connect response handler
        dialog.connect("response", self._on_delete_dialog_response)

        # Get window from app
        if self.app and hasattr(self.app, "window"):
            dialog.present(self.app.window)

    def _on_delete_dialog_response(self, dialog, response):
        """Handle delete confirmation dialog response."""
        if response == "delete":
            try:
                # Delete the file from disk
                os.remove(self.file_path)
                print(f"Deleted file from disk: {self.file_path}")

                # Remove from queue
                self.on_remove_callback(self.file_path)
            except Exception as e:
                print(f"Error deleting file: {e}")
                # Show error dialog if app window is available
                if self.app and hasattr(self.app, "window"):
                    error_dialog = Adw.AlertDialog()
                    error_dialog.set_heading(_("Error Deleting File"))
                    error_dialog.set_body(_("Could not delete file: {}").format(str(e)))
                    error_dialog.add_response("ok", _("OK"))
                    error_dialog.present(self.app.window)


class ConversionPage:
    """
    Conversion page UI component.
    Provides interface for selecting and converting video files.
    """

    def __init__(self, app):
        self.app = app

        # Storage for per-file editing metadata
        # Key: file_path, Value: dict with trim, crop, adjustments
        self.file_metadata = {}

        self.page = self._create_page()

        # Connect settings after UI is created
        self._connect_settings()

    def get_page(self):
        """Return the page widget"""
        return self.page

    def _create_page(self):
        # Create page for conversion
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_spacing(16)
        page.set_margin_start(6)
        page.set_margin_end(6)
        page.set_margin_top(12)
        page.set_margin_bottom(12)
        page.set_vexpand(True)

        # ===== QUEUE SECTION FIRST =====
        # Create a queue listbox with a scrolled window
        queue_scroll = Gtk.ScrolledWindow()
        queue_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        queue_scroll.set_vexpand(True)
        queue_scroll.set_min_content_height(300)  # Minimum height for better UX

        # Create a listbox for the queue items
        self.queue_listbox = Gtk.ListBox()
        self.queue_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.queue_listbox.connect("row-activated", self.on_queue_item_activated)
        self.queue_listbox.add_css_class(
            "boxed-list"
        )  # Adwaita style for subtle border

        # Create placeholder for empty queue
        self.placeholder = Adw.StatusPage()
        self.placeholder.set_icon_name('folder-videos-symbolic')
        self.placeholder.set_title(_("No Video Files"))
        self.placeholder.set_description(
            _("Drag files here or use the Add Files button")
        )
        self.placeholder.set_vexpand(True)
        self.placeholder.set_hexpand(True)
        self.queue_listbox.set_placeholder(self.placeholder)

        # Debug - log placeholder state
        print("DEBUG: Placeholder created:")
        print(f"  - visible: {self.placeholder.get_visible()}")
        print(f"  - parent: {self.placeholder.get_parent()}")
        print(f"  - mapped: {self.placeholder.get_mapped()}")
        print(f"  - icon: {self.placeholder.get_icon_name()}")
        print(f"  - title: {self.placeholder.get_title()}")

        # Add idle callback to check after realize
        GLib.idle_add(self._check_placeholder_state)

        queue_scroll.set_child(self.queue_listbox)

        # Single instance of dragged row tracker
        self.dragged_row = None
        self.queue_dragging_enabled = False

        # Add queue to main content
        page.append(queue_scroll)

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
        folder_button.set_icon_name('folder-symbolic')
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

        page.append(options_box)

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

    def _check_placeholder_state(self):
        """Check placeholder state after UI is realized"""
        print("DEBUG: _check_placeholder_state called after idle")
        print(f"  - placeholder visible: {self.placeholder.get_visible()}")
        print(f"  - placeholder parent: {self.placeholder.get_parent()}")
        print(f"  - placeholder mapped: {self.placeholder.get_mapped()}")
        print(
            f"  - listbox has_children: {self.queue_listbox.get_first_child() is not None}"
        )
        return False  # Don't repeat

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

    def on_clear_queue_clicked(self, button):
        """Clear all files from the queue"""
        self.app.clear_queue()

    def on_queue_item_activated(self, listbox, row):
        """Handle selection of a queue item - disabled since rows are not activatable."""
        # Rows are now not activatable, so this should not be called
        # Edit functionality is now through the edit button on each row
        pass

    def update_queue_display(self):
        """Update the queue display with current items"""
        print(
            f"DEBUG: update_queue_display called, queue length: {len(self.app.conversion_queue)}"
        )

        # Clear existing items
        while True:
            row = self.queue_listbox.get_first_child()
            if row:
                self.queue_listbox.remove(row)
            else:
                break

        print(
            f"DEBUG: Cleared listbox, children remaining: {self.queue_listbox.get_first_child()}"
        )

        # Re-set placeholder after clearing (GTK may remove it during clear)
        if hasattr(self, "placeholder"):
            self.queue_listbox.set_placeholder(self.placeholder)
            print("DEBUG: Placeholder re-set after clear")

        # Add current queue items using FileQueueRow
        for index, file_path in enumerate(self.app.conversion_queue):
            if not os.path.exists(file_path):
                continue

            # Create modern ActionRow for the file
            row = FileQueueRow(
                file_path=file_path,
                index=index,
                on_remove_callback=self.on_remove_from_queue_by_path,
                on_play_callback=self.on_play_file_by_path,
                on_edit_callback=self.on_edit_file_by_path,
                on_info_callback=self.on_show_file_info_by_path,
                app=self.app,
            )
            row.file_path = file_path  # Store for drag and drop
            row.index = index

            self.queue_listbox.append(row)

        print(f"DEBUG: Added {len(self.app.conversion_queue)} rows to listbox")
        print(
            f"DEBUG: Listbox has children: {self.queue_listbox.get_first_child() is not None}"
        )

        # Update header button visibility based on queue content
        self._update_header_buttons_visibility()

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

        # Enable or disable convert button based on queue state
        if hasattr(self.app, "header_bar") and hasattr(
            self.app.header_bar, "convert_button"
        ):
            self.app.header_bar.convert_button.set_sensitive(
                len(self.app.conversion_queue) > 0
            )

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

    def on_edit_file(self, button, file_path):
        """Open file in the video editor"""
        if file_path and os.path.exists(file_path):
            self.app.show_editor_for_file(file_path)
        else:
            print(f"Error: Invalid file path for edit: {file_path}")
            self.app.show_error_dialog(_("Could not open this video file"))

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

    def set_file(self, file_path):
        """Set the current file path for conversion (required for queue processing)"""
        if file_path and os.path.exists(file_path):
            # Store the current file to be processed
            self.current_file_path = file_path

            # Update output folder ONLY if using "Same as input" option
            if (
                self.folder_combo.get_selected() == 0
            ):  # 0 = "Same folder as original file"
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
                # Start with a copy of the current environment to preserve PATH, etc.
                env_vars = os.environ.copy()

                # Check if force copy video is enabled
                force_copy_video_enabled = self.app.settings_manager.get_boolean(
                    "force-copy-video", False
                )

                # GPU - Use direct string value, but disable if copying without reencoding
                if force_copy_video_enabled:
                    # When copying without reencoding, hardware acceleration is not needed
                    env_vars["gpu"] = "software"
                    print(
                        "Force copy video enabled: disabling hardware acceleration (not needed for copying)"
                    )
                    # Don't set video encoding parameters when in copy mode
                    print("Force copy video enabled: skipping video_quality, video_encoder, preset")
                else:
                    env_vars["gpu"] = self.app.settings_manager.load_setting(
                        "gpu", "auto"
                    )
                    # Video quality and codec
                    env_vars["video_quality"] = self.app.settings_manager.load_setting(
                        "video-quality", "default"
                    )
                    env_vars["video_encoder"] = self.app.settings_manager.load_setting(
                        "video-codec", "h264"
                    )
                    # Other encoding settings
                    env_vars["preset"] = self.app.settings_manager.load_setting(
                        "preset", "default"
                    )
                
                # Subtitle handling (works regardless of copy mode)
                env_vars["subtitle_extract"] = self.app.settings_manager.load_setting(
                    "subtitle-extract", "embedded"
                )

                # Audio handling - Check if video has audio streams
                audio_handling = self.app.settings_manager.load_setting(
                    "audio-handling", "copy"
                )

                # Import audio detection function
                from utils.file_info import has_audio_streams

                if not has_audio_streams(input_file):
                    # Video has no audio streams, force audio_handling to "none"
                    audio_handling = "none"
                    print(
                        f"No audio streams detected in {os.path.basename(input_file)}, setting audio_handling to 'none'"
                    )

                env_vars["audio_handling"] = audio_handling
                
                # Only set video resolution if NOT in copy mode
                if not force_copy_video_enabled:
                    video_resolution = self.app.settings_manager.load_setting(
                        "video-resolution", ""
                    )
                    if video_resolution:
                        env_vars["video_resolution"] = video_resolution
                else:
                    print("Copy mode enabled - skipping video_resolution")
                
                # Set flags
                if self.app.settings_manager.get_boolean("gpu-partial", False):
                    env_vars["gpu_partial"] = "1"
                if force_copy_video_enabled:
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
                audio_codec = self.app.settings_manager.load_setting(
                    "audio-codec", "aac"
                )
                if audio_codec:
                    env_vars["audio_codec"] = audio_codec

                # Get per-file metadata for this file
                file_metadata = self.file_metadata.get(input_file, {})
                print(f"Using per-file metadata for {os.path.basename(input_file)}")

                # Get trim segments from per-file metadata
                trim_segments = file_metadata.get("trim_segments", [])

                # Get output mode from per-file metadata (not global settings)
                output_mode = file_metadata.get("output_mode", "join")

                # Get crop values from per-file metadata (not global settings)
                crop_left = file_metadata.get("crop_left", 0)
                crop_right = file_metadata.get("crop_right", 0)
                crop_top = file_metadata.get("crop_top", 0)
                crop_bottom = file_metadata.get("crop_bottom", 0)

                # Temporarily set per-file metadata to settings_manager for filter generation
                # (We'll use these instead of global settings for this conversion)
                self.app.settings_manager.save_setting("preview-crop-left", crop_left)
                self.app.settings_manager.save_setting("preview-crop-right", crop_right)
                self.app.settings_manager.save_setting("preview-crop-top", crop_top)
                self.app.settings_manager.save_setting(
                    "preview-crop-bottom", crop_bottom
                )
                self.app.settings_manager.save_setting(
                    "preview-brightness", file_metadata.get("brightness", 0.0)
                )
                self.app.settings_manager.save_setting(
                    "preview-saturation", file_metadata.get("saturation", 1.0)
                )
                self.app.settings_manager.save_setting(
                    "preview-hue", file_metadata.get("hue", 0.0)
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
                print(f"DEBUG: Crop values - left={crop_left}, right={crop_right}, top={crop_top}, bottom={crop_bottom}")
                print(f"DEBUG: Video dimensions - width={video_width}, height={video_height}")
                
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
                # Skip video filters when in copy mode since filters require re-encoding
                if not force_copy_video_enabled:
                    # Pass input file for H.265 10-bit detection
                    video_filter = get_video_filter_string(
                        self.app.settings_manager,
                        video_width=video_width,
                        video_height=video_height,
                        input_file=input_file,
                    )

                    if video_filter:
                        env_vars["video_filter"] = video_filter
                        print(f"Using video_filter: {env_vars['video_filter']}")
                    else:
                        print(
                            "No video filters applied (may be handled by optimized GPU conversion)"
                        )
                else:
                    print("Copy mode enabled - skipping video_filter (filters require re-encoding)")

                # Handle additional options
                additional_options = self.app.settings_manager.load_setting(
                    "additional-options", ""
                )

                # Handle trimming based on number of segments
                if len(trim_segments) == 0:
                    # No trimming, process full video
                    print("No segments defined, processing full video")
                    pass
                elif len(trim_segments) == 1:
                    # Single segment trimming - use the segment's start/end times
                    trim_start = trim_segments[0]["start"]
                    trim_end = trim_segments[0]["end"]

                    if trim_start > 0:
                        start_str = self._format_time_ffmpeg(trim_start)
                        if additional_options:
                            additional_options += f" -ss {start_str}"
                        else:
                            additional_options = f"-ss {start_str}"
                        print(f"Adding trim start to options: -ss {start_str}")

                    if trim_end is not None:
                        duration_secs = trim_end - trim_start
                        duration_str = self._format_time_ffmpeg(duration_secs)
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
        use_same_folder = (
            self.folder_combo.get_selected() == 0
        )  # 0 = "Same folder as original file"

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

        # Check if file exists and find an available filename
        full_output_path = os.path.join(output_folder, output_basename)
        if os.path.exists(full_output_path):
            # Find an available filename by adding a counter
            base_name = os.path.splitext(output_basename)[0]
            extension = os.path.splitext(output_basename)[1]
            counter = 1
            while True:
                output_basename = f"{base_name}_{counter}{extension}"
                full_output_path = os.path.join(output_folder, output_basename)
                if not os.path.exists(full_output_path):
                    print(f"Output file exists, using alternative name: {output_basename}")
                    break
                counter += 1

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

        # Delete original setting
        delete_original = self.delete_original_check.get_active()

        # Check MP4 compatibility when copying without reencoding to MP4
        force_copy_video = env_vars.get("force_copy_video") == "1"
        if force_copy_video and output_format == "mp4":
            from utils.file_info import check_mp4_compatibility

            is_compatible, incompatible_streams = check_mp4_compatibility(input_file)
            if not is_compatible:
                # Show warning dialog
                incompatibility_msg = "\n".join(
                    f"• {issue}" for issue in incompatible_streams
                )

                # Package all needed variables
                conversion_context = {
                    "cmd": cmd,
                    "env_vars": env_vars,
                    "delete_original": delete_original,
                    "full_output_path": full_output_path,
                    "input_file": input_file,
                    "input_basename": input_basename,
                    "input_ext": input_ext,
                    "output_ext": output_ext,
                    "output_folder": output_folder,
                    "trim_segments": trim_segments,
                    "output_mode": output_mode,
                }

                def show_compatibility_warning():
                    dialog = Adw.AlertDialog()
                    dialog.set_heading(_("MP4 Compatibility Warning"))
                    dialog.set_body(
                        _(
                            "The source file contains streams that are not compatible with MP4 container when copying without reencoding:\n\n{}\n\nTo convert this file to MP4, you need to disable 'Copy video without reencoding' option to reencode the incompatible streams."
                        ).format(incompatibility_msg)
                    )
                    dialog.add_response("cancel", _("Cancel"))
                    dialog.add_response("proceed", _("Proceed Anyway"))
                    dialog.set_response_appearance(
                        "proceed", Adw.ResponseAppearance.DESTRUCTIVE
                    )
                    dialog.set_default_response("cancel")
                    dialog.set_close_response("cancel")

                    def on_response(dialog, response):
                        if response == "proceed":
                            # User chose to proceed, continue with conversion
                            GLib.idle_add(self._continue_conversion, conversion_context)

                    dialog.connect("response", on_response)
                    dialog.present(self.app.window)

                # Show dialog in main thread
                GLib.idle_add(show_compatibility_warning)
                return False  # Stop here, dialog will handle continuation

        # Continue with conversion
        conversion_context = {
            "cmd": cmd,
            "env_vars": env_vars,
            "delete_original": delete_original,
            "full_output_path": full_output_path,
            "input_file": input_file,
            "input_basename": input_basename,
            "input_ext": input_ext,
            "output_ext": output_ext,
            "output_folder": output_folder,
            "trim_segments": trim_segments,
            "output_mode": output_mode,
        }
        return self._continue_conversion(conversion_context)

    def _continue_conversion(self, context):
        """Continue with the actual conversion process"""
        # Unpack context
        cmd = context["cmd"]
        env_vars = context["env_vars"]
        delete_original = context["delete_original"]
        full_output_path = context["full_output_path"]
        input_file = context["input_file"]
        input_basename = context["input_basename"]
        input_ext = context["input_ext"]
        output_ext = context["output_ext"]
        output_folder = context["output_folder"]
        trim_segments = context["trim_segments"]
        output_mode = context["output_mode"]

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
                "resolution",
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

        # Handle multi-segment processing
        if len(trim_segments) > 1:
            print(
                f"Multi-segment conversion detected: {len(trim_segments)} segments, mode: {output_mode}"
            )

            # Helper function to process a single segment (shared by split and join modes)
            def process_single_segment(i, segment, output_path, title_prefix="Segment"):
                """Process a single video segment with progress tracking.

                Args:
                    i: Segment index (0-based)
                    segment: Dict with 'start' and 'end' keys
                    output_path: Full path for output file
                    title_prefix: Prefix for progress title (e.g., "Segment" or "Join - Segment")
                """
                # Calculate segment duration
                segment_duration = segment["end"] - segment["start"]
                print(
                    f"Processing segment {i + 1}/{len(trim_segments)}: duration={segment_duration:.2f}s"
                )

                # Create segment-specific env_vars
                segment_env_vars = env_vars.copy()
                segment_env_vars["output_file"] = output_path

                # Add trim options for this segment
                start_str = self._format_time_ffmpeg(segment["start"])
                duration = segment["end"] - segment["start"]
                duration_str = self._format_time_ffmpeg(duration)
                segment_env_vars["options"] = f"-ss {start_str} -t {duration_str}"

                # Build command for this segment
                segment_cmd = [CONVERT_SCRIPT_PATH, input_file]

                # Execute conversion with progress dialog, passing segment duration for accurate progress
                run_with_progress_dialog(
                    self.app,
                    segment_cmd,
                    f"{title_prefix} {i + 1}/{len(trim_segments)}: {os.path.basename(output_path)}",
                    None,  # Don't delete original for individual segments
                    False,
                    segment_env_vars,
                    wait_for_completion=True,  # Process one segment at a time
                    is_segment_batch=True,  # Suppress dialogs for individual segments
                    segment_duration=segment_duration,  # Pass segment duration for progress calculation
                )

            if output_mode == "split":
                # Split mode: create separate file for each segment
                print(f"Split mode: creating {len(trim_segments)} separate files")

                # Define the conversion logic to run in background thread
                def process_segments_in_background():
                    # Track the next available part number across all segments
                    next_part_number = 1

                    for i, segment in enumerate(trim_segments):
                        # Find next available filename to avoid overwriting existing segments
                        while True:
                            segment_output_basename = (
                                f"{input_basename}-part{next_part_number}{output_ext}"
                            )
                            segment_output_path = os.path.join(
                                output_folder, segment_output_basename
                            )

                            # Check if file already exists
                            if not os.path.exists(segment_output_path):
                                break  # Found available filename

                            # File exists, try next number
                            print(
                                f"File exists: {segment_output_basename}, trying next number..."
                            )
                            next_part_number += 1

                        # Use this part number and increment for next segment
                        print(
                            f"Using part number {next_part_number} for segment {i + 1}"
                        )
                        next_part_number += 1

                        print(
                            f"Converting segment {i + 1}/{len(trim_segments)}: {segment_output_basename}"
                        )

                        # Use helper function to process segment
                        process_single_segment(i, segment, segment_output_path)

                    # Handle delete original after all segments are done
                    if delete_original and os.path.exists(input_file):
                        try:
                            os.remove(input_file)
                            print(f"Deleted original file: {input_file}")
                        except Exception as e:
                            print(f"Error deleting original file: {e}")

                    # Notify completion - segment items are auto-removed individually
                    def notify_completion():
                        # Only show notification if not in queue processing
                        is_queue_processing = len(self.app.conversion_queue) > 0
                        if not is_queue_processing:
                            # Send system notification for single file conversions
                            self.app.send_system_notification(
                                _("Conversion Complete"),
                                _("All {0} segments have been processed successfully!").format(len(trim_segments))
                            )
                        # Notify app that the batch conversion is complete
                        self.app.conversion_completed(True)

                    # After all segments are processed, notify completion
                    print(f"All {len(trim_segments)} segments processed successfully")
                    GLib.idle_add(notify_completion)

                # Run the segment processing in a background thread to avoid blocking UI
                conversion_thread = threading.Thread(
                    target=process_segments_in_background, daemon=True
                )
                conversion_thread.start()

                return True

            elif output_mode == "join":
                # Join mode: use same approach as split mode but with temp names, then concatenate
                print(
                    f"Join mode: creating {len(trim_segments)} temporary segments, then joining"
                )

                # Define the conversion logic to run in background thread
                def process_segments_and_join():
                    import subprocess

                    # Define output basename for final joined file
                    if input_ext == output_ext:
                        output_basename = f"{input_basename}-converted{output_ext}"
                    else:
                        output_basename = f"{input_basename}{output_ext}"

                    # Store temp segment paths for concatenation
                    temp_segment_paths = []

                    for i, segment in enumerate(trim_segments):
                        # Create temp filename in destination folder (not /tmp)
                        temp_segment_basename = (
                            f"{input_basename}.segment{i:03d}.tmp{output_ext}"
                        )
                        temp_segment_path = os.path.join(
                            output_folder, temp_segment_basename
                        )
                        temp_segment_paths.append(temp_segment_path)

                        print(
                            f"Extracting segment {i + 1}/{len(trim_segments)} for join: {temp_segment_basename}"
                        )

                        # Use helper function to process segment
                        process_single_segment(
                            i, segment, temp_segment_path, title_prefix="Join - Segment"
                        )

                    # Now concatenate all segments
                    print(f"Concatenating {len(temp_segment_paths)} segments...")

                    # Create concatenation list file in destination folder
                    concat_list_path = os.path.join(
                        output_folder, f"{input_basename}.concat_list.txt"
                    )
                    try:
                        with open(concat_list_path, "w") as f:
                            for temp_path in temp_segment_paths:
                                # Use relative path to avoid issues with special characters
                                f.write(f"file '{os.path.basename(temp_path)}'\n")

                        # Build final output path with collision check
                        final_output_path = os.path.join(output_folder, output_basename)
                        if os.path.exists(final_output_path):
                            # Find an available filename by adding a counter
                            base_name = os.path.splitext(output_basename)[0]
                            extension = os.path.splitext(output_basename)[1]
                            counter = 1
                            while True:
                                output_basename = f"{base_name}_{counter}{extension}"
                                final_output_path = os.path.join(output_folder, output_basename)
                                if not os.path.exists(final_output_path):
                                    print(f"Output file exists, using alternative name: {output_basename}")
                                    break
                                counter += 1

                        # Run ffmpeg concatenation
                        concat_cmd = [
                            "ffmpeg",
                            "-y",
                            "-f",
                            "concat",
                            "-safe",
                            "0",
                            "-i",
                            concat_list_path,
                            "-map",
                            "0:v",
                            "-map",
                            "0:a",
                            "-map",
                            "0:s?",
                            "-c",
                            "copy",
                            final_output_path,
                        ]

                        print(f"Concat command: {' '.join(concat_cmd)}")

                        result = subprocess.run(
                            concat_cmd,
                            cwd=output_folder,
                            capture_output=True,
                            text=True,
                            timeout=300,
                        )

                        if result.returncode == 0:
                            print(
                                f"Successfully joined segments into: {final_output_path}"
                            )

                            # Clean up temp files
                            for temp_path in temp_segment_paths:
                                try:
                                    if os.path.exists(temp_path):
                                        os.remove(temp_path)
                                        print(f"Removed temp segment: {temp_path}")
                                except Exception as e:
                                    print(f"Error removing temp file {temp_path}: {e}")

                            # Remove concat list
                            try:
                                if os.path.exists(concat_list_path):
                                    os.remove(concat_list_path)
                            except Exception as e:
                                print(f"Error removing concat list: {e}")

                            # Handle delete original after successful join
                            if delete_original and os.path.exists(input_file):
                                try:
                                    os.remove(input_file)
                                    print(f"Deleted original file: {input_file}")
                                except Exception as e:
                                    print(f"Error deleting original file: {e}")

                            # Notify completion with notification
                            print("Join operation completed successfully")
                            def notify_join_completion():
                                # Track completed file for completion screen
                                if hasattr(self.app, "current_processing_file") and self.app.current_processing_file:
                                    file_info = {
                                        "input_file": self.app.current_processing_file,
                                        "output_file": final_output_path,
                                        "success": True
                                    }
                                    if not hasattr(self.app, "completed_conversions"):
                                        self.app.completed_conversions = []
                                    self.app.completed_conversions.append(file_info)
                                
                                # Only show notification if not in queue processing
                                is_queue_processing = len(self.app.conversion_queue) > 0
                                if not is_queue_processing:
                                    # Send system notification for single file conversions
                                    self.app.send_system_notification(
                                        _("Conversion Complete"),
                                        _("All {0} segments have been joined successfully!").format(len(trim_segments))
                                    )
                                # Pass skip_tracking=True to avoid duplicate tracking
                                self.app.conversion_completed(True, skip_tracking=True)
                            GLib.idle_add(notify_join_completion)
                        else:
                            error_msg = f"Concatenation failed: {result.stderr}"
                            print(error_msg)

                            def notify_error():
                                self.app.show_error_dialog(error_msg)
                                self.app.conversion_completed(False)

                            GLib.idle_add(notify_error)

                    except Exception as e:
                        error_msg = f"Error during join process: {str(e)}"
                        print(error_msg)
                        import traceback

                        traceback.print_exc()

                        def notify_error():
                            self.app.show_error_dialog(error_msg)
                            self.app.conversion_completed(False)

                        GLib.idle_add(notify_error)

                # Run the segment processing and join in a background thread
                conversion_thread = threading.Thread(
                    target=process_segments_and_join, daemon=True
                )
                conversion_thread.start()

                return True

        # Single segment or no segments - use standard conversion
        # Calculate segment duration for single-segment trimming for accurate progress
        segment_duration = None
        if len(trim_segments) == 1:
            segment_duration = trim_segments[0]["end"] - trim_segments[0]["start"]
            print(f"Single segment mode: segment_duration={segment_duration:.2f}s")
        
        # Create and display progress dialog
        # Always pass input_file for proper queue tracking
        run_with_progress_dialog(
            self.app,
            cmd,
            f"{os.path.basename(input_file)}",
            input_file,  # Always pass full path for queue tracking
            delete_original,
            env_vars,
            segment_duration=segment_duration,  # Pass segment duration for progress calculation
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

    def _filter_subtitle_range(self, srt_content, start_time, end_time, offset_seconds):
        """Filter subtitles within time range and adjust timecodes by offset."""
        import re

        # Helper to convert timecode to seconds
        def time_to_seconds(time_str):
            h, m, s = time_str.split(":")
            s, ms = s.split(",")
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

        # Helper to convert seconds to timecode
        def seconds_to_time(seconds):
            h = int(seconds // 3600)
            seconds %= 3600
            m = int(seconds // 60)
            seconds %= 60
            s = int(seconds)
            ms = int((seconds - s) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        # Split into subtitle blocks
        blocks = srt_content.strip().split("\n\n")
        filtered_blocks = []
        counter = 1

        for block in blocks:
            if not block.strip():
                continue

            lines = block.strip().split("\n")
            if len(lines) < 2:
                continue

            # Find timecode line (usually line 1, but skip subtitle number)
            timecode_line = None
            for line in lines[1:]:
                if "-->" in line:
                    timecode_line = line
                    break

            if not timecode_line:
                continue

            # Parse timecodes
            match = re.match(
                r"(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})",
                timecode_line,
            )
            if not match:
                continue

            sub_start = time_to_seconds(match.group(1))
            sub_end = time_to_seconds(match.group(2))

            # Check if subtitle is within segment range
            if sub_start >= start_time and sub_end <= end_time:
                # Adjust timecodes: subtract segment start, add cumulative offset
                adjusted_start = sub_start - start_time + offset_seconds
                adjusted_end = sub_end - start_time + offset_seconds

                # Build new block with sequential numbering
                text_lines = [
                    line
                    for line in lines
                    if line.strip() and not line.strip().isdigit() and "-->" not in line
                ]
                new_block = f"{counter}\n{seconds_to_time(adjusted_start)} --> {seconds_to_time(adjusted_end)}\n"
                new_block += "\n".join(text_lines)

                filtered_blocks.append(new_block)
                counter += 1

        return "\n\n".join(filtered_blocks)

    def on_show_file_info(self, button, file_path):
        """Show detailed information about the video file"""
        if file_path and os.path.exists(file_path):
            from utils.file_info import VideoInfoDialog

            info_dialog = VideoInfoDialog(self.app.window, file_path)
            info_dialog.show()
        else:
            print(f"Error: Invalid file path: {file_path}")
            self.app.show_error_dialog(_("Could not find this video file"))

    def _update_header_buttons_visibility(self):
        """Update visibility of Clear Queue and Convert All buttons based on queue content"""
        queue_count = len(self.app.conversion_queue)
        has_files = queue_count > 0

        if hasattr(self.app, "header_bar"):
            # Update queue size label and clear button visibility
            if hasattr(self.app.header_bar, "update_queue_size"):
                self.app.header_bar.update_queue_size(queue_count)

            # Update convert button visibility
            if hasattr(self.app.header_bar, "convert_button"):
                self.app.header_bar.convert_button.set_visible(has_files)

    # Wrapper methods for FileQueueRow callbacks (without button parameter)
    def on_remove_from_queue_by_path(self, file_path):
        """Remove file from queue (callback for FileQueueRow)"""
        self.on_remove_from_queue(None, file_path)

    def on_play_file_by_path(self, file_path):
        """Play file (callback for FileQueueRow)"""
        self.on_play_file(None, file_path)

    def on_edit_file_by_path(self, file_path):
        """Edit file (callback for FileQueueRow)"""
        self.on_edit_file(None, file_path)

    def on_show_file_info_by_path(self, file_path):
        """Show file info (callback for FileQueueRow)"""
        self.on_show_file_info(None, file_path)

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
