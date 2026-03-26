"""
Educational dialog for audio settings.
Shows audio handling, codec, bitrate and channels with explanations.
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
    """Bidirectional sync between a ComboRow (data holder) and DropDown (dialog)."""

    def _on_dropdown(dd, _p):
        if source.get_selected() != dd.get_selected():
            source.set_selected(dd.get_selected())

    def _on_source(row, _p):
        if dropdown.get_selected() != row.get_selected():
            dropdown.set_selected(row.get_selected())

    dropdown.connect("notify::selected", _on_dropdown)
    source.connect("notify::selected", _on_source)


def _clone_model(source_combo):
    """Clone a StringList model from a ComboRow."""
    model = source_combo.get_model()
    items = Gtk.StringList()
    for i in range(model.get_n_items()):
        items.append(model.get_string(i))
    return items


def show_audio_dialog(parent_window, app) -> None:
    """Present the educational audio settings dialog."""
    dialog = Adw.Dialog()
    dialog.set_title(_("Audio"))
    dialog.set_content_width(800)
    dialog.set_content_height(600)
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
            "Configure how the audio track will be handled. "
            "Copying keeps the original quality. Re-encoding allows changes."
        )
    )
    intro.set_wrap(True)
    intro.set_xalign(0)
    intro.set_margin_bottom(20)
    intro.add_css_class("dim-label")
    content.append(intro)

    # --- Audio Handling ---
    audio_dd = Gtk.DropDown(model=_clone_model(app.audio_handling_combo))
    audio_dd.set_selected(app.audio_handling_combo.get_selected())
    _make_combo_sync(app.audio_handling_combo, audio_dd)
    content.append(
        _make_card(
            "audio_handling.svg",
            _("Audio Handling"),
            _(
                "Choose what to do with the audio track. "
                "'Copy' keeps the original. 'Re-encode' allows changing "
                "codec, bitrate and applying audio cleaning."
            ),
            audio_dd,
        )
    )

    # --- Audio Codec ---
    codec_dd = Gtk.DropDown(model=_clone_model(app.settings_page.audio_codec_combo))
    codec_dd.set_selected(app.settings_page.audio_codec_combo.get_selected())
    _make_combo_sync(app.settings_page.audio_codec_combo, codec_dd)
    content.append(
        _make_card(
            "audio_codec.svg",
            _("Audio Codec"),
            _(
                "The format used to compress audio. "
                "AAC is widely compatible. Opus offers better quality at lower bitrates. "
                "Choose 'Copy' in audio handling to keep the original codec."
            ),
            codec_dd,
        )
    )

    # --- Bitrate ---
    bitrate_dd = Gtk.DropDown(model=_clone_model(app.settings_page.audio_bitrate_combo))
    bitrate_dd.set_selected(app.settings_page.audio_bitrate_combo.get_selected())
    _make_combo_sync(app.settings_page.audio_bitrate_combo, bitrate_dd)

    bitrate_card = _make_card(
        "audio_bitrate.svg",
        _("Audio Bitrate"),
        _(
            "Higher bitrate means better audio quality but larger files. "
            "192k is a good balance for most content."
        ),
        bitrate_dd,
    )
    content.append(bitrate_card)

    # Custom bitrate entry
    custom_bitrate_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    custom_bitrate_box.set_margin_start(16)
    custom_bitrate_box.set_margin_end(16)
    custom_bitrate_box.set_margin_bottom(12)

    custom_bitrate_lbl = Gtk.Label(label=_("Custom bitrate"))
    custom_bitrate_lbl.set_xalign(0)
    custom_bitrate_lbl.set_size_request(120, -1)
    custom_bitrate_box.append(custom_bitrate_lbl)

    custom_bitrate_entry = Gtk.Entry()
    custom_bitrate_entry.set_placeholder_text("192k")
    custom_bitrate_entry.set_hexpand(True)
    custom_bitrate_entry.set_text(app.settings_page.custom_bitrate_row.get_text())
    custom_bitrate_box.append(custom_bitrate_entry)

    is_custom_bitrate = bitrate_dd.get_selected() == len(
        app.settings_page.bitrate_values
    ) - 1
    custom_bitrate_box.set_visible(is_custom_bitrate)
    content.append(custom_bitrate_box)

    def _on_custom_bitrate(entry):
        text = entry.get_text()
        if app.settings_page.custom_bitrate_row.get_text() != text:
            app.settings_page.custom_bitrate_row.set_text(text)

    custom_bitrate_entry.connect("changed", lambda e: _on_custom_bitrate(e))

    def _on_bitrate_changed(dd, _p):
        custom_bitrate_box.set_visible(
            dd.get_selected() == len(app.settings_page.bitrate_values) - 1
        )

    bitrate_dd.connect("notify::selected", _on_bitrate_changed)

    # --- Channels ---
    channels_dd = Gtk.DropDown(
        model=_clone_model(app.settings_page.audio_channels_combo)
    )
    channels_dd.set_selected(app.settings_page.audio_channels_combo.get_selected())
    _make_combo_sync(app.settings_page.audio_channels_combo, channels_dd)

    channels_card = _make_card(
        "audio_channels.svg",
        _("Audio Channels"),
        _(
            "'Default' keeps the original channel count from the source video. "
            "Stereo (2) is standard. Mono (1) for voice only. 5.1 (6) for surround sound."
        ),
        channels_dd,
    )
    content.append(channels_card)

    # Custom channels entry
    custom_ch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    custom_ch_box.set_margin_start(16)
    custom_ch_box.set_margin_end(16)
    custom_ch_box.set_margin_bottom(12)

    custom_ch_lbl = Gtk.Label(label=_("Custom channels"))
    custom_ch_lbl.set_xalign(0)
    custom_ch_lbl.set_size_request(120, -1)
    custom_ch_box.append(custom_ch_lbl)

    custom_ch_entry = Gtk.Entry()
    custom_ch_entry.set_placeholder_text("2")
    custom_ch_entry.set_hexpand(True)
    custom_ch_entry.set_text(app.settings_page.custom_channels_row.get_text())
    custom_ch_box.append(custom_ch_entry)

    is_custom_ch = channels_dd.get_selected() == len(
        app.settings_page.channels_values
    ) - 1
    custom_ch_box.set_visible(is_custom_ch)
    content.append(custom_ch_box)

    def _on_custom_ch(entry):
        text = entry.get_text()
        if app.settings_page.custom_channels_row.get_text() != text:
            app.settings_page.custom_channels_row.set_text(text)

    custom_ch_entry.connect("changed", lambda e: _on_custom_ch(e))

    def _on_ch_changed(dd, _p):
        custom_ch_box.set_visible(
            dd.get_selected() == len(app.settings_page.channels_values) - 1
        )

    channels_dd.connect("notify::selected", _on_ch_changed)

    scroll.set_child(content)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    dialog.present(parent_window)
