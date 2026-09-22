"""Presets: a searchable grid of recipes, plus import and AI-assisted creation.

The grid shows bundled presets and the user's own files side by side. A click
applies the preset through the application (ProfileManagerMixin.apply_preset),
which writes the settings and refreshes every widget, so what the user sees in
the sidebar and the dialogs is exactly what the preset asked for.
"""

import gettext
import logging
import os
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from utils import presets as preset_store
from utils.ffmpeg_path import get_ffmpeg_executable

logger = logging.getLogger(__name__)
_ = gettext.gettext

_CODEC_LABELS = {"copy": _("Copy"), "h264": "H.264", "h265": "H.265", "av1": "AV1", "vp9": "VP9", "prores": "ProRes"}


def _matches(preset, query: str) -> bool:
    """Case-insensitive match on name, description, tags and codec."""
    query = (query or "").strip().lower()
    if not query:
        return True
    haystack = " ".join([preset.name, preset.display_name, preset.description, preset.display_description,
                         " ".join(preset.tags), " ".join(preset.display_tags),
                         preset.video.get("codec", ""), preset.container.get("format", "")]).lower()
    return all(word in haystack for word in query.split())


def _chip(text: str, css: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class("caption")
    label.add_css_class("chip")
    if css:
        label.add_css_class(css)
    label.set_margin_end(4)
    return label


class _PresetCard(Gtk.FlowBoxChild):
    def __init__(self, preset, active: bool, on_menu):
        super().__init__()
        self.preset = preset
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("card")
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("top", "bottom", "start", "end"):
            getattr(inner, f"set_margin_{side}")(12)
        box.append(inner)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label=preset.display_name, xalign=0)
        title.add_css_class("heading")
        title.set_wrap(True)
        title.set_hexpand(True)
        header.append(title)
        if active:
            check = Gtk.Image.new_from_icon_name("object-select-symbolic")
            check.add_css_class("accent")
            check.set_tooltip_text(_("In use"))
            header.append(check)
        menu = Gtk.MenuButton()
        menu.set_icon_name("view-more-symbolic")
        menu.add_css_class("flat")
        menu.set_valign(Gtk.Align.START)
        menu.set_popover(on_menu(preset))
        header.append(menu)
        inner.append(header)

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        codec = preset.video.get("codec")
        if codec:
            chips.append(_chip(_CODEC_LABELS.get(codec, codec), "accent"))
        if preset.video.get("resolution"):
            chips.append(_chip(preset.video["resolution"]))
        if preset.container.get("format"):
            chips.append(_chip(preset.container["format"].upper()))
        chips.append(_chip(_("Bundled") if preset.bundled else _("Yours"), "success" if not preset.bundled else "dim-label"))
        inner.append(chips)

        if preset.description:
            desc = Gtk.Label(label=preset.display_description, xalign=0)
            desc.set_wrap(True)
            desc.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            desc.set_lines(4)
            desc.set_ellipsize(Pango.EllipsizeMode.END)
            desc.add_css_class("dim-label")
            desc.set_valign(Gtk.Align.START)
            desc.set_vexpand(True)
            inner.append(desc)
        if preset.tags:
            tags = Gtk.Label(label="#" + "  #".join(preset.display_tags[:6]), xalign=0)
            tags.add_css_class("caption")
            tags.add_css_class("dim-label")
            tags.set_ellipsize(Pango.EllipsizeMode.END)
            inner.append(tags)
        self.set_child(box)
        self.set_tooltip_text(_("Click to use this preset"))


class PresetsDialog:
    """Owns the grid, the search box and the actions; ``dialog`` is the widget."""

    def __init__(self, parent_window, app):
        self.app = app
        self.parent_window = parent_window
        self.dialog = Adw.Dialog()
        self.dialog.set_title(_("Presets"))
        self.dialog.set_content_width(920)
        self.dialog.set_content_height(680)
        self.dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        ai_button = Gtk.Button(label=_("Create with AI…"))
        ai_button.add_css_class("suggested-action")
        ai_button.connect("clicked", lambda *_a: show_ai_preset_dialog(self.dialog, app, self.refresh))
        header.pack_start(ai_button)

        add_menu = Gio.Menu()
        add_menu.append(_("Import file…"), "presets.import")
        add_menu.append(_("Paste from clipboard"), "presets.paste")
        add_menu.append(_("Open presets folder"), "presets.folder")
        add_button = Gtk.MenuButton()
        add_button.set_icon_name("list-add-symbolic")
        add_button.set_tooltip_text(_("Add a preset"))
        add_button.set_menu_model(add_menu)
        header.pack_end(add_button)
        toolbar.add_top_bar(header)

        actions = Gio.SimpleActionGroup()
        for name, callback in (("import", self._import_file), ("paste", self._paste_clipboard),
                               ("folder", self._open_folder)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            actions.add_action(action)
        self.dialog.insert_action_group("presets", actions)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("start", "end"):
            getattr(content, f"set_margin_{side}")(16)
        content.set_margin_top(8)
        content.set_margin_bottom(16)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(_("Search by name, tag or codec…"))
        self.search.connect("search-changed", lambda *_a: self.flowbox.invalidate_filter())
        content.append(self.search)

        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        content.append(self.banner)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flowbox.set_homogeneous(True)
        self.flowbox.set_min_children_per_line(2)
        self.flowbox.set_max_children_per_line(3)
        self.flowbox.set_row_spacing(4)
        self.flowbox.set_column_spacing(4)
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_filter_func(lambda child: _matches(child.preset, self.search.get_text()))
        self.flowbox.connect("child-activated", self._on_card_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.flowbox)
        content.append(scroll)

        self.empty = Adw.StatusPage()
        self.empty.set_icon_name("folder-videos-symbolic")
        self.empty.set_title(_("No presets"))
        self.empty.set_description(_("Import a file, paste one from the clipboard or create one with AI."))
        self.empty.set_visible(False)
        content.append(self.empty)

        self.toasts.set_child(content)
        toolbar.set_content(self.toasts)
        self.dialog.set_child(toolbar)
        self.refresh()

    # ── grid ──

    def refresh(self):
        child = self.flowbox.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.flowbox.remove(child)
            child = following
        active_id = self.app.settings_manager.load_setting("active-preset", "")
        self.presets = preset_store.list_presets()
        for preset in self.presets:
            self.flowbox.append(_PresetCard(preset, preset.id == active_id, self._card_menu))
        self.empty.set_visible(not self.presets)
        broken = preset_store.broken_presets()
        if broken:
            names = ", ".join(os.path.basename(path) for path, _reason in broken[:3])
            self.banner.set_title(_("Ignored invalid preset file(s): {0}").format(names))
            self.banner.set_button_label(_("Details"))
            self.banner.connect("button-clicked", lambda *_a: self._show_broken(broken))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        self.flowbox.invalidate_filter()

    def visible_presets(self):
        return [p for p in self.presets if _matches(p, self.search.get_text())]

    def _on_card_activated(self, _flowbox, child):
        self.apply(child.preset)

    def apply(self, preset):
        try:
            self.app.apply_preset(preset)
        except Exception as error:  # a broken widget must not lose the dialog
            logger.exception("Could not apply preset")
            self._toast(_("Could not apply the preset: {0}").format(error))
            return
        self.refresh()
        self._toast(_("Preset “{0}” is now in use.").format(preset.display_name))

    # ── per-card menu ──

    def _card_menu(self, preset):
        menu = Gio.Menu()
        menu.append(_("Duplicate for editing"), f"card.duplicate::{preset.id}")
        menu.append(_("Export…"), f"card.export::{preset.id}")
        if not preset.bundled:
            menu.append(_("Open in text editor"), f"card.edit::{preset.id}")
            menu.append(_("Delete"), f"card.delete::{preset.id}")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        group = Gio.SimpleActionGroup()
        for name, callback in (("duplicate", self._duplicate), ("export", self._export),
                               ("edit", self._edit), ("delete", self._delete)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, param, cb=callback: cb(self._by_id(param.get_string())))
            group.add_action(action)
        popover.insert_action_group("card", group)
        return popover

    def _by_id(self, preset_id):
        return next((p for p in self.presets if p.id == preset_id), None)

    def _duplicate(self, preset):
        if preset is None:
            return
        try:
            copy = preset_store.duplicate_preset(preset)
        except (preset_store.PresetError, OSError) as error:
            self._toast(str(error))
            return
        self.refresh()
        self._toast(_("Saved as “{0}”. Edit it in {1}").format(copy.name, copy.path))

    def _delete(self, preset):
        if preset is None:
            return
        try:
            preset_store.delete_user_preset(preset)
        except (preset_store.PresetError, OSError) as error:
            self._toast(str(error))
            return
        if self.app.settings_manager.load_setting("active-preset", "") == preset.id:
            self.app.settings_manager.save_setting("active-preset", "")
            if hasattr(self.app, "_select_profile_radio"):
                self.app._select_profile_radio(self.app._detect_current_profile())
            if hasattr(self.app, "_update_presets_subtitle"):
                self.app._update_presets_subtitle()
        self.refresh()
        self._toast(_("Preset “{0}” deleted.").format(preset.display_name))

    def _edit(self, preset):
        if preset is None:
            return
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(preset.path).get_uri(), None)

    def _export(self, preset):
        if preset is None:
            return
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title(_("Export Preset"))
        file_dialog.set_initial_name(preset.id + ".toml")

        def done(dlg, result):
            try:
                gfile = dlg.save_finish(result)
            except GLib.Error:
                return
            if gfile is None:
                return
            try:
                with open(preset.path, "rb") as source, open(gfile.get_path(), "wb") as target:
                    target.write(source.read())
            except OSError as error:
                self._toast(str(error))
                return
            self._toast(_("Preset exported to {0}").format(gfile.get_path()))

        file_dialog.save(self.parent_window, None, done)

    # ── adding presets ──

    def _import_file(self):
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title(_("Import Preset"))
        toml_filter = Gtk.FileFilter()
        toml_filter.set_name(_("Preset files (*.toml)"))
        toml_filter.add_pattern("*.toml")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(toml_filter)
        file_dialog.set_filters(filters)

        def done(dlg, result):
            try:
                gfile = dlg.open_finish(result)
            except GLib.Error:
                return
            if gfile is None:
                return
            try:
                with open(gfile.get_path(), "r", encoding="utf-8") as handle:
                    self.import_text(handle.read())
            except (OSError, UnicodeDecodeError) as error:
                self._toast(str(error))

        file_dialog.open(self.parent_window, None, done)

    def _paste_clipboard(self):
        clipboard = Gdk.Display.get_default().get_clipboard()

        def done(clip, result):
            try:
                text = clip.read_text_finish(result)
            except GLib.Error as error:
                self._toast(str(error))
                return
            self.import_text(text or "")

        clipboard.read_text_async(None, done)

    def import_text(self, text: str):
        """Validate and store TOML text as a user preset; returns the Preset or None."""
        try:
            preset = preset_store.save_user_preset(text)
        except (preset_store.PresetError, OSError) as error:
            self._toast(_("The preset was not imported: {0}").format(error))
            return None
        self.refresh()
        self._toast(_("Preset “{0}” imported.").format(preset.name))
        return preset

    def _open_folder(self):
        directory = preset_store.user_dir()
        os.makedirs(directory, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(directory).get_uri(), None)

    def _show_broken(self, broken):
        alert = Adw.AlertDialog()
        alert.set_heading(_("Invalid preset files"))
        alert.set_body("\n\n".join(f"{path}\n{reason}" for path, reason in broken))
        alert.add_response("ok", _("OK"))
        alert.present(self.dialog)

    def _toast(self, message: str):
        self.toasts.add_toast(Adw.Toast.new(message))

    def present(self):
        self.dialog.present(self.parent_window)


def show_presets_dialog(parent_window, app) -> PresetsDialog:
    dialog = PresetsDialog(parent_window, app)
    dialog.present()
    return dialog


# ── AI-assisted creation ─────────────────────────────────────────────────────

def _ffmpeg_version() -> str:
    try:
        out = subprocess.run([get_ffmpeg_executable(), "-version"], capture_output=True, text=True, timeout=5)
        first = (out.stdout or "").splitlines()[0] if out.stdout else ""
        return first.replace("ffmpeg version ", "").split(" Copyright")[0] or "unknown"
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def build_prompt(request: str) -> str:
    """The text placed on the clipboard for a chat assistant."""
    from utils.gpu_selector import _available_ffmpeg_encoders

    return preset_store.build_ai_prompt(request, available_encoders=_available_ffmpeg_encoders(),
                                        ffmpeg_version=_ffmpeg_version())


def copy_to_clipboard(text: str) -> None:
    Gdk.Display.get_default().get_clipboard().set(text)


class AiPresetDialog:
    """Step one: describe and copy the prompt. Step two: paste the answer, save."""

    def __init__(self, parent, app, on_saved=None):
        self.app = app
        self.on_saved = on_saved
        self.dialog = Adw.Dialog()
        self.dialog.set_title(_("Create a preset with AI"))
        self.dialog.set_content_width(720)
        self.dialog.set_content_height(700)
        self.dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)
        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("start", "end", "bottom"):
            getattr(content, f"set_margin_{side}")(20)
        content.set_margin_top(8)

        intro = Gtk.Label(xalign=0)
        intro.set_wrap(True)
        intro.add_css_class("dim-label")
        intro.set_text(_(
            "Describe what you need in your own words. The program turns it into a prompt "
            "that any online AI assistant understands, with the exact preset format and the "
            "encoders available on this computer. Paste the assistant's answer back here."))
        content.append(intro)

        step1 = Gtk.Label(label=_("1. What should the preset do?"), xalign=0)
        step1.add_css_class("heading")
        content.append(step1)
        self.request = Gtk.TextView()
        self.request.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.request.set_top_margin(8)
        self.request.set_bottom_margin(8)
        self.request.set_left_margin(8)
        self.request.set_right_margin(8)
        self.request.set_accepts_tab(False)
        request_frame = Gtk.ScrolledWindow()
        request_frame.set_min_content_height(110)
        request_frame.add_css_class("card")
        request_frame.set_child(self.request)
        content.append(request_frame)
        hint = Gtk.Label(xalign=0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        hint.set_text(_("Example: “Vertical videos for TikTok, under 50 MB per minute, loud clear voice.”"))
        content.append(hint)

        copy_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.copy_button = Gtk.Button(label=_("Copy prompt"))
        self.copy_button.add_css_class("suggested-action")
        self.copy_button.connect("clicked", self._on_copy)
        copy_row.append(self.copy_button)
        copy_hint = Gtk.Label(label=_("Then paste it into ChatGPT, Gemini, Claude, Copilot or any other assistant."), xalign=0)
        copy_hint.add_css_class("dim-label")
        copy_hint.set_wrap(True)
        copy_row.append(copy_hint)
        content.append(copy_row)

        step2 = Gtk.Label(label=_("2. Paste the assistant's answer"), xalign=0)
        step2.add_css_class("heading")
        step2.set_margin_top(10)
        content.append(step2)
        self.answer = Gtk.TextView()
        self.answer.set_monospace(True)
        self.answer.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        for side in ("top", "bottom", "left", "right"):
            getattr(self.answer, f"set_{side}_margin")(8)
        answer_frame = Gtk.ScrolledWindow()
        answer_frame.set_min_content_height(180)
        answer_frame.set_vexpand(True)
        answer_frame.add_css_class("card")
        answer_frame.set_child(self.answer)
        content.append(answer_frame)

        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.save_button = Gtk.Button(label=_("Save preset"))
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", self._on_save)
        save_row.append(self.save_button)
        paste_button = Gtk.Button(label=_("Paste from clipboard"))
        paste_button.connect("clicked", self._on_paste)
        save_row.append(paste_button)
        content.append(save_row)

        self.status = Gtk.Label(xalign=0)
        self.status.set_wrap(True)
        self.status.set_selectable(True)
        content.append(self.status)

        self.toasts.set_child(content)
        toolbar.set_content(self.toasts)
        self.dialog.set_child(toolbar)
        self.parent = parent

    def request_text(self) -> str:
        buffer = self.request.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def answer_text(self) -> str:
        buffer = self.answer.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def _on_copy(self, *_args):
        prompt = build_prompt(self.request_text())
        copy_to_clipboard(prompt)
        self.toasts.add_toast(Adw.Toast.new(_("Prompt copied. Paste it into your AI assistant.")))

    def _on_paste(self, *_args):
        clipboard = Gdk.Display.get_default().get_clipboard()

        def done(clip, result):
            try:
                text = clip.read_text_finish(result)
            except GLib.Error as error:
                self._set_status(str(error), error=True)
                return
            self.answer.get_buffer().set_text(text or "")

        clipboard.read_text_async(None, done)

    def _on_save(self, *_args):
        self.save(self.answer_text())

    def save(self, text: str):
        """Validate the pasted TOML, store it, apply it; returns the Preset or None."""
        try:
            preset = preset_store.save_user_preset(text)
        except (preset_store.PresetError, OSError) as error:
            self._set_status(_("The preset was not saved: {0}").format(error), error=True)
            return None
        self._set_status(_("Saved as “{0}” in {1}").format(preset.name, preset.path))
        if hasattr(self.app, "apply_preset"):
            try:
                self.app.apply_preset(preset)
            except Exception:
                logger.exception("Could not apply the new preset")
        if self.on_saved:
            self.on_saved()
        return preset

    def _set_status(self, text: str, error: bool = False):
        self.status.set_text(text)
        if error:
            self.status.add_css_class("error")
        else:
            self.status.remove_css_class("error")

    def present(self):
        self.dialog.present(self.parent)


def show_ai_preset_dialog(parent, app, on_saved=None) -> AiPresetDialog:
    dialog = AiPresetDialog(parent, app, on_saved)
    dialog.present()
    return dialog
