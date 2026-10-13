import os
import subprocess
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import gettext
import logging

from engine.job_options import (
    RESOLUTION_MODES,
    freeze,
    has_picture_edits,
    normalize_metadata,
    snapshot_preset,
)
from engine.presets import PresetError, output_container
from engine.size_target import (
    TARGETS,
    describe_plan,
    prepare_size_job,
    shown_size,
)
from engine.video_settings import SettingsOverride
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ui.conversion_start import ConversionStartMixin
from ui.file_queue_row import FileQueueRow
from ui.progress_page import launch_file
from ui.signal_connections import SignalConnections
from ui.thumbnail_cache import ThumbnailCache

logger = logging.getLogger(__name__)

_ = gettext.gettext


def has_own_settings(metadata) -> bool:
    """True when a video overrides the sidebar's profile, resolution or size."""
    metadata = normalize_metadata(metadata)
    return bool(metadata.get("preset_snapshot")) or any(
        metadata[key] != "global" for key in ("resolution_mode", "size_mode")
    )


class ConversionPage(ConversionStartMixin):
    """
    Conversion page UI component.
    Provides interface for selecting and converting video files.
    """

    def __init__(self, app):
        self.app = app

        # Storage for per-file editing metadata
        # Key: file_path, Value: dict with trim, crop, adjustments
        self.file_metadata = {}
        # GTK can recreate Python wrappers; own rows until their requests stop.
        self.queue_rows = []
        self._queue_render_id = None
        self._decision_dialogs = set()
        self.thumbnail_cache = ThumbnailCache(max_workers=2)
        self._thumbnail_finalizer = weakref.finalize(
            self, ThumbnailCache.shutdown, self.thumbnail_cache
        )
        # FFprobe for the rows' duration and codec: two at a time, cached.
        self.summary_pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="bvc-queue-probe"
        )
        self._summary_finalizer = weakref.finalize(
            self, self.summary_pool.shutdown, wait=False, cancel_futures=True
        )

        self.page = self._create_page()

        self._connect_settings()

    def get_page(self):
        return self.page

    def _create_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_start(6)
        page.set_margin_end(6)
        page.set_margin_top(12)
        page.set_margin_bottom(12)
        page.set_vexpand(True)

        queue_scroll = Gtk.ScrolledWindow()
        queue_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        queue_scroll.set_vexpand(True)
        queue_scroll.set_min_content_height(300)

        clamp = Adw.Clamp(maximum_size=1180, tightening_threshold=760)
        # The cards' rounded bottom edge and shadow need room inside the
        # scrolled area, or its edge cuts them off.
        clamp.set_margin_bottom(8)
        self.queue_listbox = Gtk.ListBox()
        self.queue_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.queue_listbox.set_show_separators(False)
        self.queue_listbox.set_vexpand(True)
        self.queue_listbox.add_css_class("bvc-queue-list")
        self.queue_listbox.connect("row-activated", self.on_queue_item_activated)

        # On the pane itself: a card around it read as a box inside the pane
        # once the window is wide.
        self.placeholder = Adw.StatusPage()
        self.placeholder.set_icon_name("folder-videos-symbolic")
        self.placeholder.set_title(_("No Video Files"))
        self.placeholder.set_description(
            _("Drag files here or use the Add Files button")
        )
        self.placeholder.set_vexpand(True)
        self.placeholder.set_hexpand(True)
        self.queue_listbox.set_placeholder(self.placeholder)
        clamp.set_child(self.queue_listbox)
        queue_scroll.set_child(clamp)
        page.append(queue_scroll)

        self.dragged_row = None
        self.queue_dragging_enabled = False

        # Destination on the left, the originals switch on the right.
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        options_box.add_css_class("bvc-queue-footer")

        folder_options_store = Gtk.StringList.new(
            [
                _("Save in the same folder as the original file"),
                _("Folder to save"),
            ]
        )
        # Long translations ellipsize instead of widening a narrow window.
        shrinking_labels = Gtk.SignalListItemFactory()
        shrinking_labels.connect(
            "setup",
            lambda _factory, item: item.set_child(Gtk.Label(xalign=0, ellipsize=3)),
        )
        shrinking_labels.connect(
            "bind",
            lambda _factory, item: item.get_child().set_label(
                item.get_item().get_string()
            ),
        )
        self.folder_combo = Gtk.DropDown(
            model=folder_options_store, factory=shrinking_labels
        )
        self.folder_combo.set_selected(0)
        self.folder_combo.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Save converted videos")]
        )
        self.folder_combo.set_valign(Gtk.Align.CENTER)
        self.folder_combo.connect("notify::selected", self._on_folder_type_changed)
        options_box.append(self.folder_combo)

        self.folder_entry_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=4
        )
        self.folder_entry_box.set_visible(False)
        self.folder_entry_box.set_hexpand(True)
        self.output_folder_entry = Gtk.Entry()
        self.output_folder_entry.set_hexpand(True)
        self.output_folder_entry.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Output folder")]
        )
        self.output_folder_entry.set_placeholder_text(_("Select folder"))
        self.folder_entry_box.append(self.output_folder_entry)
        choose_folder = Gtk.Button.new_from_icon_name("folder-symbolic")
        choose_folder.add_css_class("flat")
        choose_folder.add_css_class("circular")
        choose_folder.set_valign(Gtk.Align.CENTER)
        choose_folder.set_tooltip_text(_("Choose output folder"))
        choose_folder.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Choose output folder")]
        )
        choose_folder.connect("clicked", self.on_folder_button_clicked)
        self.folder_entry_box.append(choose_folder)
        options_box.append(self.folder_entry_box)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        options_box.append(spacer)

        delete_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        delete_box.set_valign(Gtk.Align.CENTER)
        self.delete_label = Gtk.Label(label=_("Delete original files"), ellipsize=3)
        delete_box.append(self.delete_label)
        self.delete_original_check = Gtk.Switch()
        self.delete_original_check.set_valign(Gtk.Align.CENTER)
        self.delete_original_check.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Delete original files")]
        )
        delete_box.append(self.delete_original_check)
        options_box.append(delete_box)
        # The same clamp as the list, so both share their edges at any width.
        page.append(
            Adw.Clamp(maximum_size=1180, tightening_threshold=760, child=options_box)
        )

        self.update_queue_display()
        return page

    def _connect_settings(self):
        self.show_output_settings()
        self.show_delete_mode()
        self.output_folder_entry.connect(
            "changed", self._on_output_folder_entry_changed
        )
        self.delete_original_check.connect(
            "notify::active",
            lambda w, p: self.app.settings_store.save_setting(
                "delete-original", w.get_active()
            ),
        )

    def show_delete_mode(self):
        """Say where originals go: the Trash, or deleted outright."""
        permanent = self.app.settings_store.load_setting("skip-trash", False)
        self.delete_label.set_tooltip_text(
            _(
                "Originals are deleted permanently, without the Trash, only "
                "after every converted result passes validation"
            )
            if permanent
            else _(
                "Originals go to the Trash only after every converted result "
                "passes validation"
            )
        )

    def show_output_settings(self):
        """Show the saved destination and delete-original choice."""
        settings = self.app.settings_store
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
        self.folder_combo.set_selected(1 if use_custom_folder else 0)
        self.folder_entry_box.set_visible(use_custom_folder)
        self.output_folder_entry.set_text(output_folder)
        self.delete_original_check.set_active(delete_original)

    def on_folder_button_clicked(self, button) -> None:
        """Open folder chooser dialog to select output folder"""
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Select the output folder"))
        dialog.set_initial_folder(
            Gio.File.new_for_path(self.app.last_accessed_directory)
        )
        dialog.select_folder(self.app.window, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # dismissed
        if folder and folder.get_path():
            self.output_folder_entry.set_text(folder.get_path())
            self.app.settings_store.save_setting("output-folder", folder.get_path())

    def on_queue_item_activated(self, listbox, row) -> None:
        """Open the selected video without conflating row and button actions."""
        self.on_edit_file_by_path(row.file_path)

    def update_queue_display(self) -> None:

        if self._queue_render_id is not None:
            GLib.source_remove(self._queue_render_id)
            self._queue_render_id = None

        while True:
            row = self.queue_listbox.get_first_child()
            if row:
                if isinstance(row, FileQueueRow):
                    row.dispose_thumbnail()
                self.queue_listbox.remove(row)
            else:
                break

        # Re-set placeholder after clearing (GTK may remove it during clear)
        self.queue_rows.clear()
        self.queue_listbox.set_placeholder(self.placeholder)

        pending = iter(enumerate(tuple(self.app.conversion_queue)))

        def append_rows():
            if getattr(self.app, "_quitting", False):
                self._queue_render_id = None
                return GLib.SOURCE_REMOVE
            # Yield between small groups so large queues leave time for input and frames.
            for _ in range(12):
                try:
                    index, file_path = next(pending)
                except StopIteration:
                    self._queue_render_id = None
                    return GLib.SOURCE_REMOVE
                row = FileQueueRow(
                    file_path=file_path,
                    index=index,
                    on_remove_callback=self.on_remove_from_queue_by_path,
                    on_play_callback=self.on_play_file_by_path,
                    on_edit_callback=self.on_edit_file_by_path,
                    on_info_callback=self.on_show_file_info_by_path,
                    on_options_callback=self.on_file_options_by_path,
                    metadata=self.file_metadata.get(file_path),
                    thumbnail_cache=self.thumbnail_cache,
                    summary_text=self.file_recipe_summary(
                        self.file_metadata.get(file_path)
                    ),
                    app=self.app,
                    custom_settings=has_own_settings(self.file_metadata.get(file_path)),
                    summary_pool=self.summary_pool,
                )

                self.queue_listbox.append(row)
                self.queue_rows.append(row)
            return GLib.SOURCE_CONTINUE

        if append_rows():
            self._queue_render_id = GLib.idle_add(append_rows)

        self.app.header_bar.update_queue_size(len(self.app.conversion_queue))

        if len(self.app.conversion_queue) > 1 and not self.queue_dragging_enabled:
            if hasattr(self, "drag_source") and self.drag_source:
                self.queue_listbox.remove_controller(self.drag_source)
            if hasattr(self, "drop_target") and self.drop_target:
                self.queue_listbox.remove_controller(self.drop_target)

            self.drag_source = Gtk.DragSource.new()
            self.drag_source.set_actions(Gdk.DragAction.MOVE)
            self.drag_source.connect("prepare", self.on_drag_prepare_listbox)
            self.drag_source.connect("drag-begin", self.on_drag_begin_listbox)
            self.drag_source.connect("drag-end", self.on_drag_end_listbox)
            self.queue_listbox.add_controller(self.drag_source)

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

    # Unified drag and drop handlers for listbox
    def on_drag_prepare_listbox(self, drag_source, x, y):
        row = self.queue_listbox.get_row_at_y(y)
        if row and hasattr(row, "index"):
            self.dragged_row = row

            return Gdk.ContentProvider.new_for_value(str(row.index))
        return None

    def on_drag_begin_listbox(self, drag_source, drag) -> None:
        if self.dragged_row:
            self.dragged_row.add_css_class("dragging")

    def on_drag_end_listbox(self, drag_source, drag, delete_data) -> None:
        """Clean up after drag operation"""
        for i in range(len(self.app.conversion_queue)):
            row = self.queue_listbox.get_row_at_index(i)
            if row:
                row.remove_css_class("dragging")
                row.remove_css_class("drag-hover")

        self.dragged_row = None

    def on_drag_motion_listbox(self, drop_target, x, y):
        """Handle drag motion to show drop target position"""
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
            source_index = int(value)
            # Only a row dragged from this list reorders it; text dropped
            # from elsewhere ("5") is not an index into the queue.
            if self.dragged_row is None or not 0 <= source_index < len(
                self.app.conversion_queue
            ):
                return False

            target_row = self.queue_listbox.get_row_at_y(y)
            if not target_row:
                # Dropped below the last row: move it to the end.
                target_index = len(self.app.conversion_queue)
            else:
                target_index = target_row.index

                # If dropping onto self, do nothing
                if source_index == target_index:
                    return False

            for i in range(len(self.app.conversion_queue)):
                row = self.queue_listbox.get_row_at_index(i)
                if row:
                    row.remove_css_class("dragging")
                    row.remove_css_class("drag-hover")

            item_to_move = self.app.conversion_queue[source_index]
            queue_list = list(self.app.conversion_queue)
            del queue_list[source_index]
            queue_list.insert(
                target_index if target_index < source_index else target_index - 1,
                item_to_move,
            )

            self.app.conversion_queue.clear()
            self.app.conversion_queue.extend(queue_list)

            self.update_queue_display()

            return True
        except (ValueError, TypeError):
            logger.exception("Could not reorder the queue")
            return False

    def on_edit_file_by_path(self, file_path: str) -> None:
        """Open file in the video editor"""
        if file_path and os.path.exists(file_path):
            self.app.show_editor_for_file(file_path)
        else:
            logger.error("Invalid file path for edit: %s", file_path)
            self.app.show_error_dialog(_("Could not open this video file"))

    def on_remove_from_queue_by_path(self, file_path: str) -> None:
        self.app.remove_from_queue(file_path)
        self.update_queue_display()

    def on_play_file_by_path(self, file_path: str) -> None:
        """Play the video in the default player."""
        if file_path and os.path.exists(file_path):
            launch_file(self.app, self.app.window, file_path)
        else:
            self.app.show_error_dialog(_("Could not find this video file"))

    def _on_output_folder_entry_changed(self, entry) -> None:
        """Persist the destination folder while "In one folder" is selected."""
        if self.folder_combo.get_selected() != 1:
            return
        self.app.settings_store.save_setting("output-folder", entry.get_text())

    def _on_folder_type_changed(self, combo, param):
        selected = combo.get_selected()
        use_custom_folder = selected == 1  # "Custom folder" option is selected

        self.folder_entry_box.set_visible(use_custom_folder)

        self.app.settings_store.save_setting(
            "use-custom-output-folder", use_custom_folder
        )

        # Keep the configured path: switching back to "same folder as the
        # original" should not throw away the folder the user chose, so it is
        # still there when they switch the option on again. The path is simply
        # ignored while this mode is active.

    def on_show_file_info_by_path(self, file_path: str) -> None:
        """Show detailed information about the video file"""
        if file_path and os.path.exists(file_path):
            from ui.video_info_dialog import VideoInfoDialog

            info_dialog = VideoInfoDialog(self.app.window, file_path)
            info_dialog.show()
        else:
            logger.error("Invalid file path: %s", file_path)
            self.app.show_error_dialog(_("Could not find this video file"))

    def refresh_recipe_summaries(self) -> None:
        row = self.queue_listbox.get_first_child()
        while row is not None:
            if isinstance(row, FileQueueRow):
                metadata = self.file_metadata.get(row.file_path)
                row.set_recipe(
                    self.file_recipe_summary(metadata), has_own_settings(metadata)
                )
            row = row.get_next_sibling()

    def file_recipe_summary(self, metadata) -> str:
        metadata = normalize_metadata(metadata)
        settings = freeze(self.app.settings_store.settings, metadata)["settings"]
        preset = metadata.get("preset_snapshot") or {}
        name = preset.get("name") or _("General settings")
        resolution = settings.get("video-resolution") or _("Original resolution")
        if settings.get("force-copy-video"):
            resolution = _("Original resolution")
        target_id = settings.get("size-target", "")
        if target_id:
            # The size decides the resolution, so the limit takes its place.
            resolution = _("up to {size}").format(
                size=shown_size(target_id, settings.get("size-target-mb", 50.0))
            )
        return _("{profile} · {resolution}").format(profile=name, resolution=resolution)

    def on_file_options_by_path(self, file_path: str) -> None:
        """Choose a preset and resolution for one queue item."""

        from engine.presets import list_presets

        metadata = normalize_metadata(self.file_metadata.get(file_path))
        try:
            presets = [snapshot_preset(preset) for preset in list_presets()]
        except (OSError, ValueError, PresetError) as error:
            self.app.show_error_dialog(str(error))
            return
        saved_preset = metadata.get("preset_snapshot")
        if saved_preset:
            presets = [saved_preset] + [
                preset for preset in presets if preset["id"] != saved_preset["id"]
            ]

        dialog = Adw.AlertDialog()
        connections = SignalConnections(dialog)
        dialog.set_heading(_("Options for this video"))
        dialog.set_body(
            _(
                "Override the general recipe only when this video needs a "
                "different destination profile or size."
            )
        )
        if hasattr(dialog, "set_prefer_wide_layout"):  # libadwaita 1.6
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
            preset["name"] for preset in presets
        ]
        preset_row = Adw.ComboRow(
            title=_("Profile"),
            subtitle=_("Target a platform or workflow"),
            model=Gtk.StringList.new(preset_names),
        )
        selected_preset = 0
        for index, preset in enumerate(presets, start=1):
            if preset["id"] == (saved_preset or {}).get(
                "id", metadata.get("preset_id")
            ):
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
            _("Vertical 4K · 2160×3840"),
            _("Vertical QHD · 1440×2560"),
            _("Vertical Full HD · 1080×1920"),
            _("Vertical HD · 720×1280"),
            _("Vertical SD · 480×854"),
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
        width_row.set_activatable_widget(width_spin)
        width_spin.update_property([Gtk.AccessibleProperty.LABEL], [_("Width")])
        group.add(width_row)

        height_spin = Gtk.SpinButton.new_with_range(16, 16384, 2)
        height_spin.set_value(metadata.get("custom_height") or 720)
        height_row = Adw.ActionRow(title=_("Height"), subtitle=_("Even pixels"))
        height_row.add_suffix(height_spin)
        height_row.set_activatable_widget(height_spin)
        height_spin.update_property([Gtk.AccessibleProperty.LABEL], [_("Height")])
        group.add(height_row)

        # Where this one video is going, when that differs from the others.
        size_modes = ("global", "none", *(t[0] for t in TARGETS), "custom")
        size_labels = [
            _("Use the general size"),
            _("No limit"),
            *(f"{t[3]} · {_(t[1])}" for t in TARGETS),
            _("Another size"),
        ]
        size_row = Adw.ComboRow(
            title=_("Maximum size"),
            subtitle=_("Fits this video under a size, whatever the general setting"),
            model=Gtk.StringList.new(size_labels),
        )
        size_row.set_selected(size_modes.index(metadata["size_mode"]))
        group.add(size_row)
        size_spin = Gtk.SpinButton.new_with_range(1, 1_000_000, 1)
        size_spin.set_value(metadata.get("size_mb") or 50)
        size_spin.set_valign(Gtk.Align.CENTER)
        size_spin.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Size in megabytes")]
        )
        size_mb_row = Adw.ActionRow(title=_("Size in megabytes"))
        size_mb_row.add_suffix(size_spin)
        size_mb_row.set_activatable_widget(size_spin)
        group.add(size_mb_row)

        preview = Adw.ActionRow(title=_("This video will use"))
        preview_value = Gtk.Label()
        preview_value.add_css_class("bvc-profile-chip")
        preview.add_suffix(preview_value)
        group.add(preview)
        # What the size target will do, worked out from the file itself.
        size_result = Adw.ActionRow(title=_("To fit the size"))
        size_result.add_css_class("property")
        group.add(size_result)
        size_generation = [0]
        # Probe once the spin buttons pause, not on every step.
        size_timer = [None]
        dialog_closed = [False]

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
            size_mb_row.set_visible(size_modes[size_row.get_selected()] == "custom")
            temporary["size_mode"] = size_modes[size_row.get_selected()]
            temporary["size_mb"] = float(size_spin.get_value())
            selected = preset_row.get_selected()
            temporary["preset_snapshot"] = presets[selected - 1] if selected else None
            preview_value.set_text(self.file_recipe_summary(temporary))
            refresh_size_result(temporary)

        def refresh_size_result(temporary):
            size_generation[0] += 1
            generation = size_generation[0]
            settings = freeze(self.app.settings_store.settings, temporary)["settings"]
            if not settings.get("size-target"):
                size_result.set_visible(False)
                return
            size_result.set_visible(True)
            size_result.set_subtitle(_("Checking the video…"))
            output_ext = "." + output_container(settings.get("output-format-index", 0))
            context = {
                "size_target": self._size_target(
                    SettingsOverride(self.app.settings_store, settings)
                ),
                "input_file": file_path,
                "output_ext": output_ext,
                "trim_segments": temporary.get("trim_segments") or [],
                "output_mode": temporary.get("output_mode", "join"),
                "encode_env": {
                    "video_encoder": settings.get("video-codec", "h264"),
                    "audio_handling": settings.get("audio-handling", "copy"),
                    "audio_bitrate": settings.get("audio-bitrate", ""),
                    "video_filter": "edited" if has_picture_edits(temporary) else "",
                    "video_resolution": settings.get("video-resolution", ""),
                    "video_fps": settings.get("video-fps", ""),
                },
            }

            def work():
                try:
                    text = describe_plan(prepare_size_job(context))
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    text = str(error)

                def show():
                    if not dialog_closed[0] and generation == size_generation[0]:
                        size_result.set_subtitle(text)
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(show)

            def start():
                size_timer[0] = None
                threading.Thread(target=work, daemon=True).start()
                return GLib.SOURCE_REMOVE

            if size_timer[0] is not None:
                GLib.source_remove(size_timer[0])
            size_timer[0] = GLib.timeout_add(300, start)

        def on_closed(_dialog):
            dialog_closed[0] = True
            if size_timer[0] is not None:
                GLib.source_remove(size_timer[0])
                size_timer[0] = None

        connections.connect(preset_row, "notify::selected", refresh)
        connections.connect(resolution_row, "notify::selected", refresh)
        connections.connect(width_spin, "value-changed", refresh)
        connections.connect(height_spin, "value-changed", refresh)
        connections.connect(size_row, "notify::selected", refresh)
        connections.connect(size_spin, "value-changed", refresh)
        dialog.connect("closed", on_closed)
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
                metadata["preset_id"] = chosen["id"]
                metadata["preset_snapshot"] = deepcopy(chosen)
            else:
                metadata["preset_id"] = None
                metadata["preset_snapshot"] = None
            metadata["resolution_mode"] = RESOLUTION_MODES[
                resolution_row.get_selected()
            ]
            metadata["size_mode"] = size_modes[size_row.get_selected()]
            metadata["size_mb"] = float(size_spin.get_value())
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
