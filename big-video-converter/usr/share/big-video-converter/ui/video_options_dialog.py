"""
Educational dialog for video encoding options.
Shows preset and resolution choices with SVG illustrations.
"""

import gettext
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

# Ensure parent package is importable
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import constants

_ = gettext.gettext

_ILLUSTRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "icons",
    "illustrations",
)


def _load_svg_picture(filename: str, size: int = 92) -> Gtk.Picture:
    path = os.path.join(_ILLUSTRATIONS_DIR, filename)
    picture = Gtk.Picture.new_for_filename(path)
    picture.set_size_request(size, int(size * 80 / 120))
    picture.set_can_shrink(True)
    picture.set_content_fit(Gtk.ContentFit.CONTAIN)
    return picture


def _make_card(svg: str, title: str, description: str, control: Gtk.Widget) -> Gtk.Box:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    card.add_css_class("card")
    card.set_margin_bottom(12)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    row.set_margin_top(16)
    row.set_margin_bottom(16)
    row.set_margin_start(16)
    row.set_margin_end(16)

    picture = _load_svg_picture(svg)
    picture.set_valign(Gtk.Align.CENTER)
    row.append(picture)

    text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    text_box.set_hexpand(True)
    text_box.set_valign(Gtk.Align.CENTER)

    lbl_title = Gtk.Label(label=title)
    lbl_title.add_css_class("heading")
    lbl_title.set_halign(Gtk.Align.START)
    lbl_title.set_wrap(True)
    text_box.append(lbl_title)

    lbl_desc = Gtk.Label(label=description)
    lbl_desc.set_wrap(True)
    lbl_desc.set_xalign(0)
    lbl_desc.add_css_class("dim-label")
    text_box.append(lbl_desc)

    row.append(text_box)

    control.set_valign(Gtk.Align.CENTER)
    row.append(control)

    card.append(row)
    return card


# ---------------------------------------------------------------------------
# Preset section
# ---------------------------------------------------------------------------

_PRESET_DATA = None


def _get_preset_data():
    global _PRESET_DATA
    if _PRESET_DATA is None:
        _PRESET_DATA = [
            {
                "svg": "preset_speed.svg",
                "title": _("Conversion Speed"),
                "description": _(
                    "Slower conversion produces smaller files. "
                    "Faster presets create larger files but finish sooner."
                ),
            },
        ]
    return _PRESET_DATA


# ---------------------------------------------------------------------------
# Resolution section
# ---------------------------------------------------------------------------

_RESOLUTION_DATA = None


def _get_resolution_data():
    global _RESOLUTION_DATA
    if _RESOLUTION_DATA is None:
        _RESOLUTION_DATA = [
            {
                "svg": "resolution.svg",
                "title": _("Output Resolution"),
                "description": _(
                    "Change the video size. 'Original' keeps it as-is. "
                    "Lower resolutions create smaller files."
                ),
            },
        ]
    return _RESOLUTION_DATA


# ---------------------------------------------------------------------------
# Dialog builder
# ---------------------------------------------------------------------------


def show_video_options_dialog(parent_window, app) -> None:
    """Present the educational video options dialog."""
    dialog = Adw.Dialog()
    dialog.set_title(_("Video Options"))
    dialog.set_content_width(700)
    dialog.set_content_height(450)
    dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    content.set_margin_start(24)
    content.set_margin_end(24)
    content.set_margin_top(12)
    content.set_margin_bottom(24)

    # Intro text
    intro = Gtk.Label()
    intro.set_text(
        _(
            "Choose how your video will be encoded. The defaults work well for most cases."
        )
    )
    intro.set_wrap(True)
    intro.set_xalign(0)
    intro.set_margin_bottom(20)
    intro.add_css_class("dim-label")
    content.append(intro)

    # --- Preset section ---
    preset_label = Gtk.Label(label=_("Conversion Speed"))
    preset_label.add_css_class("title-3")
    preset_label.set_halign(Gtk.Align.START)
    preset_label.set_margin_top(16)
    preset_label.set_margin_bottom(8)
    content.append(preset_label)

    preset_data = _get_preset_data()[0]
    preset_dropdown = Gtk.DropDown()
    preset_model = Gtk.StringList()
    for j in range(app.settings_page.preset_combo.get_model().get_n_items()):
        preset_model.append(app.settings_page.preset_combo.get_model().get_string(j))
    preset_dropdown.set_model(preset_model)
    preset_dropdown.set_selected(app.settings_page.preset_combo.get_selected())

    def _sync_preset_dialog(dropdown, _pspec):
        sel = dropdown.get_selected()
        if app.settings_page.preset_combo.get_selected() != sel:
            app.settings_page.preset_combo.set_selected(sel)

    def _sync_preset_sidebar(combo, _pspec):
        sel = combo.get_selected()
        if preset_dropdown.get_selected() != sel:
            preset_dropdown.set_selected(sel)

    preset_dropdown.connect("notify::selected", _sync_preset_dialog)
    app.settings_page.preset_combo.connect("notify::selected", _sync_preset_sidebar)

    card = _make_card(
        preset_data["svg"], preset_data["title"], preset_data["description"], preset_dropdown
    )
    content.append(card)

    # --- Resolution section ---
    res_label = Gtk.Label(label=_("Output Resolution"))
    res_label.add_css_class("title-3")
    res_label.set_halign(Gtk.Align.START)
    res_label.set_margin_top(16)
    res_label.set_margin_bottom(8)
    content.append(res_label)

    res_data = _get_resolution_data()[0]
    res_dropdown = Gtk.DropDown()
    res_model = Gtk.StringList()
    for j in range(app.settings_page.video_resolution_combo.get_model().get_n_items()):
        res_model.append(
            app.settings_page.video_resolution_combo.get_model().get_string(j)
        )
    res_dropdown.set_model(res_model)
    res_dropdown.set_selected(app.settings_page.video_resolution_combo.get_selected())

    def _sync_res_dialog(dropdown, _pspec):
        sel = dropdown.get_selected()
        if app.settings_page.video_resolution_combo.get_selected() != sel:
            app.settings_page.video_resolution_combo.set_selected(sel)

    def _sync_res_sidebar(combo, _pspec):
        sel = combo.get_selected()
        if res_dropdown.get_selected() != sel:
            res_dropdown.set_selected(sel)

    res_dropdown.connect("notify::selected", _sync_res_dialog)
    app.settings_page.video_resolution_combo.connect(
        "notify::selected", _sync_res_sidebar
    )

    card = _make_card(
        res_data["svg"], res_data["title"], res_data["description"], res_dropdown
    )
    content.append(card)

    # Custom resolution entry — shown when "Custom" is selected
    custom_res_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    custom_res_box.set_margin_start(16)
    custom_res_box.set_margin_end(16)
    custom_res_box.set_margin_bottom(12)

    custom_res_lbl = Gtk.Label(label=_("Custom resolution"))
    custom_res_lbl.set_xalign(0)
    custom_res_lbl.set_size_request(120, -1)
    custom_res_box.append(custom_res_lbl)

    custom_res_entry = Gtk.Entry()
    custom_res_entry.set_placeholder_text("1280x720")
    custom_res_entry.set_hexpand(True)
    # Load existing custom text from settings_page
    custom_res_entry.set_text(app.settings_page.custom_resolution_row.get_text())
    custom_res_box.append(custom_res_entry)

    custom_res_box.set_visible(
        res_dropdown.get_selected() == len(constants.VIDEO_RESOLUTION_OPTIONS) - 1
    )
    content.append(custom_res_box)

    def _on_custom_res_changed(entry):
        text = entry.get_text()
        if app.settings_page.custom_resolution_row.get_text() != text:
            app.settings_page.custom_resolution_row.set_text(text)

    custom_res_entry.connect("changed", lambda e: _on_custom_res_changed(e))

    def _on_res_selected_changed(dropdown, _pspec):
        is_custom = (
            dropdown.get_selected()
            == len(constants.VIDEO_RESOLUTION_OPTIONS) - 1
        )
        custom_res_box.set_visible(is_custom)

    res_dropdown.connect("notify::selected", _on_res_selected_changed)

    scroll.set_child(content)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    dialog.present(parent_window)
