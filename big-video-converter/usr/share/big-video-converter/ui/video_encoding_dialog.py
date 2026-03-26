"""
Educational dialog for video encoding settings.
Shows codec, quality, GPU, speed, resolution and output format
with SVG illustrations and explanations.
"""

import gettext
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

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


def _make_switch_sync(source, toggle):
    """Bidirectional sync between a SwitchRow (data holder) and Switch (dialog)."""

    def _on_toggle(sw, _p):
        if source.get_active() != sw.get_active():
            source.set_active(sw.get_active())

    def _on_source(row, _p):
        if toggle.get_active() != row.get_active():
            toggle.set_active(row.get_active())

    toggle.connect("notify::active", _on_toggle)
    source.connect("notify::active", _on_source)


def _clone_model(source_combo):
    """Clone a StringList model from a ComboRow."""
    model = source_combo.get_model()
    items = Gtk.StringList()
    for i in range(model.get_n_items()):
        items.append(model.get_string(i))
    return items


def show_video_encoding_dialog(parent_window, app) -> None:
    """Present the educational video encoding dialog."""
    dialog = Adw.Dialog()
    dialog.set_title(_("Video Encoding"))
    dialog.set_content_width(800)
    dialog.set_content_height(750)
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
            "Configure how your video will be encoded. "
            "The defaults work well for most cases."
        )
    )
    intro.set_wrap(True)
    intro.set_xalign(0)
    intro.set_margin_bottom(20)
    intro.add_css_class("dim-label")
    content.append(intro)

    # --- Output Format ---
    fmt_dd = Gtk.DropDown(model=_clone_model(app.output_format_combo))
    fmt_dd.set_selected(app.output_format_combo.get_selected())
    _make_combo_sync(app.output_format_combo, fmt_dd)
    content.append(
        _make_card(
            "output_format.svg",
            _("Output Format"),
            _(
                "The container that holds audio and video. "
                "MP4 is the most compatible. MKV supports all codecs."
            ),
            fmt_dd,
        )
    )

    # --- Video Codec ---
    codec_dd = Gtk.DropDown(model=_clone_model(app.video_codec_combo))
    codec_dd.set_selected(app.video_codec_combo.get_selected())
    _make_combo_sync(app.video_codec_combo, codec_dd)
    content.append(
        _make_card(
            "codec_h264.svg",
            _("Video Codec"),
            _(
                "Choose the compression format. H.264 is the most compatible. "
                "H.265 and AV1 produce smaller files but need more processing power."
            ),
            codec_dd,
        )
    )

    # --- Image Quality ---
    quality_dd = Gtk.DropDown(model=_clone_model(app.video_quality_combo))
    quality_dd.set_selected(app.video_quality_combo.get_selected())
    _make_combo_sync(app.video_quality_combo, quality_dd)
    content.append(
        _make_card(
            "image_quality.svg",
            _("Image Quality"),
            _(
                "Higher quality keeps more detail but creates larger files. "
                "'Good' is a balanced choice for most videos."
            ),
            quality_dd,
        )
    )

    # --- Output Resolution ---
    res_dd = Gtk.DropDown(model=_clone_model(app.settings_page.video_resolution_combo))
    res_dd.set_selected(app.settings_page.video_resolution_combo.get_selected())
    _make_combo_sync(app.settings_page.video_resolution_combo, res_dd)
    content.append(
        _make_card(
            "resolution.svg",
            _("Output Resolution"),
            _(
                "Increasing beyond the original resolution does not improve quality. "
                "Reducing the resolution creates smaller files."
            ),
            res_dd,
        )
    )

    # Custom resolution entry
    custom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    custom_box.set_margin_start(16)
    custom_box.set_margin_end(16)
    custom_box.set_margin_bottom(12)

    custom_lbl = Gtk.Label(label=_("Custom resolution"))
    custom_lbl.set_xalign(0)
    custom_lbl.set_size_request(120, -1)
    custom_box.append(custom_lbl)

    custom_entry = Gtk.Entry()
    custom_entry.set_placeholder_text("1280x720")
    custom_entry.set_hexpand(True)
    custom_entry.set_text(app.settings_page.custom_resolution_row.get_text())
    custom_box.append(custom_entry)

    is_custom_res = (
        res_dd.get_selected() == len(constants.VIDEO_RESOLUTION_OPTIONS) - 1
    )
    custom_box.set_visible(is_custom_res)
    content.append(custom_box)

    def _on_custom_entry(entry):
        text = entry.get_text()
        if app.settings_page.custom_resolution_row.get_text() != text:
            app.settings_page.custom_resolution_row.set_text(text)

    custom_entry.connect("changed", lambda e: _on_custom_entry(e))

    def _on_res_changed(dd, _p):
        custom_box.set_visible(
            dd.get_selected() == len(constants.VIDEO_RESOLUTION_OPTIONS) - 1
        )

    res_dd.connect("notify::selected", _on_res_changed)

    # --- Conversion Speed (Preset) ---
    preset_dd = Gtk.DropDown(model=_clone_model(app.settings_page.preset_combo))
    preset_dd.set_selected(app.settings_page.preset_combo.get_selected())
    _make_combo_sync(app.settings_page.preset_combo, preset_dd)
    content.append(
        _make_card(
            "preset_speed.svg",
            _("Conversion Speed"),
            _(
                "Slower conversion produces smaller files. "
                "Faster presets create larger files but finish sooner."
            ),
            preset_dd,
        )
    )

    # --- GPU Acceleration ---
    gpu_dd = Gtk.DropDown(model=_clone_model(app.gpu_combo))
    gpu_dd.set_selected(app.gpu_combo.get_selected())
    _make_combo_sync(app.gpu_combo, gpu_dd)
    content.append(
        _make_card(
            "gpu_accel.svg",
            _("GPU Acceleration"),
            _(
                "Use your graphics card to speed up encoding. "
                "'Auto' detects the best option. Disable if you experience issues."
            ),
            gpu_dd,
        )
    )

    # --- GPU Device (only when multiple GPUs) ---
    if len(app.detected_gpus) > 1:
        gpu_dev_dd = Gtk.DropDown(model=_clone_model(app.gpu_device_combo))
        gpu_dev_dd.set_selected(app.gpu_device_combo.get_selected())
        _make_combo_sync(app.gpu_device_combo, gpu_dev_dd)
        content.append(
            _make_card(
                "gpu_accel.svg",
                _("GPU Device"),
                _(
                    "Select which graphics card to use for encoding "
                    "when multiple GPUs are available."
                ),
                gpu_dev_dd,
            )
        )

    # --- Software Decode ---
    sw_decode_toggle = Gtk.Switch()
    sw_decode_toggle.set_active(app.settings_page.gpu_partial_check.get_active())
    _make_switch_sync(app.settings_page.gpu_partial_check, sw_decode_toggle)
    content.append(
        _make_card(
            "sw_decode.svg",
            _("Decode Using Software"),
            _(
                "Decode on CPU and only encode on GPU. "
                "Enable this if GPU decoding causes errors or artifacts."
            ),
            sw_decode_toggle,
        )
    )

    scroll.set_child(content)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    dialog.present(parent_window)
