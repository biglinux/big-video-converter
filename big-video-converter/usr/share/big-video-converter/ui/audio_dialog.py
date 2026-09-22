"""Clear, causal audio settings dialog.

The dialog mirrors the hidden data-holder widgets used by the conversion
backend.  Controls that do not apply to the selected handling mode remain
visible for orientation, but become unavailable without erasing their saved
values.
"""

from __future__ import annotations

import gettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from utils.audio_ui_state import audio_mode_presentation
from utils.signal_connections import SignalConnections

_ = gettext.gettext


def _clone_model(source_combo) -> Gtk.StringList:
    """Copy a StringList model without reparenting the source ComboRow."""

    source = source_combo.get_model()
    result = Gtk.StringList()
    for index in range(source.get_n_items()):
        result.append(source.get_string(index))
    return result


def _make_combo_sync(source, target, connections: SignalConnections) -> None:
    """Keep a dialog ComboRow and the application's data holder synchronized."""

    def target_changed(row, _pspec):
        selected = row.get_selected()
        if source.get_selected() != selected:
            source.set_selected(selected)

    def source_changed(row, _pspec):
        selected = row.get_selected()
        if target.get_selected() != selected:
            target.set_selected(selected)

    target.connect("notify::selected", target_changed)
    connections.connect(source, "notify::selected", source_changed)


def _make_entry_sync(source, target, connections: SignalConnections) -> None:
    """Keep a dialog EntryRow and its existing application field synchronized."""

    def target_changed(row):
        value = row.get_text()
        if source.get_text() != value:
            source.set_text(value)

    def source_changed(row):
        value = row.get_text()
        if target.get_text() != value:
            target.set_text(value)

    target.connect("changed", target_changed)
    connections.connect(source, "changed", source_changed)


def _presentation_text(state):
    if state.result_title_key == "convert-audio":
        return (
            _("Convert the audio"),
            _(
                "The codec, quality and channel choices below will be applied "
                "to the final video."
            ),
            "audio-card-symbolic",
        )
    if state.result_title_key == "remove-audio":
        return (
            _("Final video without audio"),
            _(
                "The video track will be converted normally, but every audio "
                "track will be removed."
            ),
            "audio-volume-muted-symbolic",
        )
    return (
        _("Keep the original audio"),
        _(
            "Audio will be copied without quality loss or additional audio "
            "encoding time."
        ),
        "audio-volume-high-symbolic",
    )


def show_audio_dialog(parent_window, app) -> None:
    """Present audio choices grouped by intent and actual applicability."""

    dialog = Adw.Dialog()
    connections = SignalConnections(dialog)
    dialog.set_title(_("Audio"))
    dialog.set_content_width(680)
    dialog.set_content_height(580)
    dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    clamp = Adw.Clamp()
    clamp.set_maximum_size(680)
    content = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_top=18,
        margin_bottom=24,
        margin_start=18,
        margin_end=18,
    )
    clamp.set_child(content)

    intro = Gtk.Label(
        label=_(
            "Choose the result you want. Technical audio choices appear in "
            "the second section only when they can affect the conversion."
        ),
        wrap=True,
        xalign=0,
    )
    intro.add_css_class("dim-label")
    content.append(intro)

    result_group = Adw.PreferencesGroup()
    result_group.set_title(_("Audio result"))
    result_group.set_description(
        _("Choose whether the source audio is preserved, converted or removed.")
    )

    audio_row = Adw.ComboRow(
        title=_("How to handle the audio"),
        subtitle=_("This choice controls which settings are applicable"),
        model=_clone_model(app.audio_handling_combo),
    )
    audio_row.set_selected(app.audio_handling_combo.get_selected())
    _make_combo_sync(app.audio_handling_combo, audio_row, connections)
    result_group.add(audio_row)

    result_row = Adw.ActionRow()
    result_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
    result_icon.set_pixel_size(20)
    result_row.add_prefix(result_icon)
    result_group.add(result_row)
    content.append(result_group)

    details_group = Adw.PreferencesGroup()
    details_group.set_title(_("Re-encoding details"))
    details_group.set_description(
        _(
            "These saved choices are used only when “Re-encode audio” is "
            "selected. Switching modes does not erase them."
        )
    )

    codec_row = Adw.ComboRow(
        title=_("Codec"),
        subtitle=_("AAC is broadly compatible; Opus is efficient at lower bitrates"),
        model=_clone_model(app.settings_page.audio_codec_combo),
    )
    codec_row.set_selected(app.settings_page.audio_codec_combo.get_selected())
    _make_combo_sync(app.settings_page.audio_codec_combo, codec_row, connections)
    details_group.add(codec_row)

    bitrate_row = Adw.ComboRow(
        title=_("Audio quality"),
        subtitle=_("Higher bitrates improve quality and increase file size"),
        model=_clone_model(app.settings_page.audio_bitrate_combo),
    )
    bitrate_row.set_selected(app.settings_page.audio_bitrate_combo.get_selected())
    _make_combo_sync(app.settings_page.audio_bitrate_combo, bitrate_row, connections)
    details_group.add(bitrate_row)

    custom_bitrate_row = Adw.EntryRow(title=_("Custom bitrate"))
    custom_bitrate_row.set_text(app.settings_page.custom_bitrate_row.get_text())
    custom_bitrate_row.set_input_purpose(Gtk.InputPurpose.NUMBER)
    custom_bitrate_row.set_show_apply_button(False)
    _make_entry_sync(
        app.settings_page.custom_bitrate_row,
        custom_bitrate_row,
        connections,
    )
    details_group.add(custom_bitrate_row)

    channels_row = Adw.ComboRow(
        title=_("Channels"),
        subtitle=_("Keep the source layout unless the destination requires another one"),
        model=_clone_model(app.settings_page.audio_channels_combo),
    )
    channels_row.set_selected(app.settings_page.audio_channels_combo.get_selected())
    _make_combo_sync(app.settings_page.audio_channels_combo, channels_row, connections)
    details_group.add(channels_row)

    custom_channels_row = Adw.EntryRow(title=_("Custom channel count"))
    custom_channels_row.set_text(app.settings_page.custom_channels_row.get_text())
    custom_channels_row.set_input_purpose(Gtk.InputPurpose.NUMBER)
    custom_channels_row.set_show_apply_button(False)
    _make_entry_sync(
        app.settings_page.custom_channels_row,
        custom_channels_row,
        connections,
    )
    details_group.add(custom_channels_row)

    content.append(details_group)

    availability_row = Adw.ActionRow(
        title=_("Audio cleaning"),
        subtitle=_(
            "Noise removal, equalization and normalization become available "
            "when audio is re-encoded."
        ),
    )
    availability_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
    availability_icon.set_pixel_size(18)
    availability_row.add_prefix(availability_icon)
    availability_group = Adw.PreferencesGroup()
    availability_group.add(availability_row)
    content.append(availability_group)

    def update_custom_rows(*_args):
        custom_bitrate = bitrate_row.get_selected() == len(
            app.settings_page.bitrate_values
        ) - 1
        custom_channels = channels_row.get_selected() == len(
            app.settings_page.channels_values
        ) - 1
        custom_bitrate_row.set_visible(custom_bitrate)
        custom_channels_row.set_visible(custom_channels)

    def update_mode(*_args):
        state = audio_mode_presentation(audio_row.get_selected())
        title, description, icon_name = _presentation_text(state)
        result_row.set_title(title)
        result_row.set_subtitle(description)
        result_icon.set_from_icon_name(icon_name)
        details_group.set_sensitive(state.details_sensitive)
        availability_row.set_sensitive(state.details_sensitive)
        if state.details_sensitive:
            availability_row.set_subtitle(
                _(
                    "Noise removal, equalization and normalization can be "
                    "configured in Audio cleaning."
                )
            )
        else:
            availability_row.set_subtitle(
                _(
                    "Select “Re-encode audio” to apply cleaning. Your saved "
                    "cleaning preferences are preserved."
                )
            )
        update_custom_rows()

    audio_row.connect("notify::selected", update_mode)
    bitrate_row.connect("notify::selected", update_custom_rows)
    channels_row.connect("notify::selected", update_custom_rows)
    update_mode()

    scroll.set_child(clamp)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    dialog.present(parent_window)
