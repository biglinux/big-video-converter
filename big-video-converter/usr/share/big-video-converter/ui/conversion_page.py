import os
from copy import deepcopy
import threading
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Setup translation
import gettext

from constants import CONVERT_SCRIPT_PATH
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk
from utils.conversion import run_with_progress_dialog
from utils.segment_batch import start_segment_batch
from utils.ffmpeg_options import validate_additional_options
from utils.job_options import (
    RESOLUTION_MODES,
    default_metadata,
    normalize_metadata,
    snapshot_preset,
    summary,
)
from utils.thumbnail_cache import ThumbnailManager
from utils.video_settings import SettingsOverride, get_video_filter_string

import logging

logger = logging.getLogger(__name__)

_ = gettext.gettext


class FileQueueRow(Gtk.ListBoxRow):
    """Premium queue card with preview, effective settings and focused actions."""

    def __init__(
        self,
        file_path,
        index,
        on_remove_callback,
        on_play_callback,
        on_edit_callback,
        on_info_callback,
        on_options_callback,
        metadata,
        thumbnail_manager,
        app=None,
    ):
        super().__init__()
        self.file_path = file_path
        self.index = index
        self.on_remove_callback = on_remove_callback
        self.on_play_callback = on_play_callback
        self.on_edit_callback = on_edit_callback
        self.on_info_callback = on_info_callback
        self.on_options_callback = on_options_callback
        self.metadata = normalize_metadata(metadata)
        self.thumbnail_manager = thumbnail_manager
        self.app = app
        self._thumbnail_request = None
        self._thumbnail_disposed = False

        self.set_activatable(True)
        self.set_selectable(False)
        self.add_css_class("bvc-queue-card")
        self.set_tooltip_text(_("Open this video in the editor"))

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        content.set_margin_top(2)
        content.set_margin_bottom(2)
        self.set_child(content)

        preview_frame = Gtk.Box()
        preview_frame.add_css_class("bvc-thumbnail-frame")
        preview_frame.set_valign(Gtk.Align.CENTER)
        self.thumbnail_stack = Gtk.Stack()
        self.thumbnail_stack.set_size_request(144, 82)
        self.thumbnail_stack.set_hhomogeneous(True)
        self.thumbnail_stack.set_vhomogeneous(True)
        placeholder = Gtk.Image.new_from_icon_name("video-x-generic-symbolic")
        placeholder.set_pixel_size(42)
        placeholder.add_css_class("dim-label")
        self.thumbnail_picture = Gtk.Picture()
        self.thumbnail_picture.set_can_shrink(True)
        if hasattr(self.thumbnail_picture, "set_content_fit"):
            self.thumbnail_picture.set_content_fit(Gtk.ContentFit.COVER)
        self.thumbnail_stack.add_named(placeholder, "placeholder")
        self.thumbnail_stack.add_named(self.thumbnail_picture, "picture")
        self.thumbnail_stack.set_visible_child_name("placeholder")
        preview_frame.append(self.thumbnail_stack)
        content.append(preview_frame)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        details.set_hexpand(True)
        details.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label=os.path.basename(file_path))
        title.set_xalign(0)
        title.set_ellipsize(3)
        title.set_tooltip_text(file_path)
        title.add_css_class("title-3")
        details.append(title)

        directory = Gtk.Label(label=os.path.dirname(file_path))
        directory.set_xalign(0)
        directory.set_ellipsize(3)
        directory.add_css_class("caption")
        directory.add_css_class("dim-label")
        details.append(directory)

        metadata_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        profile = Gtk.Label(label=_(summary(self.metadata)))
        profile.add_css_class("bvc-profile-chip")
        profile.set_valign(Gtk.Align.CENTER)
        metadata_row.append(profile)
        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            size = Gtk.Label(label=f"{size_mb:.1f} MB")
            size.add_css_class("caption")
            size.add_css_class("dim-label")
            size.set_valign(Gtk.Align.CENTER)
            metadata_row.append(size)
        except OSError:
            pass
        details.append(metadata_row)
        content.append(details)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_valign(Gtk.Align.CENTER)

        options_button = Gtk.Button()
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        options_box.append(Gtk.Image.new_from_icon_name("preferences-system-symbolic"))
        options_box.append(Gtk.Label(label=_("Options")))
        options_button.set_child(options_box)
        options_button.add_css_class("bvc-secondary")
        options_button.set_tooltip_text(_("Choose a preset or resolution for this video"))
        options_button.connect(
            "clicked", lambda _button: self.on_options_callback(self.file_path)
        )
        actions.append(options_button)

        edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        edit_button.add_css_class("bvc-icon-button")
        edit_button.add_css_class("bvc-quiet")
        edit_button.set_tooltip_text(_("Edit video"))
        edit_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Edit video")]
        )
        edit_button.connect(
            "clicked", lambda _button: self.on_edit_callback(self.file_path)
        )
        actions.append(edit_button)

        more_button = Gtk.MenuButton(icon_name="view-more-symbolic")
        more_button.add_css_class("bvc-icon-button")
        more_button.add_css_class("bvc-quiet")
        more_button.set_tooltip_text(_("More actions"))
        more_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("More actions")]
        )
        more_button.set_popover(self._create_more_popover())
        actions.append(more_button)
        content.append(actions)

        self._request_thumbnail()

    def _menu_action(self, label, icon_name, callback, *, destructive=False):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("bvc-quiet")
        if destructive:
            button.add_css_class("bvc-danger")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.append(Gtk.Image.new_from_icon_name(icon_name))
        text = Gtk.Label(label=label)
        text.set_xalign(0)
        text.set_hexpand(True)
        row.append(text)
        button.set_child(row)
        button.connect("clicked", callback)
        return button

    def _create_more_popover(self):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.append(self._menu_action(
            _("Play video"), "media-playback-start-symbolic",
            lambda _b: self.on_play_callback(self.file_path),
        ))
        box.append(self._menu_action(
            _("Video information"), "dialog-information-symbolic",
            lambda _b: self.on_info_callback(self.file_path),
        ))
        box.append(self._menu_action(
            _("Open containing folder"), "folder-open-symbolic",
            lambda _b: self._on_open_folder(),
        ))
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(4)
        separator.set_margin_bottom(4)
        box.append(separator)
        box.append(self._menu_action(
            _("Remove from queue"), "list-remove-symbolic",
            lambda _b: self.on_remove_callback(self.file_path),
            destructive=True,
        ))
        box.append(self._menu_action(
            _("Delete from disk…"), "user-trash-symbolic",
            lambda _b: self._on_delete_from_disk(),
            destructive=True,
        ))
        popover.set_child(box)
        return popover

    def _request_thumbnail(self):
        row_ref = weakref.ref(self)
        expected = self.file_path

        def ready(thumbnail_path):
            row = row_ref()
            if row is not None:
                GLib.idle_add(row._apply_thumbnail, expected, thumbnail_path)

        self._thumbnail_request = self.thumbnail_manager.request(
            self.file_path, ready
        )

    def _apply_thumbnail(self, expected, thumbnail_path):
        if (
            self._thumbnail_disposed
            or expected != self.file_path
            or not thumbnail_path
            or not os.path.isfile(thumbnail_path)
        ):
            return GLib.SOURCE_REMOVE
        self.thumbnail_picture.set_filename(thumbnail_path)
        self.thumbnail_stack.set_visible_child_name("picture")
        return GLib.SOURCE_REMOVE

    def dispose_thumbnail(self):
        if self._thumbnail_disposed:
            return
        self._thumbnail_disposed = True
        request, self._thumbnail_request = self._thumbnail_request, None
        if request is not None:
            request.cancel()
        self.thumbnail_picture.set_paintable(None)

    def _on_open_folder(self):
        if not os.path.isfile(self.file_path):
            return
        try:
            Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(os.path.dirname(self.file_path)).get_uri(),
                None,
            )
        except GLib.Error as error:
            logger.error("Could not open containing folder: %s", error)

    def _on_delete_from_disk(self):
        if not os.path.isfile(self.file_path):
            return
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete this video permanently?"))
        dialog.set_body(
            _("“{}” will be removed from disk. This cannot be undone.").format(
                os.path.basename(self.file_path)
            )
        )
        dialog.add_response("cancel", _("Keep video"))
        dialog.add_response("delete", _("Delete permanently"))
        dialog.set_response_appearance(
            "delete", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, response_id):
            if response_id != "delete":
                return
            try:
                os.remove(self.file_path)
                self.on_remove_callback(self.file_path)
            except OSError as error:
                self.app.show_error_dialog(
                    _("Could not delete the video: {} ").format(error)
                )

        dialog.connect("response", response)
        dialog.present(self.app.window)


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
        self.thumbnail_manager = ThumbnailManager(max_workers=2)
        self._thumbnail_finalizer = weakref.finalize(
            self, ThumbnailManager.shutdown, self.thumbnail_manager
        )

        self.page = self._create_page()

        # Connect settings after UI is created
        self._connect_settings()

    def get_page(self):
        """Return the page widget"""
        return self.page

    def _create_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_vexpand(True)
        page.add_css_class("bvc-queue-page")

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        main.set_margin_start(22)
        main.set_margin_end(22)
        main.set_margin_top(18)
        main.set_margin_bottom(12)
        main.set_vexpand(True)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        hero.add_css_class("bvc-hero")
        eyebrow = Gtk.Label(label=_("CONVERSION QUEUE"))
        eyebrow.set_xalign(0)
        eyebrow.add_css_class("bvc-eyebrow")
        hero.append(eyebrow)
        title = Gtk.Label(label=_("Prepare every video with confidence"))
        title.set_xalign(0)
        title.set_wrap(True)
        title.add_css_class("bvc-page-title")
        hero.append(title)
        description = Gtk.Label(
            label=_(
                "Use one simple recipe for the whole queue, then customize only "
                "the videos that need a different profile or resolution."
            )
        )
        description.set_xalign(0)
        description.set_wrap(True)
        description.add_css_class("bvc-subtle")
        hero.append(description)
        main.append(hero)

        queue_scroll = Gtk.ScrolledWindow()
        queue_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        queue_scroll.set_vexpand(True)
        queue_scroll.set_min_content_height(330)

        clamp = Adw.Clamp(maximum_size=1180, tightening_threshold=760)
        self.queue_listbox = Gtk.ListBox()
        self.queue_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.queue_listbox.set_show_separators(False)
        self.queue_listbox.set_vexpand(True)
        self.queue_listbox.add_css_class("bvc-queue-list")
        self.queue_listbox.connect("row-activated", self.on_queue_item_activated)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        empty.add_css_class("bvc-empty-state")
        empty.set_halign(Gtk.Align.FILL)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_vexpand(True)
        icon_frame = Gtk.Box()
        icon_frame.add_css_class("bvc-empty-icon")
        icon_frame.set_halign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("folder-videos-symbolic")
        icon.set_pixel_size(44)
        icon_frame.append(icon)
        empty.append(icon_frame)
        empty_title = Gtk.Label(label=_("Start with the videos you want to convert"))
        empty_title.add_css_class("title-1")
        empty_title.set_wrap(True)
        empty_title.set_justify(Gtk.Justification.CENTER)
        empty.append(empty_title)
        empty_text = Gtk.Label(
            label=_(
                "Drop files here, choose videos, or add a whole folder. "
                "Nothing is changed until you start the conversion."
            )
        )
        empty_text.add_css_class("bvc-subtle")
        empty_text.set_wrap(True)
        empty_text.set_justify(Gtk.Justification.CENTER)
        empty_text.set_max_width_chars(54)
        empty.append(empty_text)
        empty_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        empty_actions.set_halign(Gtk.Align.CENTER)
        choose = Gtk.Button(label=_("Choose videos"))
        choose.add_css_class("suggested-action")
        choose.add_css_class("bvc-primary")
        choose.connect("clicked", lambda _b: self.app.select_files_for_queue())
        empty_actions.append(choose)
        folder = Gtk.Button(label=_("Add a folder"))
        folder.add_css_class("bvc-secondary")
        folder.connect("clicked", lambda _b: self.app.select_folder_for_queue())
        empty_actions.append(folder)
        empty.append(empty_actions)
        self.placeholder = empty
        self.queue_listbox.set_placeholder(empty)
        clamp.set_child(self.queue_listbox)
        queue_scroll.set_child(clamp)
        main.append(queue_scroll)
        page.append(main)

        self.dragged_row = None
        self.queue_dragging_enabled = False

        tray = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        tray.add_css_class("bvc-action-tray")

        save_label = Gtk.Label(label=_("Save converted videos"))
        save_label.add_css_class("bvc-body-strong")
        save_label.set_valign(Gtk.Align.CENTER)
        tray.append(save_label)

        folder_options_store = Gtk.StringList.new([
            _("Next to each original"),
            _("In one folder"),
        ])
        self.folder_combo = Gtk.DropDown(model=folder_options_store)
        self.folder_combo.set_selected(0)
        self.folder_combo.set_valign(Gtk.Align.CENTER)
        self.folder_combo.connect("notify::selected", self._on_folder_type_changed)
        tray.append(self.folder_combo)

        self.folder_entry_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self.folder_entry_box.set_visible(False)
        self.folder_entry_box.set_hexpand(True)
        self.output_folder_entry = Gtk.Entry()
        self.output_folder_entry.set_hexpand(True)
        self.output_folder_entry.set_placeholder_text(_("Choose an output folder"))
        self.folder_entry_box.append(self.output_folder_entry)
        choose_folder = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        choose_folder.add_css_class("bvc-icon-button")
        choose_folder.set_tooltip_text(_("Choose output folder"))
        choose_folder.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Choose output folder")]
        )
        choose_folder.connect("clicked", self.on_folder_button_clicked)
        self.folder_entry_box.append(choose_folder)
        tray.append(self.folder_entry_box)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        tray.append(spacer)

        delete_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        delete_box.set_valign(Gtk.Align.CENTER)
        delete_label = Gtk.Label(label=_("Delete originals after verification"))
        delete_label.set_tooltip_text(
            _("Originals are kept unless every converted result passes validation")
        )
        delete_box.append(delete_label)
        self.delete_original_check = Gtk.Switch()
        self.delete_original_check.set_valign(Gtk.Align.CENTER)
        delete_box.append(self.delete_original_check)
        tray.append(delete_box)
        page.append(tray)

        self.update_queue_display()
        return page

    def _connect_settings(self):
        """Connect UI elements to settings"""
        settings = self.app.settings_manager

        # Load settings and update UI
        output_folder = settings.load_setting("output-folder", "")
        delete_original = settings.load_setting("delete-original", False)
        # Check the stored dict directly: a missing key must be told apart from
        # a stored False, which load_setting() cannot do.
        if "use-custom-output-folder" in getattr(settings, "settings", {}):
            use_custom_folder = settings.load_setting("use-custom-output-folder", False)
        else:
            # Settings written by an older version: infer the mode from the
            # folder itself, otherwise a configured destination silently falls
            # back to "same folder as the original file".
            use_custom_folder = bool(output_folder and os.path.isdir(output_folder))
            settings.save_setting("use-custom-output-folder", use_custom_folder)
            logger.debug(
                f"Inferred output folder mode from the saved path: custom={use_custom_folder}"
            )

        # Set folder combo selection and visibility
        self.folder_combo.set_selected(1 if use_custom_folder else 0)
        self.folder_entry_box.set_visible(use_custom_folder)

        # Set output folder path if using custom folder
        self.output_folder_entry.set_text(output_folder)

        # Connect signals
        # Only persist what the user typed: set_file() also writes into this
        # entry to display the input folder, and that must not overwrite the
        # destination the user configured.
        self.output_folder_entry.connect("changed", self._on_output_folder_entry_changed)

        # Set delete original switch
        self.delete_original_check.set_active(delete_original)

        self.delete_original_check.connect(
            "notify::active",
            lambda w, p: settings.save_setting("delete-original", w.get_active()),
        )

    def on_folder_button_clicked(self, button) -> None:
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
        except (ValueError, KeyError, OSError) as e:
            logger.error(f"Error selecting folder: {e}")

    def on_queue_item_activated(self, listbox, row) -> None:
        """Open the editor when the user activates a queue card."""
        if row and getattr(row, "file_path", None):
            self.on_edit_file_by_path(row.file_path)

    def update_queue_display(self) -> None:
        """Update the queue display with current items"""
        logger.debug(
            f"DEBUG: update_queue_display called, queue length: {len(self.app.conversion_queue)}"
        )

        # Clear existing items
        while True:
            row = self.queue_listbox.get_first_child()
            if row:
                if hasattr(row, "dispose_thumbnail"):
                    row.dispose_thumbnail()
                self.queue_listbox.remove(row)
            else:
                break

        # Re-set placeholder after clearing (GTK may remove it during clear)
        if hasattr(self, "placeholder"):
            self.queue_listbox.set_placeholder(self.placeholder)

        # Add current queue items using FileQueueRow
        for index, file_path in enumerate(self.app.conversion_queue):
            if not os.path.exists(file_path):
                continue

            self.file_metadata[file_path] = normalize_metadata(
                self.file_metadata.get(file_path)
            )

            # Create modern ActionRow for the file
            row = FileQueueRow(
                file_path=file_path,
                index=index,
                on_remove_callback=self.on_remove_from_queue_by_path,
                on_play_callback=self.on_play_file_by_path,
                on_edit_callback=self.on_edit_file_by_path,
                on_info_callback=self.on_show_file_info_by_path,
                on_options_callback=self.on_file_options_by_path,
                metadata=self.file_metadata[file_path],
                thumbnail_manager=self.thumbnail_manager,
                app=self.app,
            )
            row.file_path = file_path  # Store for drag and drop
            row.index = index

            self.queue_listbox.append(row)

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

    def on_drag_begin_listbox(self, drag_source, drag) -> None:
        """Handle start of drag operation"""
        if self.dragged_row:
            # Add visual styling
            self.dragged_row.add_css_class("dragging")

    def on_drag_end_listbox(self, drag_source, drag, delete_data) -> None:
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

    def on_drop_listbox(self, drop_target, value: str, x, y) -> bool:
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
        except (ValueError, TypeError) as e:
            logger.error(f"Error during drag and drop: {e}")
            import traceback

            traceback.print_exc()
            return False

    def on_edit_file(self, button, file_path: str) -> None:
        """Open file in the video editor"""
        if file_path and os.path.exists(file_path):
            self.app.show_editor_for_file(file_path)
        else:
            logger.error(f"Error: Invalid file path for edit: {file_path}")
            self.app.show_error_dialog(_("Could not open this video file"))

    def on_remove_from_queue(self, button, file_path: str) -> None:
        """Remove a specific file from the queue"""
        self.app.remove_from_queue(file_path)
        self.update_queue_display()

    def on_play_file(self, button, file_path: str) -> None:
        """Play file in the default system video player"""
        if file_path and os.path.exists(file_path):
            logger.debug(f"Opening file in default video player: {file_path}")
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
            except (GLib.Error, OSError) as e:
                logger.error(f"Error opening file: {e}")
                self.app.show_error_dialog(
                    _("Could not open the video file with the default player")
                )
        else:
            logger.error(f"Error: Invalid file path: {file_path}")
            self.app.show_error_dialog(_("Could not find this video file"))

    def set_file(self, file_path: str) -> bool:
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

    def _encoding_environment(self, gpu_override=None):
        """Hardware and encoder settings for a job that re-encodes video.

        Copy mode needs none of this, but the dialog that offers to re-encode
        instead of copying does, and it must land on the accelerator the user
        actually chose rather than falling back to the processor.
        """
        settings = self.app.settings_manager
        env = {}

        if gpu_override:
            # Use override settings for parallel processing
            if "type" in gpu_override:
                env["gpu"] = gpu_override["type"]
            if "device" in gpu_override:
                env["gpu_device"] = gpu_override["device"]
            logger.debug(f"Using GPU override: {gpu_override}")
        else:
            gpu_setting = settings.load_setting("gpu", "auto")
            env["gpu"] = gpu_setting

            # GPU device selection (render device path)
            gpu_device_index = settings.load_setting("gpu-device-index", 0)

            # Auto-detect architecture from device if GPU is Auto but Device is
            # specific. This fixes selecting an "Intel" device with "Auto" mode.
            if gpu_device_index > 0 and hasattr(self.app, "detected_gpus"):
                idx = gpu_device_index - 1  # 0 = Auto
                if idx < len(self.app.detected_gpus):
                    device_info = self.app.detected_gpus[idx]
                    env["gpu_device"] = device_info["device"]

                    if gpu_setting == "auto":
                        device_name = device_info.get("name", "").lower()
                        if "intel" in device_name:
                            env["gpu"] = "intel"
                        elif "nvidia" in device_name:
                            env["gpu"] = "nvidia"
                        elif ("amd" in device_name
                              or "advanced micro devices" in device_name):
                            env["gpu"] = "amd"
                        logger.debug(
                            f"Auto-detected {env['gpu']} GPU from device selection: "
                            f"{device_name}"
                        )

            # Smart GPU selection: when auto mode + auto device + multiple GPUs,
            # pick the best GPU for the selected codec
            if (
                gpu_setting == "auto"
                and gpu_device_index == 0
                and hasattr(self.app, "detected_gpus")
                and len(self.app.detected_gpus) > 1
            ):
                from utils.gpu_selector import select_best_gpu

                codec = settings.load_setting("video-codec", "h264")
                best = select_best_gpu(self.app.detected_gpus, codec)
                if best:
                    env["gpu"] = best["type"]
                    if best.get("device"):
                        env["gpu_device"] = best["device"]
                    logger.info(
                        f"Smart GPU selection: {best['type']} "
                        f"(device={best.get('device', 'default')}) for {codec}"
                    )

        env["video_quality"] = settings.load_setting("video-quality", "default")
        env["video_encoder"] = settings.load_setting("video-codec", "h264")
        env["preset"] = settings.load_setting("preset", "default")
        return env

    def force_start_conversion(self, gpu_override=None):
        """Start conversion process with the currently selected file"""
        # Check if we have a file to convert
        if not hasattr(self, "current_file_path") or not os.path.exists(
            self.current_file_path
        ):
            logger.error("Cannot start conversion: No valid file selected")
            return False

        # Get the file to convert
        input_file = self.current_file_path
        logger.debug(f"Starting conversion for: {input_file}")

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
                    logger.debug(
                        "Force copy video enabled: disabling hardware acceleration "
                        "and skipping video_quality, video_encoder, preset"
                    )
                else:
                    env_vars.update(self._encoding_environment(gpu_override))

                # Subtitle handling (works regardless of copy mode)
                env_vars["subtitle_extract"] = self.app.settings_manager.load_setting(
                    "subtitle-extract", "embedded"
                )

                # The widgets already carry a preset's structured choices; the
                # file adds what has no widget: per-encoder arguments and the
                # preset's own FFmpeg options.
                active_preset = self.app.active_preset() if hasattr(self.app, "active_preset") else None
                if active_preset is not None:
                    env_vars["preset_file"] = active_preset.path
                else:
                    env_vars.pop("preset_file", None)

                # Audio handling - Check if video has audio streams
                audio_handling = self.app.settings_manager.load_setting(
                    "audio-handling", "copy"
                )

                # Import audio detection function
                from utils.file_info import has_audio_streams

                if not has_audio_streams(input_file):
                    # Video has no audio streams, force audio_handling to "none"
                    audio_handling = "none"
                    logger.debug(
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
                    logger.debug("Copy mode enabled - skipping video_resolution")

                # Set flags
                if self.app.settings_manager.get_boolean("gpu-partial", False):
                    env_vars["gpu_partial"] = "1"
                if force_copy_video_enabled:
                    env_vars["force_copy_video"] = "1"
                if self.app.settings_manager.get_boolean(
                    "only-extract-subtitles", False
                ):
                    env_vars["only_extract_subtitles"] = "1"

                # Noise reduction
                sm = self.app.settings_manager
                if sm.get_boolean("noise-reduction", False):
                    env_vars["noise_reduction"] = "1"

                    # Core NR parameters
                    env_vars["noise_strength"] = str(
                        sm.load_setting("noise-reduction-strength", 1.0)
                    )
                    env_vars["noise_model"] = str(sm.load_setting("noise-model", 0))
                    env_vars["noise_speech_strength"] = str(
                        sm.load_setting("noise-speech-strength", 1.0)
                    )
                    env_vars["noise_lookahead"] = str(
                        sm.load_setting("noise-lookahead", 50)
                    )
                    env_vars["noise_model_blend"] = (
                        "1" if sm.get_boolean("noise-model-blend", False) else "0"
                    )
                    env_vars["noise_voice_recovery"] = str(
                        sm.load_setting("noise-voice-recovery", 0.75)
                    )

                # Audio filters (work independently of NR)
                # Noise gate
                if sm.get_boolean("noise-gate-enabled", False):
                    env_vars["noise_gate"] = "1"
                    env_vars["gate_intensity"] = str(
                        sm.load_setting("noise-gate-intensity", 0.5)
                    )

                # High-pass filter
                if sm.get_boolean("hpf-enabled", False):
                    env_vars["hpf_enabled"] = "1"
                    env_vars["hpf_frequency"] = str(
                        sm.load_setting("hpf-frequency", 80)
                    )

                # Compressor
                if sm.get_boolean("compressor-enabled", False):
                    env_vars["compressor_enabled"] = "1"
                    env_vars["compressor_intensity"] = str(
                        sm.load_setting("compressor-intensity", 1.0)
                    )

                # Equalizer
                if sm.get_boolean("eq-enabled", False):
                    env_vars["eq_enabled"] = "1"
                    env_vars["eq_bands"] = str(
                        sm.load_setting("eq-bands", "0,0,0,0,0,0,0,0,0,0")
                    )

                # Loudness normalization
                if sm.get_boolean("normalize-enabled", False):
                    env_vars["normalize_enabled"] = "1"

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
                logger.debug(
                    f"Using per-file metadata for {os.path.basename(input_file)}"
                )

                # Get trim segments from per-file metadata
                trim_segments = deepcopy(file_metadata.get("trim_segments", []))

                # Get output mode from per-file metadata (not global settings)
                output_mode = file_metadata.get("output_mode", "join")

                # Get crop values from per-file metadata (not global settings)
                crop_left = file_metadata.get("crop_left", 0)
                crop_right = file_metadata.get("crop_right", 0)
                crop_top = file_metadata.get("crop_top", 0)
                crop_bottom = file_metadata.get("crop_bottom", 0)

                # Per-file editing values are applied through a local override
                # instead of being written to the global settings: two parallel
                # conversions would otherwise overwrite each other's filters.
                filter_settings = SettingsOverride(
                    self.app.settings_manager,
                    {
                        "preview-crop-left": crop_left,
                        "preview-crop-right": crop_right,
                        "preview-crop-top": crop_top,
                        "preview-crop-bottom": crop_bottom,
                        "preview-brightness": file_metadata.get("brightness", 0.0),
                        "preview-saturation": file_metadata.get("saturation", 1.0),
                        "preview-hue": file_metadata.get("hue", 0.0),
                        "preview-rotation": file_metadata.get("rotation", 0),
                        "preview-flip-h": file_metadata.get("flip_h", False),
                        "preview-flip-v": file_metadata.get("flip_v", False),
                    },
                )

                # Try to get video dimensions if there are crop values
                video_width = None
                video_height = None

                # If we need to crop and don't have dimensions, try to get them
                if (
                    crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0
                ) and (video_width is None or video_height is None):
                    # Answered from the probe cache the queue warmed off the
                    # main thread; a cache miss still probes synchronously.
                    from utils.file_info import get_video_dimensions

                    video_width, video_height = get_video_dimensions(input_file)

                if crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0:
                    logger.debug(
                        f"Using crop values: left={crop_left}, right={crop_right}, top={crop_top}, bottom={crop_bottom}"
                    )

                # Get the unified video filter string from the per-file values.
                # Skip video filters when in copy mode since filters require re-encoding
                if not force_copy_video_enabled:
                    # Pass input file for H.265 10-bit detection
                    video_filter = get_video_filter_string(
                        filter_settings,
                        video_width=video_width,
                        video_height=video_height,
                        input_file=input_file,
                    )

                    if video_filter:
                        env_vars["video_filter"] = video_filter
                        logger.debug(f"Using video_filter: {env_vars['video_filter']}")
                    else:
                        logger.debug(
                            "No video filters applied (may be handled by optimized GPU conversion)"
                        )
                else:
                    logger.debug(
                        "Copy mode enabled - skipping video_filter (filters require re-encoding)"
                    )

                # Validate the option grammar shared with the backend. The
                # transport text is parsed into argv, never executed as shell.
                raw_additional_options = self.app.settings_manager.load_setting(
                    "additional-options", ""
                )
                options_ok, additional_options = validate_additional_options(
                    raw_additional_options
                )
                if not options_ok:
                    error_message = additional_options
                    logger.error(f"Rejected additional options: {error_message}")
                    GLib.idle_add(
                        lambda msg=error_message: self.app.show_error_dialog(msg)
                    )
                    return False

                # Handle trimming based on number of segments
                if len(trim_segments) == 0:
                    # No trimming, process full video
                    logger.debug("No segments defined, processing full video")
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
                        logger.debug(f"Adding trim start to options: -ss {start_str}")

                    if trim_end is not None:
                        duration_secs = trim_end - trim_start
                        duration_str = self._format_time_ffmpeg(duration_secs)
                        additional_options += f" -t {duration_str}"
                        logger.debug(
                            f"Adding trim duration to options: -t {duration_str}"
                        )

                # Set the final options environment variable
                if additional_options:
                    env_vars["options"] = additional_options
                    logger.debug(f"Setting options={additional_options}")

                # REMOVED: Separate crop handling - this is now done through the video_filter mechanism
                # We still pass the dimensions to the environment for other potential uses
                if video_width is not None and video_height is not None:
                    env_vars["video_width"] = str(video_width)
                    env_vars["video_height"] = str(video_height)

        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Error setting up conversion environment: {e}")
            import traceback

            traceback.print_exc()
            return False

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
                    logger.debug(
                        f"Output file exists, using alternative name: {output_basename}"
                    )
                    break
                counter += 1

        # Set the full path as output_file
        env_vars["output_file"] = full_output_path

        # Remove output_folder to avoid confusion in the bash script
        if "output_folder" in env_vars:
            del env_vars["output_folder"]

        logger.debug(f"Full output path: {full_output_path}")

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

        job_info = next((info for info in getattr(self.app, "active_conversions", [])
                         if info.get("file_path") == input_file), {})
        job_id = job_info.get("job_id")
        cancel_event = job_info.get("cancel_event") or threading.Event()

        # Check MP4 compatibility when copying without reencoding to MP4
        force_copy_video = env_vars.get("force_copy_video") == "1"
        if force_copy_video and output_format == "mp4":
            from utils.file_info import check_mp4_compatibility

            is_compatible, incompatible_streams = check_mp4_compatibility(input_file)
            if not is_compatible:
                # Package all needed variables
                conversion_context = {
                    "cmd": cmd,
                    "env_vars": env_vars,
                    "job_id": job_id,
                    "cancel_event": cancel_event,
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

                # Re-encoding uses the settings copy mode had skipped, the
                # accelerator included: choosing "re-encode" is not a request
                # to fall back to the processor.
                reencode_env = dict(env_vars)
                reencode_env.pop("force_copy_video", None)
                reencode_env.pop("force_software", None)
                reencode_env.update(self._encoding_environment(gpu_override))
                video_resolution = self.app.settings_manager.load_setting(
                    "video-resolution", "")
                if video_resolution:
                    reencode_env["video_resolution"] = video_resolution
                reencode_env["video_filter"] = get_video_filter_string(
                    filter_settings, video_width=video_width, video_height=video_height,
                    input_file=input_file)

                def show_compatibility_warning() -> None:
                    dialog = Adw.AlertDialog()
                    dialog.set_heading(_("These tracks can't be copied into MP4"))
                    dialog.set_body(
                        _(
                            "“Copy video without reencoding” keeps the original "
                            "streams as they are, and the MP4 container does not "
                            "accept the ones listed below."
                        )
                    )
                    dialog.set_extra_child(
                        self._build_incompatible_streams_child(incompatible_streams)
                    )

                    # Wider layout when libadwaita supports it (1.5+).
                    if hasattr(dialog, "set_prefer_wide_layout"):
                        dialog.set_prefer_wide_layout(True)

                    dialog.add_response("cancel", _("Cancel"))
                    dialog.add_response("reencode", _("Reencode (recommended)"))
                    dialog.add_response("proceed", _("Copy anyway"))
                    dialog.set_response_appearance(
                        "reencode", Adw.ResponseAppearance.SUGGESTED
                    )
                    dialog.set_response_appearance(
                        "proceed", Adw.ResponseAppearance.DESTRUCTIVE
                    )
                    dialog.set_default_response("reencode")
                    dialog.set_close_response("cancel")

                    def on_response(dialog, response) -> None:
                        if cancel_event.is_set() or response == "cancel":
                            cancel_event.set()
                            self.app.conversion_completed(False, file_path=input_file, job_id=job_id)
                            return
                        if response == "reencode":
                            conversion_context["env_vars"] = reencode_env
                        # Direct invocation: this callback is already on GTK's
                        # main loop. Do not schedule a True-returning idle task.
                        try:
                            self._continue_conversion(conversion_context)
                        except Exception as error:
                            logger.exception("Could not resume conversion")
                            self.app.show_error_dialog(str(error))
                            self.app.conversion_completed(False, file_path=input_file, job_id=job_id)

                    dialog.connect("response", on_response)
                    dialog.present(self.app.window)

                # Show dialog in main thread
                GLib.idle_add(show_compatibility_warning)
                return True  # The active job remains reserved while awaiting a decision.

        # Continue with conversion
        conversion_context = {
            "cmd": cmd,
            "env_vars": env_vars,
            "job_id": job_id,
            "cancel_event": cancel_event,
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

    def _build_incompatible_streams_child(self, streams):
        """Build the list of MP4-incompatible tracks shown in the warning dialog."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        # Give the dialog room to breathe: AlertDialog sizes itself around the
        # extra child, and the default width squeezes these labels into 3 lines.
        box.set_size_request(480, -1)

        group = Adw.PreferencesGroup()
        group.set_title(_("Incompatible tracks"))

        for stream in streams:
            codec = (stream.get("codec_name") or "").upper()
            row = Adw.ActionRow(title=codec or _("Unknown codec"))

            if stream.get("codec_type") == "video":
                kind = _("Video track {0}").format(stream.get("index", 1))
                icon_name = "video-x-generic-symbolic"
            else:
                kind = _("Audio track {0}").format(stream.get("index", 1))
                icon_name = "audio-volume-high-symbolic"

            details = [kind]
            language = stream.get("language")
            if language:
                details.append(language.upper())
            title = stream.get("title")
            if title:
                details.append(title)
            row.set_subtitle(" · ".join(details))

            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.add_css_class("dim-label")
            row.add_prefix(icon)

            badge = Gtk.Label(label=_("not supported by MP4"))
            badge.add_css_class("caption")
            badge.add_css_class("warning")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)

            group.add(row)

        box.append(group)

        hint = Gtk.Label()
        hint.set_wrap(True)
        hint.set_xalign(0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_label(
            _(
                "Reencode converts these tracks so the MP4 plays anywhere. "
                "Copying anyway is faster, but the result may have no sound or "
                "may not play at all — choose MKV as the output format to keep "
                "the original tracks untouched."
            )
        )
        box.append(hint)

        return box

    def _continue_conversion(self, context):
        """Continue with the actual conversion process"""
        # The naming and segment fields are read by start_segment_batch, which
        # takes the whole context; only these are used here.
        cmd = context["cmd"]
        env_vars = context["env_vars"]
        delete_original = context["delete_original"]
        input_file = context["input_file"]
        trim_segments = context["trim_segments"]

        # Log the command and environment variables for debugging
        logger.debug("\n=== CONVERSION COMMAND ===")
        logger.debug(f"Command: {' '.join(cmd)}")
        logger.debug("\n=== ENVIRONMENT VARIABLES ===")
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
            logger.debug(f"Raw video-quality setting: {settings_dict['video-quality']}")
        else:
            logger.debug("video-quality setting not found in config")

        if "video-codec" in settings_dict:
            logger.debug(f"Raw video-codec setting: {settings_dict['video-codec']}")
        else:
            logger.debug("video-codec setting not found in config")

        for key, value in conversion_vars.items():
            logger.debug(f"{key}={value}")
        logger.debug("===========================\n")

        if context.get("cancel_event") is not None and context["cancel_event"].is_set():
            self.app.conversion_completed(False, file_path=input_file, job_id=context.get("job_id"))
            return False
        if len(trim_segments) > 1:
            return start_segment_batch(self, context)

        # Single segment or no segments - use standard conversion
        # Calculate segment duration for single-segment trimming for accurate progress
        segment_duration = None
        if len(trim_segments) == 1:
            segment_duration = trim_segments[0]["end"] - trim_segments[0]["start"]
            logger.debug(
                f"Single segment mode: segment_duration={segment_duration:.2f}s"
            )

        # Create and display progress dialog
        # Always pass input_file for proper queue tracking
        run_with_progress_dialog(
            self.app,
            cmd,
            f"{os.path.basename(input_file)}",
            input_file,  # Always pass full path for queue tracking
            delete_original,
            env_vars,
            segment_duration=segment_duration,
            job_id=context.get("job_id"), cancel_event=context.get("cancel_event"),
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
            logger.debug(
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

    def _on_output_folder_entry_changed(self, entry) -> None:
        """Persist the destination folder, but only while it is in use.

        set_file() writes the input folder into this entry when the "same
        folder as the original file" mode is active; saving that would replace
        the destination the user configured.
        """
        if self.folder_combo.get_selected() != 1:
            return
        self.app.settings_manager.save_setting("output-folder", entry.get_text())

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

        # Keep the configured path: switching back to "same folder as the
        # original" should not throw away the folder the user chose, so it is
        # still there when they switch the option on again. The path is simply
        # ignored while this mode is active.

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

    def on_show_file_info(self, button, file_path: str) -> None:
        """Show detailed information about the video file"""
        if file_path and os.path.exists(file_path):
            from utils.file_info import VideoInfoDialog

            info_dialog = VideoInfoDialog(self.app.window, file_path)
            info_dialog.show()
        else:
            logger.error(f"Error: Invalid file path: {file_path}")
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
    def on_remove_from_queue_by_path(self, file_path: str) -> None:
        """Remove file from queue (callback for FileQueueRow)"""
        self.on_remove_from_queue(None, file_path)

    def on_play_file_by_path(self, file_path: str) -> None:
        """Play file (callback for FileQueueRow)"""
        self.on_play_file(None, file_path)

    def on_edit_file_by_path(self, file_path: str) -> None:
        """Edit file (callback for FileQueueRow)"""
        self.on_edit_file(None, file_path)

    def on_show_file_info_by_path(self, file_path: str) -> None:
        """Show file info (callback for FileQueueRow)"""
        self.on_show_file_info(None, file_path)

    def on_file_options_by_path(self, file_path: str) -> None:
        """Choose a preset and resolution for one queue item."""

        from utils.presets import list_presets

        metadata = normalize_metadata(self.file_metadata.get(file_path))
        presets = list_presets()

        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Options for this video"))
        dialog.set_body(
            _(
                "Override the general recipe only when this video needs a "
                "different destination profile or size."
            )
        )
        if hasattr(dialog, "set_prefer_wide_layout"):
            dialog.set_prefer_wide_layout(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_size_request(520, -1)
        content.set_margin_top(8)

        group = Adw.PreferencesGroup()
        group.set_title(_("Effective result"))
        group.set_description(
            _("General settings remain the default for every other video.")
        )

        preset_names = [_("Use the general recipe")] + [
            preset.display_name for preset in presets
        ]
        preset_row = Adw.ComboRow(
            title=_("Profile"),
            subtitle=_("Target a platform or workflow"),
            model=Gtk.StringList.new(preset_names),
        )
        selected_preset = 0
        for index, preset in enumerate(presets, start=1):
            if preset.id == metadata.get("preset_id"):
                selected_preset = index
                break
        preset_row.set_selected(selected_preset)
        group.add(preset_row)

        resolution_labels = [
            _("Use the general resolution"),
            _("Use the profile resolution"),
            _("Keep the original resolution"),
            "4K · 3840×2160",
            "QHD · 2560×1440",
            "Full HD · 1920×1080",
            "HD · 1280×720",
            "SD · 854×480",
            "Vertical 4K · 2160×3840",
            "Vertical QHD · 1440×2560",
            "Vertical Full HD · 1080×1920",
            "Vertical HD · 720×1280",
            "Vertical SD · 480×854",
            _("Custom size"),
        ]
        resolution_row = Adw.ComboRow(
            title=_("Resolution"),
            subtitle=_("Controls image dimensions and file size"),
            model=Gtk.StringList.new(resolution_labels),
        )
        try:
            resolution_row.set_selected(
                RESOLUTION_MODES.index(metadata.get("resolution_mode", "global"))
            )
        except ValueError:
            resolution_row.set_selected(0)
        group.add(resolution_row)

        width_spin = Gtk.SpinButton.new_with_range(16, 16384, 2)
        width_spin.set_value(metadata.get("custom_width") or 1280)
        width_row = Adw.ActionRow(title=_("Width"), subtitle=_("Even pixels"))
        width_row.add_suffix(width_spin)
        group.add(width_row)

        height_spin = Gtk.SpinButton.new_with_range(16, 16384, 2)
        height_spin.set_value(metadata.get("custom_height") or 720)
        height_row = Adw.ActionRow(title=_("Height"), subtitle=_("Even pixels"))
        height_row.add_suffix(height_spin)
        group.add(height_row)

        preview = Adw.ActionRow(title=_("This video will use"))
        preview_value = Gtk.Label()
        preview_value.add_css_class("bvc-profile-chip")
        preview.add_suffix(preview_value)
        group.add(preview)

        content.append(group)
        dialog.set_extra_child(content)

        def refresh(*_args):
            custom = resolution_row.get_selected() == len(RESOLUTION_MODES) - 1
            width_row.set_visible(custom)
            height_row.set_visible(custom)
            temporary = dict(metadata)
            temporary["resolution_mode"] = RESOLUTION_MODES[
                resolution_row.get_selected()
            ]
            temporary["custom_width"] = int(width_spin.get_value())
            temporary["custom_height"] = int(height_spin.get_value())
            selected = preset_row.get_selected()
            temporary["preset_snapshot"] = (
                {"name": presets[selected - 1].display_name}
                if selected
                else None
            )
            preview_value.set_text(_(summary(temporary)))

        preset_row.connect("notify::selected", refresh)
        resolution_row.connect("notify::selected", refresh)
        width_spin.connect("value-changed", refresh)
        height_spin.connect("value-changed", refresh)
        refresh()

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Apply to this video"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def response(_dialog, response_id):
            if response_id != "save":
                return
            selected = preset_row.get_selected()
            if selected:
                chosen = presets[selected - 1]
                metadata["preset_id"] = chosen.id
                metadata["preset_snapshot"] = snapshot_preset(chosen)
            else:
                metadata["preset_id"] = None
                metadata["preset_snapshot"] = None
            metadata["resolution_mode"] = RESOLUTION_MODES[
                resolution_row.get_selected()
            ]
            if metadata["resolution_mode"] == "custom":
                metadata["custom_width"] = int(width_spin.get_value())
                metadata["custom_height"] = int(height_spin.get_value())
            else:
                metadata["custom_width"] = None
                metadata["custom_height"] = None
            self.file_metadata[file_path] = normalize_metadata(metadata)
            self.update_queue_display()

        dialog.connect("response", response)
        dialog.present(self.app.window)

    def generate_trim_options(self):
        """
        Generate trim options with the most up-to-date values
        and update the object attributes and environment variables.
        Should be called right before conversion starts.
        """
        # Always get the latest trim values directly from the app
        start_time, end_time, duration = self.app.get_trim_times()

        # If we don't have values from the app, check settings
        if start_time == 0 and end_time is None:
            start_time = self.app.settings_manager.load_setting("video-trim-start", 0.0)
            end_time_setting = self.app.settings_manager.load_setting(
                "video-trim-end", -1.0
            )
            end_time = None if end_time_setting < 0 else end_time_setting
            logger.debug(
                f"Using trim settings from settings: start={start_time}, end={end_time}"
            )
        else:
            logger.debug(
                f"Using trim settings from app state: start={start_time}, end={end_time}"
            )

        # Validate that end_time is not less than or equal to start_time
        if end_time is not None and end_time <= start_time:
            logger.warning(
                "WARNING: Invalid trim values detected (end_time <= start_time)"
            )
            end_time = None
            self.app.settings_manager.save_setting("video-trim-end", -1.0)

        # Always update the object attributes for consistency
        self.trim_start_time = start_time
        self.trim_end_time = end_time
        self.trim_duration = duration

        # Return a dictionary with trim configuration to be used by force_start_conversion
        return {"start_time": start_time, "end_time": end_time, "duration": duration}
