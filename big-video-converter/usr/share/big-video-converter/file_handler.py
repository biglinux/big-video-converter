"""File handler mixin — drag & drop, file dialogs, network mount."""

import gettext
import os
import threading

from gi.repository import Gdk, Gio, GLib, Gtk

from utils.signal_connections import SignalConnections

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

    def add_paths_to_queue(self, paths, on_complete=None, *, selected_files=False):
        """Enumerate storage off-thread, then publish one batch on the GTK thread."""
        if getattr(self, "_quitting", False):
            return
        self._pending_imports = getattr(self, "_pending_imports", 0) + 1
        self.header_bar.set_buttons_sensitive(False)

        def finish(files, error):
            self._pending_imports -= 1
            if getattr(self, "_quitting", False):
                return False
            added = 0
            try:
                with self.settings_manager.batch_update():
                    for file_path in files:
                        added += self.add_file_to_queue(file_path)
                if error is not None:
                    self.show_error_dialog(str(error))
                elif on_complete is not None:
                    on_complete(added)
            except OSError as persistence_error:
                self.show_error_dialog(str(persistence_error))
            finally:
                self.header_bar.set_buttons_sensitive(
                    not self._pending_imports and not self.active_conversions)
            return False

        def scan():
            files = []
            error = None
            try:
                for path in paths:
                    if getattr(self, "_quitting", False):
                        break
                    if os.path.isdir(path):
                        def failed_walk(error):
                            raise error
                        for directory, _dirs, names in os.walk(path, onerror=failed_walk):
                            if getattr(self, "_quitting", False):
                                break
                            files.extend(os.path.join(directory, name) for name in names
                                         if self.is_valid_video_file(name))
                    elif selected_files or self.is_valid_video_file(path):
                        files.append(path)
            except OSError as caught:
                error = caught
            GLib.idle_add(finish, files, error)

        threading.Thread(target=scan, name="bvc-file-scan", daemon=True).start()

    def on_drop_file(self, drop_target, value, x, y):
        if isinstance(value, Gio.File) and (path := value.get_path()):
            self.add_paths_to_queue([path])
            return True
        return False

    def on_drop_filelist(self, drop_target, value, x, y):
        if isinstance(value, Gdk.FileList):
            paths = [file.get_path() for file in value.get_files() if file.get_path()]
            if paths:
                self.add_paths_to_queue(paths)
                return True
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
        try:
            folder = dialog.select_folder_finish(result)
            if folder and (path := folder.get_path()):
                def completed(count):
                    if count:
                        self.show_info_dialog(_("Files Added"),
                            _("{} video files have been added to the queue.").format(count))
                    else:
                        self.show_info_dialog(_("No Files Found"),
                            _("No valid video files were found in the selected folder."))
                self.add_paths_to_queue([path], completed)
        except (GLib.Error, OSError) as error:
            self.logger.debug("Folder not selected: %s", error)

    def _on_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
            if files:
                self.add_paths_to_queue(
                    [file.get_path() for file in files if file.get_path()], selected_files=True)
        except (GLib.Error, OSError) as error:
            self.logger.debug("Files not selected: %s", error)

    def show_network_file_dialog(self) -> None:
        """Show dialog to add files from network locations (SFTP, SMB, FTP)"""
        from gi.repository import Adw

        dialog = Adw.Dialog()
        connections = SignalConnections(dialog)
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

        connections.connect(connect_button, "clicked", on_connect_clicked)
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
