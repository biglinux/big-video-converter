"""
Educational dialog for subtitle settings.
Shows subtitle handling options with explanations.
"""

import gettext
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

_ = gettext.gettext

_ILLUSTRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "icons",
    "illustrations",
)


def _load_svg(filename: str, size: int = 92) -> Gtk.Picture:
    path = os.path.join(_ILLUSTRATIONS_DIR, filename)
    pic = Gtk.Picture.new_for_filename(path)
    pic.set_size_request(size, int(size * 80 / 120))
    pic.set_can_shrink(True)
    pic.set_content_fit(Gtk.ContentFit.CONTAIN)
    return pic


def _make_card(
    svg: str, title: str, description: str, control: Gtk.Widget
) -> Gtk.Box:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    card.add_css_class("card")
    card.set_margin_bottom(12)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    row.set_margin_top(16)
    row.set_margin_bottom(16)
    row.set_margin_start(16)
    row.set_margin_end(16)

    picture = _load_svg(svg)
    picture.set_valign(Gtk.Align.CENTER)
    row.append(picture)

    text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    text_box.set_hexpand(True)
    text_box.set_valign(Gtk.Align.CENTER)

    lbl = Gtk.Label(label=title)
    lbl.add_css_class("heading")
    lbl.set_halign(Gtk.Align.START)
    lbl.set_wrap(True)
    text_box.append(lbl)

    desc = Gtk.Label(label=description)
    desc.set_wrap(True)
    desc.set_xalign(0)
    desc.add_css_class("dim-label")
    text_box.append(desc)

    row.append(text_box)

    control.set_valign(Gtk.Align.CENTER)
    row.append(control)

    card.append(row)
    return card


def _make_combo_sync(source, dropdown):
    def _on_dropdown(dd, _p):
        if source.get_selected() != dd.get_selected():
            source.set_selected(dd.get_selected())

    def _on_source(row, _p):
        if dropdown.get_selected() != row.get_selected():
            dropdown.set_selected(row.get_selected())

    dropdown.connect("notify::selected", _on_dropdown)
    source.connect("notify::selected", _on_source)


def _make_switch_sync(source, toggle):
    def _on_toggle(sw, _p):
        if source.get_active() != sw.get_active():
            source.set_active(sw.get_active())

    def _on_source(row, _p):
        if toggle.get_active() != row.get_active():
            toggle.set_active(row.get_active())

    toggle.connect("notify::active", _on_toggle)
    source.connect("notify::active", _on_source)


def _clone_model(source_combo):
    model = source_combo.get_model()
    items = Gtk.StringList()
    for i in range(model.get_n_items()):
        items.append(model.get_string(i))
    return items


def show_subtitles_dialog(parent_window, app) -> None:
    """Present the educational subtitles dialog."""
    dialog = Adw.Dialog()
    dialog.set_title(_("Subtitles"))
    dialog.set_content_width(700)
    dialog.set_content_height(400)
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

    intro = Gtk.Label()
    intro.set_text(
        _(
            "Configure how subtitles are handled during conversion."
        )
    )
    intro.set_wrap(True)
    intro.set_xalign(0)
    intro.set_margin_bottom(20)
    intro.add_css_class("dim-label")
    content.append(intro)

    # --- Subtitle Handling ---
    sub_dd = Gtk.DropDown(model=_clone_model(app.subtitle_combo))
    sub_dd.set_selected(app.subtitle_combo.get_selected())
    _make_combo_sync(app.subtitle_combo, sub_dd)
    content.append(
        _make_card(
            "subtitle_keep.svg",
            _("Subtitle Handling"),
            _(
                "Choose how subtitles are included in the output. "
                "'Embedded' keeps them inside the video file. "
                "'Extract to .srt' creates separate subtitle files."
            ),
            sub_dd,
        )
    )

    # --- Extract Subtitles Only ---
    extract_toggle = Gtk.Switch()
    extract_toggle.set_active(
        app.settings_page.only_extract_subtitles_check.get_active()
    )
    _make_switch_sync(app.settings_page.only_extract_subtitles_check, extract_toggle)
    content.append(
        _make_card(
            "subtitle_extract.svg",
            _("Only Extract Subtitles"),
            _(
                "Skip video conversion entirely and only extract "
                "the subtitle tracks to .srt files."
            ),
            extract_toggle,
        )
    )

    scroll.set_child(content)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    dialog.present(parent_window)
