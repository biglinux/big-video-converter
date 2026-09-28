import os
import subprocess
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Setup translation
import gettext
import logging
import re

from constants import CONVERT_SCRIPT_PATH
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango
from utils.conversion import run_with_progress_dialog
from utils.ffmpeg_options import validate_additional_options
from utils.job_options import (
    RESOLUTION_MODES,
    freeze,
    has_picture_edits,
    normalize_metadata,
    snapshot_preset,
)
from utils.presets import PresetError
from utils.segment_batch import start_segment_batch
from utils.signal_connections import SignalConnections
from utils.size_target import (
    TARGETS,
    describe_plan,
    prepare_size_job,
    shown_size,
    target_bytes,
)
from utils.thumbnail_cache import ThumbnailManager
from utils.video_settings import CropError, SettingsOverride, get_ffmpeg_filter_string

from ui.progress_page import CODEC_NAMES, format_clock, format_size

logger = logging.getLogger(__name__)

_ = gettext.gettext

# Settings the conversion script reads from its environment are all named so.
_SCRIPT_SETTING = re.compile(r"[a-z_]+")
_PROXY_VARIABLES = frozenset({"http_proxy", "https_proxy", "no_proxy", "all_proxy"})


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
        summary_text,
        app=None,
        custom_settings=False,
        summary_pool=None,
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
        row = weakref.proxy(self)

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
        self.thumbnail_picture.set_content_fit(Gtk.ContentFit.COVER)
        self.thumbnail_stack.add_named(placeholder, "placeholder")
        picture_frame = Gtk.Overlay()
        picture_frame.set_child(Gtk.Box(width_request=144, height_request=82))
        picture_frame.add_overlay(self.thumbnail_picture)
        picture_frame.set_measure_overlay(self.thumbnail_picture, False)
        self.thumbnail_stack.add_named(picture_frame, "picture")
        self.thumbnail_stack.set_visible_child_name("placeholder")
        # Duration on the picture's corner, as video apps show it.
        preview = Gtk.Overlay(child=self.thumbnail_stack)
        self.duration_badge = Gtk.Label(halign=Gtk.Align.END, valign=Gtk.Align.END)
        self.duration_badge.add_css_class("bvc-duration-badge")
        self.duration_badge.set_margin_end(6)
        self.duration_badge.set_margin_bottom(6)
        self.duration_badge.set_visible(False)
        preview.add_overlay(self.duration_badge)
        preview_frame.append(preview)
        content.append(preview_frame)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        details.set_hexpand(True)
        details.set_valign(Gtk.Align.CENTER)

        name = os.path.basename(file_path)
        title = Gtk.Label(label=name)
        title.set_xalign(0)
        title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        title.set_tooltip_text(file_path)
        title.add_css_class("title-3")
        details.append(title)

        folder = os.path.dirname(file_path)
        home = os.path.expanduser("~")
        if folder == home or folder.startswith(home + os.sep):
            folder = "~" + folder[len(home):]
        self.folder_label = Gtk.Label(label=folder, xalign=0)
        # The end of a path names the folder; its start is the same for all.
        self.folder_label.set_ellipsize(Pango.EllipsizeMode.START)
        self.folder_label.add_css_class("caption")
        self.folder_label.add_css_class("dim-label")
        details.append(self.folder_label)

        metadata_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        metadata_row.set_margin_top(3)
        self.meta_label = Gtk.Label(xalign=0)
        self.meta_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.meta_label.add_css_class("caption")
        self.meta_label.add_css_class("bvc-tabular")
        self.meta_label.add_css_class("bvc-meta-chip")
        self.meta_label.set_valign(Gtk.Align.CENTER)
        try:
            self._size_text = format_size(os.path.getsize(file_path))
            self.meta_label.set_text(self._size_text)
        except OSError:
            # Moved or deleted since it was queued: say so instead of hiding
            # a file the queue still counts and will report as failed.
            self._size_text = None
            self.meta_label.set_text(_("File not found"))
            self.meta_label.add_css_class("error")
        metadata_row.append(self.meta_label)
        # Only a video with settings of its own carries a chip; the others
        # follow the sidebar, which already says what they use.
        profile = Gtk.Label()
        profile.set_ellipsize(Pango.EllipsizeMode.END)
        profile.set_xalign(0)
        profile.add_css_class("bvc-profile-chip")
        profile.set_valign(Gtk.Align.CENTER)
        self.recipe_label = profile
        metadata_row.append(profile)
        self.set_recipe(summary_text, custom_settings)
        details.append(metadata_row)
        content.append(details)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        actions.set_valign(Gtk.Align.CENTER)
        actions.add_css_class("bvc-row-actions")

        options_button = Gtk.Button()
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        options_box.append(Gtk.Image.new_from_icon_name("preferences-system-symbolic"))
        options_box.append(Gtk.Label(label=_("Options")))
        options_button.set_child(options_box)
        options_button.add_css_class("flat")
        options_button.set_tooltip_text(_("Choose a preset or resolution for this video"))
        options_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Options for “{name}”").format(name=name)]
        )
        options_button.connect(
            "clicked", lambda _button: row.on_options_callback(row.file_path)
        )
        actions.append(options_button)
        actions.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        edit_button.add_css_class("flat")
        edit_button.set_tooltip_text(_("Edit video"))
        edit_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Edit “{name}”").format(name=name)]
        )
        edit_button.connect(
            "clicked", lambda _button: row.on_edit_callback(row.file_path)
        )
        actions.append(edit_button)
        actions.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        more_button = Gtk.MenuButton(icon_name="view-more-symbolic")
        more_button.add_css_class("flat")
        more_button.set_tooltip_text(_("More actions"))
        more_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("More actions for “{name}”").format(name=name)],
        )
        more_button.set_popover(self._create_more_popover())
        actions.append(more_button)
        content.append(actions)

        self._summary_future = None
        self._request_summary(summary_pool)
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
        row = weakref.proxy(self)
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.append(self._menu_action(
            _("Play video"), "media-playback-start-symbolic",
            lambda _b: row.on_play_callback(row.file_path),
        ))
        box.append(self._menu_action(
            _("Video information"), "dialog-information-symbolic",
            lambda _b: row.on_info_callback(row.file_path),
        ))
        box.append(self._menu_action(
            _("Open containing folder"), "folder-open-symbolic",
            lambda _b: row._on_open_folder(),
        ))
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(4)
        separator.set_margin_bottom(4)
        box.append(separator)
        box.append(self._menu_action(
            _("Remove from queue"), "list-remove-symbolic",
            lambda _b: row.on_remove_callback(row.file_path),
            destructive=True,
        ))
        box.append(self._menu_action(
            _("Delete from disk…"), "user-trash-symbolic",
            lambda _b: row._on_delete_from_disk(),
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
        self.thumbnail_picture.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Video preview")])
        return GLib.SOURCE_REMOVE

    def set_recipe(self, summary_text: str, custom: bool) -> None:
        text = _("Own: {recipe}").format(recipe=summary_text)
        self.recipe_label.set_text(text)
        self.recipe_label.set_tooltip_text(text)
        self.recipe_label.set_visible(custom)

    def _request_summary(self, pool) -> None:
        """Probe duration, size and codec on a worker; the result is cached."""
        if pool is None or self._size_text is None:
            return
        row_ref = weakref.ref(self)
        expected = self.file_path

        def done(future):
            if future.cancelled():
                return
            try:
                summary = future.result()
            except Exception:
                logger.exception("Queue probe failed")
                summary = None
            GLib.idle_add(_apply_summary, row_ref, expected, summary)

        from utils.file_info import get_queue_summary

        self._summary_future = pool.submit(get_queue_summary, expected)
        self._summary_future.add_done_callback(done)

    def dispose_thumbnail(self):
        if self._thumbnail_disposed:
            return
        self._thumbnail_disposed = True
        future, self._summary_future = self._summary_future, None
        if future is not None:
            future.cancel()
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


def _apply_summary(row_ref, expected, summary):
    row = row_ref()
    if row is None or row._thumbnail_disposed or expected != row.file_path or not summary:
        return GLib.SOURCE_REMOVE
    duration = summary.get("duration")
    if duration and duration > 0:
        row.duration_badge.set_text(format_clock(duration))
        row.duration_badge.set_visible(True)
    facts = []
    if summary.get("width") and summary.get("height"):
        facts.append(f"{summary['width']} × {summary['height']}")
    codec = summary.get("codec")
    if codec:
        facts.append(CODEC_NAMES.get(codec, codec.upper()))
    facts.append(row._size_text)
    row.meta_label.set_text(" · ".join(facts))
    return GLib.SOURCE_REMOVE


def has_own_settings(metadata) -> bool:
    """True when a video overrides the sidebar's profile, resolution or size."""
    metadata = normalize_metadata(metadata)
    return bool(metadata.get("preset_snapshot")) or any(
        metadata[key] != "global" for key in ("resolution_mode", "size_mode"))


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
        # GTK can recreate Python wrappers; own rows until their requests stop.
        self.queue_rows = []
        self._queue_render_id = None
        self._decision_dialogs = set()
        self.thumbnail_manager = ThumbnailManager(max_workers=2)
        self._thumbnail_finalizer = weakref.finalize(
            self, ThumbnailManager.shutdown, self.thumbnail_manager)
        # FFprobe for the rows' duration and codec: two at a time, cached.
        self.summary_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bvc-queue-probe")
        self._summary_finalizer = weakref.finalize(
            self, self.summary_pool.shutdown, wait=False, cancel_futures=True)

        self.page = self._create_page()

        # Connect settings after UI is created
        self._connect_settings()

    def get_page(self):
        """Return the page widget"""
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
        self.queue_listbox = Gtk.ListBox()
        self.queue_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.queue_listbox.set_show_separators(False)
        self.queue_listbox.set_vexpand(True)
        self.queue_listbox.add_css_class("bvc-queue-list")
        self.queue_listbox.connect("row-activated", self.on_queue_item_activated)

        self.placeholder = Adw.StatusPage()
        self.placeholder.add_css_class("card")
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

        folder_options_store = Gtk.StringList.new([
            _("Save in the same folder as the original file"),
            _("Folder to save"),
        ])
        # Long translations ellipsize instead of widening a narrow window.
        shrinking_labels = Gtk.SignalListItemFactory()
        shrinking_labels.connect("setup", lambda _factory, item: item.set_child(
            Gtk.Label(xalign=0, ellipsize=3)))
        shrinking_labels.connect("bind", lambda _factory, item: item.get_child().set_label(
            item.get_item().get_string()))
        self.folder_combo = Gtk.DropDown(model=folder_options_store, factory=shrinking_labels)
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
        self.output_folder_entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Output folder")])
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
        delete_label = Gtk.Label(label=_("Delete original files"), ellipsize=3)
        delete_label.set_tooltip_text(
            _("Originals are kept unless every converted result passes validation")
        )
        delete_box.append(delete_label)
        self.delete_original_check = Gtk.Switch()
        self.delete_original_check.set_valign(Gtk.Align.CENTER)
        self.delete_original_check.update_property([Gtk.AccessibleProperty.LABEL], [_("Delete original files")])
        delete_box.append(self.delete_original_check)
        options_box.append(delete_box)
        # The same clamp as the list, so both share their edges at any width.
        page.append(Adw.Clamp(maximum_size=1180, tightening_threshold=760, child=options_box))

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
        """Open the selected video without conflating row and button actions."""
        self.on_edit_file_by_path(row.file_path)

    def update_queue_display(self) -> None:
        """Update the queue display with current items"""
        logger.debug(
            f"DEBUG: update_queue_display called, queue length: {len(self.app.conversion_queue)}"
        )

        if self._queue_render_id is not None:
            GLib.source_remove(self._queue_render_id)
            self._queue_render_id = None

        # Clear existing items
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
                    thumbnail_manager=self.thumbnail_manager,
                    summary_text=self.file_recipe_summary(self.file_metadata.get(file_path)),
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
            # Only a row dragged from this list reorders it; text dropped
            # from elsewhere ("5") is not an index into the queue.
            if (self.dragged_row is None
                    or not 0 <= source_index < len(self.app.conversion_queue)):
                return False

            # Get target row
            target_row = self.queue_listbox.get_row_at_y(y)
            if not target_row:
                # Dropped below the last row: move it to the end.
                target_index = len(self.app.conversion_queue)
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

    def on_edit_file_by_path(self, file_path: str) -> None:
        """Open file in the video editor"""
        if file_path and os.path.exists(file_path):
            self.app.show_editor_for_file(file_path)
        else:
            logger.error(f"Error: Invalid file path for edit: {file_path}")
            self.app.show_error_dialog(_("Could not open this video file"))

    def on_remove_from_queue_by_path(self, file_path: str) -> None:
        """Remove a specific file from the queue"""
        self.app.remove_from_queue(file_path)
        self.update_queue_display()

    def on_play_file_by_path(self, file_path: str) -> None:
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

    def _encoding_environment(self, gpu_override=None, settings=None):
        """Hardware and encoder settings for a job that re-encodes video.

        Copy mode needs none of this, but the dialog that offers to re-encode
        instead of copying does, and it must land on the accelerator the user
        actually chose rather than falling back to the processor.
        """
        settings = settings or self.app.settings_manager
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

        try:
            file_metadata = deepcopy(self.file_metadata.get(input_file, {}))
            active_preset = self.app.active_preset()
            preset_snapshot = file_metadata.get("preset_snapshot")
            if preset_snapshot and preset_snapshot.get("settings", {}).get("gpu", "auto") != "auto":
                gpu_override = None
            if preset_snapshot is None and active_preset is not None:
                preset_snapshot = snapshot_preset(active_preset)
            resolved = freeze(self.app.settings_manager.settings, file_metadata)
            settings = SettingsOverride(self.app.settings_manager, resolved["settings"])
        except (OSError, ValueError, PresetError) as error:
            self.app.show_error_dialog(str(error))
            return False

        # Load app settings for conversion
        try:
            # The script reads its settings from lowercase variables. An
            # inherited one (an exported "options" or "size_limit") would
            # silently change the job, so only the proxy variables pass.
            env_vars = {name: value for name, value in os.environ.items()
                        if not _SCRIPT_SETTING.fullmatch(name) or name in _PROXY_VARIABLES}

            # Check if force copy video is enabled
            force_copy_video_enabled = settings.get_boolean(
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
                env_vars.update(self._encoding_environment(gpu_override, settings))

            # Subtitle handling (works regardless of copy mode)
            env_vars["subtitle_extract"] = settings.load_setting(
                "subtitle-extract", "embedded"
            )

            # The widgets already carry a preset's structured choices; the
            # file adds what has no widget: per-encoder arguments and the
            # preset's own FFmpeg options.
            env_vars.pop("preset_file", None)
            for key in ("force_copy_video", "video_resolution", "video_fps", "video_stabilize", "source_hdr",
                        "audio_bitrate", "audio_channels", "normalize_enabled"):
                env_vars[key] = ""
            if force_copy_video_enabled:
                env_vars["force_copy_video"] = "1"

            # Audio handling - Check if video has audio streams
            audio_handling = settings.load_setting(
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
                video_resolution = settings.load_setting(
                    "video-resolution", ""
                )
                if video_resolution:
                    env_vars["video_resolution"] = video_resolution
                env_vars["video_fps"] = settings.load_setting("video-fps", "")
                if file_metadata.get("stabilize"):
                    env_vars["video_stabilize"] = "1"
                if file_metadata.get("source_hdr", "auto") != "auto":
                    env_vars["source_hdr"] = file_metadata["source_hdr"]
            else:
                logger.debug("Copy mode enabled - skipping video_resolution")

            # Set flags
            if settings.get_boolean("gpu-partial", False):
                env_vars["gpu_partial"] = "1"
            if force_copy_video_enabled:
                env_vars["force_copy_video"] = "1"
            if settings.get_boolean(
                "only-extract-subtitles", False
            ):
                env_vars["only_extract_subtitles"] = "1"

            # Noise reduction
            sm = settings
            if sm.get_boolean("noise-reduction", False):
                env_vars["noise_reduction"] = "1"

                # Core NR parameters
                env_vars["noise_strength"] = str(
                    sm.load_setting("noise-reduction-strength", 1.0)
                )
                env_vars["noise_model"] = str(sm.load_setting("noise-model", 0))

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
            audio_bitrate = settings.load_setting(
                "audio-bitrate", ""
            )
            if audio_bitrate:
                env_vars["audio_bitrate"] = audio_bitrate
            audio_channels = settings.load_setting(
                "audio-channels", ""
            )
            if audio_channels:
                env_vars["audio_channels"] = audio_channels
            audio_codec = settings.load_setting(
                "audio-codec", "aac"
            )
            if audio_codec:
                env_vars["audio_codec"] = audio_codec

            # Get per-file metadata for this file
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
                settings,
                {
                    "preview-crop-left": crop_left,
                    "preview-crop-right": crop_right,
                    "preview-crop-top": crop_top,
                    "preview-crop-bottom": crop_bottom,
                    "preview-brightness": file_metadata.get("brightness", 0.0),
                    "preview-contrast": file_metadata.get("contrast", 0.0),
                    "preview-saturation": file_metadata.get("saturation", 1.0),
                    "preview-hue": file_metadata.get("hue", 0.0),
                    "preview-rotation": file_metadata.get("rotation", 0),
                    "preview-flip-h": file_metadata.get("flip_h", False),
                    "preview-flip-v": file_metadata.get("flip_v", False),
                    "preview-denoise": file_metadata.get("denoise", "off"),
                    "preview-sharpen": file_metadata.get("sharpen", "off"),
                    "preview-effect-file": file_metadata.get("effect_file", ""),
                },
            )

            # Cropping needs the frame size. Answered from the probe cache
            # the queue warmed off the main thread; a cache miss still
            # probes synchronously.
            video_width = video_height = None
            if crop_left > 0 or crop_right > 0 or crop_top > 0 or crop_bottom > 0:
                from utils.file_info import get_video_dimensions

                video_width, video_height = get_video_dimensions(input_file)
                logger.debug(
                    f"Using crop values: left={crop_left}, right={crop_right}, top={crop_top}, bottom={crop_bottom}"
                )

            # The unified video filter string from the per-file values, also
            # used when a copy job is re-encoded after all.
            try:
                video_filter = get_ffmpeg_filter_string(
                    filter_settings,
                    video_width=video_width,
                    video_height=video_height,
                )
            except CropError as error:
                # Converting without the crop the user asked for is a wrong
                # result, not a fallback.
                logger.error(f"Crop not applicable: {error}")
                self.app.show_error_dialog(
                    _("The crop cannot be applied"),
                    _("“{name}” was not converted: its picture size could not be read, "
                      "or the crop removes the whole picture. Check the crop in the "
                      "editor and try again.").format(name=os.path.basename(input_file)))
                return False

            # Skip video filters when in copy mode since filters require re-encoding
            env_vars["video_filter"] = ""
            if not force_copy_video_enabled:
                env_vars["video_filter"] = video_filter
                if video_filter:
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
            raw_additional_options = settings.load_setting(
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

            env_vars["options"] = additional_options
            if additional_options:
                logger.debug(f"Setting options={additional_options}")

            if video_width is not None and video_height is not None:
                env_vars["video_width"] = str(video_width)
                env_vars["video_height"] = str(video_height)

        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Error setting up conversion environment: {e}")
            import traceback

            traceback.print_exc()
            return False

        # Get the extension of the selected output format
        output_ext = {0: ".mp4", 1: ".mkv", 2: ".mov", 3: ".webm"}.get(
            settings.load_setting("output-format-index", 0), ".mp4")
        output_format = output_ext.lstrip(".")

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

        job_info = next((info for info in getattr(self.app, "active_conversions", [])
                         if info.get("file_path") == input_file), {})
        claimed = self.app.claimed_outputs

        # Find a name neither on disk nor reserved by a parallel job
        full_output_path = os.path.join(output_folder, output_basename)
        if os.path.exists(full_output_path) or full_output_path in claimed:
            # Find an available filename by adding a counter
            base_name = os.path.splitext(output_basename)[0]
            extension = os.path.splitext(output_basename)[1]
            counter = 1
            while True:
                output_basename = f"{base_name}_{counter}{extension}"
                full_output_path = os.path.join(output_folder, output_basename)
                if not os.path.exists(full_output_path) and full_output_path not in claimed:
                    logger.debug(
                        f"Output file exists, using alternative name: {output_basename}"
                    )
                    break
                counter += 1

        # Reserved until conversion_completed releases it with the job.
        if job_info:
            job_info["output_path"] = full_output_path
            claimed.add(full_output_path)
        # Set the full path as output_file
        env_vars["output_file"] = full_output_path

        logger.debug(f"Full output path: {full_output_path}")

        # Set the output format
        env_vars["output_format"] = output_format

        cmd = [CONVERT_SCRIPT_PATH, input_file]

        # Delete original setting
        delete_original = self.delete_original_check.get_active()

        job_id = job_info.get("job_id")
        cancel_event = job_info.get("cancel_event") or threading.Event()

        # Check MP4 compatibility when copying without reencoding to MP4
        force_copy_video = env_vars.get("force_copy_video") == "1"
        # Re-encoding uses the settings copy mode had skipped, the accelerator
        # included: choosing "re-encode" is not a request to fall back to the
        # processor. Offered when MP4 refuses the copied tracks, and taken by
        # a trim whose cut is not on a keyframe (utils/segment_batch.py).
        reencode_env = None
        if force_copy_video:
            reencode_env = dict(env_vars)
            reencode_env.pop("force_copy_video", None)
            reencode_env.pop("force_software", None)
            reencode_env.update(self._encoding_environment(gpu_override, settings))
            video_resolution = settings.load_setting("video-resolution", "")
            if video_resolution:
                reencode_env["video_resolution"] = video_resolution
            reencode_env["video_fps"] = settings.load_setting("video-fps", "")
            if file_metadata.get("stabilize"):
                reencode_env["video_stabilize"] = "1"
            if file_metadata.get("source_hdr", "auto") != "auto":
                reencode_env["source_hdr"] = file_metadata["source_hdr"]
            reencode_env["video_filter"] = video_filter
        conversion_context = {
            "preset_source": preset_snapshot["source"] if preset_snapshot else None,
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
            "reencode_env": reencode_env,
        }
        # A size target decides copy or encode itself, so it comes before the
        # copy-mode MP4 check.
        size_target = self._size_target(settings)
        if size_target is not None:
            conversion_context.update(size_target=size_target,
                                      encode_env=reencode_env or env_vars)
            return self._start_size_job(conversion_context)
        if force_copy_video and output_format == "mp4":
            from utils.file_info import check_mp4_compatibility

            is_compatible, incompatible_streams = check_mp4_compatibility(input_file)
            if not is_compatible:
                # Package all needed variables
                def show_compatibility_warning() -> None:
                    if cancel_event.is_set():
                        self.app.conversion_completed(False, file_path=input_file, job_id=job_id)
                        return
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

                    if hasattr(dialog, "set_prefer_wide_layout"):  # libadwaita 1.6
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
                    # Quitting closes it: the job it holds must not keep the
                    # application waiting for an answer nobody can give.
                    self._decision_dialogs.add(dialog)
                    dialog.connect("closed", self._decision_dialogs.discard)
                    dialog.present(self.app.window)

                # Show dialog in main thread
                GLib.idle_add(show_compatibility_warning)
                return True  # The active job remains reserved while awaiting a decision.

        return self._continue_conversion(conversion_context)

    def close_decision_dialogs(self) -> None:
        """Answer every open question about a job with its close response."""
        for dialog in list(self._decision_dialogs):
            dialog.force_close()

    def additional_options_error(self, files) -> str | None:
        """Why the FFmpeg options of a queued file are rejected, if they are."""
        for path in files:
            try:
                resolved = freeze(self.app.settings_manager.settings,
                                  deepcopy(self.file_metadata.get(path, {})))
            except (OSError, ValueError, PresetError):
                continue  # The job reports its own recipe error.
            settings = SettingsOverride(self.app.settings_manager, resolved["settings"])
            accepted, message = validate_additional_options(
                settings.load_setting("additional-options", ""))
            if not accepted:
                return message
        return None

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

    def _size_target(self, settings):
        """{"bytes", "strategy"} when converted videos must fit a size, else None."""
        target_id = settings.load_setting("size-target", "")
        if not target_id:
            return None
        return {"bytes": target_bytes(target_id, float(settings.load_setting("size-target-mb", 50))),
                "strategy": settings.load_setting("size-strategy", "auto")}

    def _start_size_job(self, context):
        """Plan off the GTK thread (it probes the file), then start the job."""
        input_file, job_id = context["input_file"], context.get("job_id")

        def dispatch(job):
            try:
                if job["route"] == "batch":
                    start_segment_batch(self, job)
                else:
                    self._continue_conversion(job)
            except (OSError, ValueError) as error:
                logger.exception("Size job could not start")
                fail(str(error))
            return GLib.SOURCE_REMOVE

        def fail(message):
            self.app.show_error_dialog(_("This video cannot be made to fit the size"), message)
            self.app.conversion_completed(False, file_path=input_file, job_id=job_id)
            return GLib.SOURCE_REMOVE

        def plan():
            try:
                job = prepare_size_job(context)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                logger.warning("Size target not applied: %s", error)
                GLib.idle_add(fail, str(error))
                return
            GLib.idle_add(dispatch, job)

        threading.Thread(target=plan, daemon=True).start()
        return True

    def _continue_conversion(self, context):
        """Continue with the actual conversion process"""
        # The naming and segment fields are read by start_segment_batch, which
        # takes the whole context; only these are used here.
        cmd = context["cmd"]
        env_vars = context["env_vars"]
        delete_original = context["delete_original"]
        input_file = context["input_file"]
        trim_segments = context["trim_segments"]

        logger.debug(f"Conversion command: {' '.join(cmd)}")
        for key in ("gpu", "video_quality", "video_encoder", "preset", "subtitle_extract",
                    "audio_handling", "audio_bitrate", "audio_channels", "video_resolution",
                    "video_fps", "video_stabilize", "source_hdr",
                    "options", "gpu_partial", "force_copy_video", "only_extract_subtitles",
                    "video_filter", "video_width", "video_height"):
            if key in env_vars:
                logger.debug(f"{key}={env_vars[key]}")

        if context.get("cancel_event") is not None and context["cancel_event"].is_set():
            self.app.conversion_completed(False, file_path=input_file, job_id=context.get("job_id"))
            return False
        # Several cuts go to a segment batch, and so does one cut copied
        # without re-encoding: the batch checks that it starts on a keyframe
        # and re-encodes it otherwise.
        if len(trim_segments) > 1 or (trim_segments and env_vars.get("force_copy_video") == "1"):
            return start_segment_batch(self, context)

        # Single segment or no segments - use standard conversion
        # Calculate segment duration for single-segment trimming for accurate progress
        segment_duration = None
        if len(trim_segments) == 1:
            trim_start = trim_segments[0]["start"]
            segment_duration = trim_segments[0]["end"] - trim_start
            trim = f"-t {self._format_time_ffmpeg(segment_duration)}"
            if trim_start > 0:
                trim = f"-ss {self._format_time_ffmpeg(trim_start)} {trim}"
            env_vars = {**env_vars, "options": " ".join(
                filter(None, [env_vars.get("options", "").strip(), trim]))}
            logger.debug(f"Single segment trim: {trim}")

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
            preset_source=context.get("preset_source"),
            job_id=context.get("job_id"), cancel_event=context.get("cancel_event"),
        )

        return True

    def _format_time_ffmpeg(self, seconds):
        """HH:MM:SS.ffffff for ffmpeg, rounded to the microsecond FFmpeg keeps.

        Players hand over exact cut positions; truncating 3.3 (stored as
        3.2999…) to milliseconds cut a frame early.
        """
        whole, micro = divmod(round(seconds * 1_000_000), 1_000_000)
        return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}.{micro:06d}"

    def _on_output_folder_entry_changed(self, entry) -> None:
        """Persist the destination folder while "In one folder" is selected."""
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

    def on_show_file_info_by_path(self, file_path: str) -> None:
        """Show detailed information about the video file"""
        if file_path and os.path.exists(file_path):
            from utils.file_info import VideoInfoDialog

            info_dialog = VideoInfoDialog(self.app.window, file_path)
            info_dialog.show()
        else:
            logger.error(f"Error: Invalid file path: {file_path}")
            self.app.show_error_dialog(_("Could not find this video file"))

    def refresh_recipe_summaries(self) -> None:
        row = self.queue_listbox.get_first_child()
        while row is not None:
            if isinstance(row, FileQueueRow):
                metadata = self.file_metadata.get(row.file_path)
                row.set_recipe(self.file_recipe_summary(metadata), has_own_settings(metadata))
            row = row.get_next_sibling()

    def file_recipe_summary(self, metadata) -> str:
        metadata = normalize_metadata(metadata)
        settings = freeze(self.app.settings_manager.settings, metadata)["settings"]
        preset = metadata.get("preset_snapshot") or {}
        name = preset.get("name") or _("General settings")
        resolution = settings.get("video-resolution") or _("Original resolution")
        if settings.get("force-copy-video"):
            resolution = _("Original resolution")
        target_id = settings.get("size-target", "")
        if target_id:
            # The size decides the resolution, so the limit takes its place.
            resolution = _("up to {size}").format(
                size=shown_size(target_id, settings.get("size-target-mb", 50.0)))
        return _("{profile} · {resolution}").format(profile=name, resolution=resolution)

    def on_file_options_by_path(self, file_path: str) -> None:
        """Choose a preset and resolution for one queue item."""

        from utils.presets import list_presets

        metadata = normalize_metadata(self.file_metadata.get(file_path))
        try:
            presets = [snapshot_preset(preset) for preset in list_presets()]
        except (OSError, ValueError, PresetError) as error:
            self.app.show_error_dialog(str(error))
            return
        saved_preset = metadata.get("preset_snapshot")
        if saved_preset:
            presets = [saved_preset] + [preset for preset in presets
                                      if preset["id"] != saved_preset["id"]]

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
            if preset["id"] == (saved_preset or {}).get("id", metadata.get("preset_id")):
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
        size_labels = [_("Use the general size"), _("No limit"),
                       *(f"{t[3]} · {_(t[1])}" for t in TARGETS), _("Another size")]
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
        size_spin.update_property([Gtk.AccessibleProperty.LABEL], [_("Size in megabytes")])
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
        # Each spin step used to start its own ffprobe; wait for a pause.
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
            temporary["preset_snapshot"] = (
                presets[selected - 1]
                if selected
                else None
            )
            preview_value.set_text(self.file_recipe_summary(temporary))
            refresh_size_result(temporary)

        def refresh_size_result(temporary):
            size_generation[0] += 1
            generation = size_generation[0]
            settings = freeze(self.app.settings_manager.settings, temporary)["settings"]
            if not settings.get("size-target"):
                size_result.set_visible(False)
                return
            size_result.set_visible(True)
            size_result.set_subtitle(_("Checking the video…"))
            output_ext = {0: ".mp4", 1: ".mkv", 2: ".mov", 3: ".webm"}.get(
                settings.get("output-format-index", 0), ".mp4")
            context = {
                "size_target": self._size_target(SettingsOverride(self.app.settings_manager, settings)),
                "input_file": file_path, "output_ext": output_ext,
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
