"""
Dialog for extra / advanced settings.
FFmpeg custom flags, preview rendering and reset.
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


def _clone_model(source_combo):
    model = source_combo.get_model()
    items = Gtk.StringList()
    for i in range(model.get_n_items()):
        items.append(model.get_string(i))
    return items


def show_extra_dialog(parent_window, app) -> None:
    """Present the extra settings dialog."""
    dialog = Adw.Dialog()
    dialog.set_title(_("Extra"))
    dialog.set_content_width(700)
    dialog.set_content_height(500)
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
            "Additional options for advanced users. "
            "The defaults work well for most cases."
        )
    )
    intro.set_wrap(True)
    intro.set_xalign(0)
    intro.set_margin_bottom(20)
    intro.add_css_class("dim-label")
    content.append(intro)

    # --- FFmpeg Custom Flags ---
    ffmpeg_entry = Gtk.Entry()
    ffmpeg_entry.set_hexpand(True)
    ffmpeg_entry.set_text(app.settings_page.options_entry.get_text())
    ffmpeg_entry.set_placeholder_text(_("e.g.: -ss 60 -t 30"))

    def _on_ffmpeg_changed(entry):
        text = entry.get_text()
        if app.settings_page.options_entry.get_text() != text:
            app.settings_page.options_entry.set_text(text)

    ffmpeg_entry.connect("changed", lambda e: _on_ffmpeg_changed(e))

    # Sync back from settings_page entry
    def _on_source_changed(row):
        text = row.get_text()
        if ffmpeg_entry.get_text() != text:
            ffmpeg_entry.set_text(text)

    app.settings_page.options_entry.connect(
        "changed", lambda w: _on_source_changed(w)
    )

    # Build card manually with entry below the header
    ffmpeg_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    ffmpeg_card.add_css_class("card")
    ffmpeg_card.set_margin_bottom(12)

    ffmpeg_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    ffmpeg_header.set_margin_top(16)
    ffmpeg_header.set_margin_start(16)
    ffmpeg_header.set_margin_end(16)
    ffmpeg_header.set_margin_bottom(8)

    ffmpeg_pic = _load_svg("ffmpeg_flags.svg")
    ffmpeg_pic.set_valign(Gtk.Align.CENTER)
    ffmpeg_header.append(ffmpeg_pic)

    ffmpeg_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    ffmpeg_text.set_hexpand(True)
    ffmpeg_text.set_valign(Gtk.Align.CENTER)

    ffmpeg_lbl = Gtk.Label(label=_("Additional FFmpeg Options"))
    ffmpeg_lbl.add_css_class("heading")
    ffmpeg_lbl.set_halign(Gtk.Align.START)
    ffmpeg_lbl.set_wrap(True)
    ffmpeg_text.append(ffmpeg_lbl)

    ffmpeg_desc = Gtk.Label(
        label=_(
            "Custom flags passed directly to FFmpeg. "
            "Example: '-ss 60 -t 30' to convert only 30 seconds starting at 1 minute."
        )
    )
    ffmpeg_desc.set_wrap(True)
    ffmpeg_desc.set_xalign(0)
    ffmpeg_desc.add_css_class("dim-label")
    ffmpeg_text.append(ffmpeg_desc)

    ffmpeg_header.append(ffmpeg_text)
    ffmpeg_card.append(ffmpeg_header)

    ffmpeg_entry.set_margin_start(16)
    ffmpeg_entry.set_margin_end(16)
    ffmpeg_entry.set_margin_bottom(16)
    ffmpeg_card.append(ffmpeg_entry)

    content.append(ffmpeg_card)

    # --- Video Preview Rendering ---
    render_dd = Gtk.DropDown(model=_clone_model(app.settings_page.render_mode_combo))
    render_dd.set_selected(app.settings_page.render_mode_combo.get_selected())
    _make_combo_sync(app.settings_page.render_mode_combo, render_dd)
    content.append(
        _make_card(
            "render_mode.svg",
            _("Video Preview Rendering"),
            _(
                "How the video editor preview is rendered. "
                "'Automatic' works best for most systems. "
                "Try 'Software' if you see display issues. Requires restart."
            ),
            render_dd,
        )
    )

    # --- Reset All Settings ---
    reset_btn = Gtk.Button(label=_("Reset All Settings"))
    reset_btn.add_css_class("destructive-action")
    reset_btn.add_css_class("pill")
    reset_btn.set_valign(Gtk.Align.CENTER)

    def _on_reset(_btn):
        app.settings_page._on_reset_button_clicked(_btn)

    reset_btn.connect("clicked", _on_reset)

    content.append(
        _make_card(
            "reset_settings.svg",
            _("Reset All Settings"),
            _(
                "Restore all options to their default values. "
                "This cannot be undone."
            ),
            reset_btn,
        )
    )

    scroll.set_child(content)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    dialog.present(parent_window)
