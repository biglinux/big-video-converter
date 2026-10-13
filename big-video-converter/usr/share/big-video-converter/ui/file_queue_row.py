"""A queued video row: preview, recipe summary, probe facts and actions."""

import os
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import gettext
import logging

from engine.job_options import normalize_metadata
from engine.presets import CODEC_NAMES
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ui.progress_page import format_clock, format_size, launch_file

logger = logging.getLogger(__name__)

_ = gettext.gettext


class FileQueueRow(Gtk.ListBoxRow):
    """A queued video: preview, the settings it will use, and its actions."""

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
        thumbnail_cache,
        summary_text,
        app,
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
        self.thumbnail_cache = thumbnail_cache
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
            folder = "~" + folder[len(home) :]
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
        options_button.set_tooltip_text(
            _("Choose a preset or resolution for this video")
        )
        options_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_("Options for “{name}”").format(name=name)],
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
        box.append(
            self._menu_action(
                _("Play video"),
                "media-playback-start-symbolic",
                lambda _b: row.on_play_callback(row.file_path),
            )
        )
        box.append(
            self._menu_action(
                _("Video information"),
                "dialog-information-symbolic",
                lambda _b: row.on_info_callback(row.file_path),
            )
        )
        box.append(
            self._menu_action(
                _("Open containing folder"),
                "folder-open-symbolic",
                lambda _b: row._on_open_folder(),
            )
        )
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(4)
        separator.set_margin_bottom(4)
        box.append(separator)
        box.append(
            self._menu_action(
                _("Remove from queue"),
                "list-remove-symbolic",
                lambda _b: row.on_remove_callback(row.file_path),
                destructive=True,
            )
        )
        box.append(
            self._menu_action(
                _("Delete from disk…"),
                "user-trash-symbolic",
                lambda _b: row._on_delete_from_disk(),
                destructive=True,
            )
        )
        popover.set_child(box)
        return popover

    def _request_thumbnail(self):
        row_ref = weakref.ref(self)
        expected = self.file_path

        def ready(thumbnail_path):
            row = row_ref()
            if row is not None:
                GLib.idle_add(row._apply_thumbnail, expected, thumbnail_path)

        self._thumbnail_request = self.thumbnail_cache.request(self.file_path, ready)

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
            [Gtk.AccessibleProperty.LABEL], [_("Video preview")]
        )
        return GLib.SOURCE_REMOVE

    def set_recipe(self, summary_text: str, custom: bool) -> None:
        text = _("Own: {recipe}").format(recipe=summary_text)
        self.recipe_label.set_text(text)
        self.recipe_label.set_tooltip_text(text)
        self.recipe_label.set_visible(custom)

    def _request_summary(self, pool) -> None:
        """Read size, duration and codec on a worker; the probe is cached."""
        if pool is None:
            return
        row_ref = weakref.ref(self)
        expected = self.file_path

        def done(future):
            if future.cancelled():
                return
            try:
                answer = future.result()
            except Exception:
                logger.exception("Queue probe failed")
                return
            GLib.idle_add(_apply_summary, row_ref, expected, answer)

        self._summary_future = pool.submit(_queue_row_facts, expected)
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
        if os.path.isfile(self.file_path):
            launch_file(self.app, self.app.window, self.file_path, containing=True)

    def _on_delete_from_disk(self):
        if not os.path.isfile(self.file_path):
            return
        if not self.app.settings_store.load_setting("skip-trash", False):
            self._on_move_to_trash()
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
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
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
                    _("Could not delete the video: {}").format(error)
                )

        dialog.connect("response", response)
        dialog.present(self.app.window)

    def _on_move_to_trash(self):
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Move this video to the Trash?"))
        dialog.set_body(
            _("“{}” will be moved to the Trash.").format(
                os.path.basename(self.file_path)
            )
        )
        dialog.add_response("cancel", _("Keep video"))
        dialog.add_response("trash", _("Move to Trash"))
        dialog.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        path = self.file_path

        def trashed(file, result):
            try:
                file.trash_finish(result)
            except GLib.Error as error:
                self.app.show_error_dialog(
                    _("Could not move the video to the Trash: {}").format(error.message)
                )
                return
            self.on_remove_callback(path)

        def response(_dialog, response_id):
            if response_id == "trash":
                Gio.File.new_for_path(path).trash_async(
                    GLib.PRIORITY_DEFAULT, None, trashed
                )

        dialog.connect("response", response)
        dialog.present(self.app.window)


def _queue_row_facts(file_path):
    """The file's size and probe summary, or None once the file is gone."""
    from engine.file_info import get_queue_summary

    try:
        size = os.path.getsize(file_path)
    except OSError:
        return None
    return size, get_queue_summary(file_path)


def _apply_summary(row_ref, expected, answer):
    row = row_ref()
    if row is None or row._thumbnail_disposed or expected != row.file_path:
        return GLib.SOURCE_REMOVE
    if answer is None:
        # Moved or deleted since it was queued: say so instead of hiding a
        # file the queue still counts and will report as failed.
        row.meta_label.set_text(_("File not found"))
        row.meta_label.add_css_class("error")
        return GLib.SOURCE_REMOVE
    size, summary = answer
    summary = summary or {}
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
    facts.append(format_size(size))
    row.meta_label.set_text(" · ".join(facts))
    return GLib.SOURCE_REMOVE
