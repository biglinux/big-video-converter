"""File handler mixin — drag & drop, file dialogs, network mount."""

import gettext
import os

from gi.repository import Gdk, Gio, GLib, Gtk

_ = gettext.gettext


class FileHandlerMixin:
    """Mixin providing file selection, drag-and-drop, and network mount support."""

    def _setup_drag_and_drop(self):
        """Set up drag and drop support for the window"""
        # Handle single files
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("drop", self.on_drop_file)
        self.window.add_controller(drop_target)

        # Handle multiple files
        filelist_drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        filelist_drop_target.connect("drop", self.on_drop_filelist)
        self.window.add_controller(filelist_drop_target)

    # File handling methods
    def is_valid_video_file(self, file_path: str):
        """Check if file has a valid video extension"""
        if not file_path:
            return False

        valid_extensions = [
            ".mp4",
            ".mkv",
            ".webm",
            ".mov",
            ".avi",
            ".wmv",
            ".mpeg",
            ".m4v",
            ".ts",
            ".flv",
        ]

        ext = os.path.splitext(file_path)[1].lower()
        return ext in valid_extensions

    def process_path_recursively(self, path: str):
        """Process a path (file or folder) recursively adding all valid video files to the queue"""
        if not os.path.exists(path):
            self.logger.debug(f"Path does not exist: {path}")
            return 0

        files_added = 0

        if os.path.isfile(path):
            # If it's a single file, just add it if valid
            if self.is_valid_video_file(path):
                if self.add_file_to_queue(path):
                    files_added += 1
        elif os.path.isdir(path):
            # If it's a directory, walk through it recursively
            self.logger.debug(f"Processing directory recursively: {path}")
            for root, dirs, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(root, file)
                    if self.is_valid_video_file(file_path):
                        if self.add_file_to_queue(file_path):
                            files_added += 1

        return files_added

    def on_drop_file(self, drop_target, value: str, x, y):
        """Handle single dropped file or folder"""
        if isinstance(value, Gio.File):
            file_path = value.get_path()
            if file_path and os.path.exists(file_path):
                files_added = self.process_path_recursively(file_path)
                return files_added > 0
        return False

    def on_drop_filelist(self, drop_target, value: str, x, y):
        """Handle multiple dropped files or folders"""
        if isinstance(value, Gdk.FileList):
            files_added = 0
            for file in value.get_files():
                if (
                    file
                    and (file_path := file.get_path())
                    and os.path.exists(file_path)
                ):
                    files_added += self.process_path_recursively(file_path)
            return files_added > 0
        return False

    # File selection methods
    def select_files_for_queue(self) -> None:
        """Open file chooser to select video files for the queue"""
        from constants import VIDEO_FILE_MIME_TYPES

        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Video Files"))
        dialog.set_modal(True)

        if hasattr(self, "last_accessed_directory") and self.last_accessed_directory:
            try:
                initial_folder = Gio.File.new_for_path(self.last_accessed_directory)
                dialog.set_initial_folder(initial_folder)
            except (GLib.Error, OSError) as e:
                self.logger.error(f"Error setting initial folder: {e}")

        filter = Gtk.FileFilter()
        filter.set_name(_("Video Files"))
        for mime_type in VIDEO_FILE_MIME_TYPES:
            filter.add_mime_type(mime_type)

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter)
        dialog.set_filters(filters)

        dialog.open_multiple(self.window, None, self._on_files_selected)

    def select_folder_for_queue(self) -> None:
        """Open folder chooser to select a folder with video files"""
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Folder with Video Files"))
        dialog.set_modal(True)

        if hasattr(self, "last_accessed_directory") and self.last_accessed_directory:
            try:
                initial_folder = Gio.File.new_for_path(self.last_accessed_directory)
                dialog.set_initial_folder(initial_folder)
            except (GLib.Error, OSError) as e:
                self.logger.error(f"Error setting initial folder: {e}")

        dialog.select_folder(self.window, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        """Handle selected folder from folder chooser dialog"""
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                folder_path = folder.get_path()
                if folder_path and os.path.isdir(folder_path):
                    files_added = self.process_path_recursively(folder_path)
                    if files_added > 0:
                        self.last_accessed_directory = folder_path
                        self.settings_manager.save_setting(
                            "last-accessed-directory", self.last_accessed_directory
                        )
                        message = _(
                            "{} video files have been added to the queue."
                        ).format(files_added)
                        GLib.idle_add(
                            lambda msg=message: self.show_info_dialog(
                                _("Files Added"), msg
                            )
                        )
                    else:
                        GLib.idle_add(
                            lambda: self.show_info_dialog(
                                _("No Files Found"),
                                _(
                                    "No valid video files were found in the selected folder."
                                ),
                            )
                        )
                else:
                    GLib.idle_add(
                        lambda: self.show_error_dialog(
                            _("Please select a folder, not a file.")
                        )
                    )
        except (ValueError, KeyError, OSError) as e:
            self.logger.error(f"Error selecting folder: {e}")
            error_msg = str(e)
            GLib.idle_add(
                lambda msg=error_msg: self.show_error_dialog(
                    _("Error selecting folder: {}").format(msg)
                )
            )

    def _on_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
            files_added = 0
            first_file = None

            if files:
                for file in files:
                    file_path = file.get_path()
                    if file_path:
                        if self.add_file_to_queue(file_path):
                            files_added += 1
                            if not first_file:
                                first_file = file_path

                if files_added > 0:
                    self.conversion_page.update_queue_display()
                else:
                    GLib.idle_add(
                        lambda: self.show_info_dialog(
                            _("No Files Added"),
                            _("No valid video files were found in your selection."),
                        )
                    )
        except (GLib.Error, OSError) as error:
            if self.logger:
                self.logger.warning(f"Files not selected: {error}")

    def show_network_file_dialog(self) -> None:
        """Show dialog to add files from network locations (SFTP, SMB, FTP)"""
        from gi.repository import Adw

        dialog = Adw.Dialog()
        dialog.set_title(_("Add Network File"))
        dialog.set_content_width(480)
        dialog.set_content_height(420)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        # Info label
        info_label = Gtk.Label(
            label=_(
                "Connect to a remote server to browse and add video files.\n"
                "Files are accessed directly from the network without downloading."
            )
        )
        info_label.set_wrap(True)
        info_label.set_xalign(0)
        info_label.add_css_class("dim-label")
        content.append(info_label)

        # Protocol group
        protocol_group = Adw.PreferencesGroup()
        protocol_group.set_title(_("Connection"))

        # Protocol ComboRow
        protocol_row = Adw.ComboRow()
        protocol_row.set_title(_("Protocol"))
        protocol_model = Gtk.StringList()
        for p in ["SFTP (SSH)", "SMB (Windows Share)", "FTP"]:
            protocol_model.append(p)
        protocol_row.set_model(protocol_model)
        protocol_group.add(protocol_row)

        # Server entry
        server_row = Adw.EntryRow()
        server_row.set_title(_("Server"))
        server_row.set_text("")
        protocol_group.add(server_row)

        # Port entry
        port_row = Adw.EntryRow()
        port_row.set_title(_("Port"))
        port_row.set_text("")
        protocol_group.add(port_row)

        # Username entry
        user_row = Adw.EntryRow()
        user_row.set_title(_("Username"))
        user_row.set_text("")
        protocol_group.add(user_row)

        # Remote path entry
        path_row = Adw.EntryRow()
        path_row.set_title(_("Remote Path"))
        path_row.set_text("/")
        protocol_group.add(path_row)

        content.append(protocol_group)

        # Status label
        status_label = Gtk.Label(label="")
        status_label.set_wrap(True)
        status_label.set_xalign(0)
        status_label.set_visible(False)
        content.append(status_label)

        # Connect button
        connect_button = Gtk.Button(label=_("Connect and Browse"))
        connect_button.add_css_class("suggested-action")
        connect_button.add_css_class("pill")
        connect_button.set_halign(Gtk.Align.CENTER)
        connect_button.set_margin_top(8)
        connect_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Connect and Browse")],
        )
        content.append(connect_button)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(content)
        scrolled.set_vexpand(True)
        toolbar_view.set_content(scrolled)
        dialog.set_child(toolbar_view)

        def on_connect_clicked(button) -> None:
            server = server_row.get_text().strip()
            if not server:
                status_label.set_text(_("Please enter a server address."))
                status_label.add_css_class("error")
                status_label.set_visible(True)
                return

            # Build URI from fields
            protocol_idx = protocol_row.get_selected()
            schemes = ["sftp", "smb", "ftp"]
            scheme = schemes[protocol_idx]

            user = user_row.get_text().strip()
            port = port_row.get_text().strip()
            remote_path = path_row.get_text().strip() or "/"

            # Build URI
            if user:
                authority = f"{user}@{server}"
            else:
                authority = server
            if port:
                authority += f":{port}"

            # SMB uses smb://server/share format
            if scheme == "smb" and not remote_path.startswith("/"):
                remote_path = "/" + remote_path

            uri = f"{scheme}://{authority}{remote_path}"
            self.logger.debug(f"Mounting network location: {uri}")

            status_label.remove_css_class("error")
            status_label.add_css_class("dim-label")
            status_label.set_text(_("Connecting..."))
            status_label.set_visible(True)
            button.set_sensitive(False)

            # Mount using GIO
            gfile = Gio.File.new_for_uri(uri)
            mount_op = Gtk.MountOperation.new(self.window)

            def on_mount_finished(source, result) -> None:
                try:
                    gfile.mount_enclosing_volume_finish(result)
                except GLib.Error as e:
                    # Already mounted is not an error
                    if "already mounted" not in str(e).lower():
                        error_msg = str(e)
                        self.logger.error(f"Mount error: {e}")
                        GLib.idle_add(
                            lambda: self._handle_mount_error(
                                status_label, button, error_msg
                            )
                        )
                        return

                self.logger.debug(f"Mount successful for {uri}")
                GLib.idle_add(lambda: self._open_network_file_browser(dialog, gfile))

            gfile.mount_enclosing_volume(
                Gio.MountMountFlags.NONE, mount_op, None, on_mount_finished
            )

        connect_button.connect("clicked", on_connect_clicked)
        dialog.present(self.window)

    def _handle_mount_error(self, status_label, button, error_msg):
        """Handle mount error in network dialog"""
        status_label.remove_css_class("dim-label")
        status_label.add_css_class("error")
        status_label.set_text(_("Connection failed: {}").format(error_msg))
        status_label.set_visible(True)
        button.set_sensitive(True)

    def _open_network_file_browser(self, network_dialog, gfile):
        """Open file browser at the mounted network location"""
        from constants import VIDEO_FILE_MIME_TYPES

        network_dialog.close()

        # Resolve GVFS local path
        local_path = gfile.get_path()
        if not local_path:
            # Try to find the GVFS mount point
            try:
                mount = gfile.find_enclosing_mount(None)
                root = mount.get_root()
                local_path = root.get_path()
            except (GLib.Error, OSError) as e:
                self.logger.error(f"Could not resolve GVFS path: {e}")
                self.show_error_dialog(
                    _("Error"),
                    _(
                        "Connected but could not resolve local path. Try browsing via file manager."
                    ),
                )
                return

        self.logger.debug(f"Browsing network files at: {local_path}")

        # Open file dialog at the mounted location
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select Network Video Files"))
        dialog.set_modal(True)

        try:
            initial_folder = Gio.File.new_for_path(local_path)
            dialog.set_initial_folder(initial_folder)
        except (GLib.Error, OSError) as e:
            self.logger.error(f"Error setting initial folder: {e}")

        filter = Gtk.FileFilter()
        filter.set_name(_("Video Files"))
        for mime_type in VIDEO_FILE_MIME_TYPES:
            filter.add_mime_type(mime_type)

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter)
        dialog.set_filters(filters)

        dialog.open_multiple(self.window, None, self._on_files_selected)
